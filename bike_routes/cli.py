"""Command-line entry point for the pipeline."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from . import config
from .cache import _load_route_cache, _load_state, _save_route_cache, _save_state
from .export import _export_geojson
from .gps import _load_and_resample
from .graph import _load_graph
from .hmm import _build_matcher_context, _match_one
from .matching import _match_rides_parallel, _match_worker_count
from .render import _build_render_cache, _get_render_data, _render
from .ride_stats import _backfill_ride_stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line overrides for the module-level config."""
    parser = argparse.ArgumentParser(
        description="Process bike ride GPS logs into an interactive frequency map."
    )
    parser.add_argument(
        "--sample", type=int, metavar="N", help="process only the first N ride files"
    )
    parser.add_argument(
        "--rides", nargs="+", metavar="FILE", help="process only these ride CSV filenames"
    )
    parser.add_argument("--no-png", action="store_true", help="skip rendering the static PNG maps")
    parser.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help="worker processes for map matching (1 = sequential)",
    )
    return parser.parse_args(argv)
def main(argv: list[str] | None = None) -> None:
    """Run the full bike route pipeline."""
    t0 = time.time()

    args = _parse_args(argv)
    sample_size = args.sample if args.sample is not None else config.SAMPLE_SIZE
    ride_files = args.rides if args.rides is not None else config.RIDE_FILES
    skip_png = config.SKIP_PNG_RENDER or args.no_png
    if args.workers is not None:
        # _match_worker_count reads this; the env var also reaches workers
        os.environ["MATCH_WORKERS"] = str(args.workers)

    # 1. Load state
    state = _load_state()

    # 2. Find new rides (exclude already-processed and already-skipped files)
    all_files = sorted(f.name for f in Path(config.RIDES_FOLDER).iterdir() if f.suffix == ".csv")
    if ride_files is not None:
        ride_set = set(ride_files)
        all_files = [f for f in all_files if f in ride_set]
    elif sample_size is not None:
        all_files = all_files[:sample_size]
    known = state["processed_files"] | state.get("skipped_files", set())
    new_files = [f for f in all_files if f not in known]

    n_total = len(all_files)
    n_processed = len(state["processed_files"])
    n_skipped = len(state.get("skipped_files", set()))
    n_new = len(new_files)

    print(f"Rides: {n_total} total, {n_processed} NYC, {n_skipped} non-NYC, {n_new} new")

    if n_new <= 10 and n_new > 0:
        for f in new_files:
            print(f"  + {f}")

    # Backfill time/distance stats for rides processed before stats existed
    n_backfilled = _backfill_ride_stats(state)
    if n_backfilled:
        print(f"Backfilled ride stats for {n_backfilled} ride(s)")
        _save_state(state)

    # 3. No new rides -- re-render from cache
    if n_new == 0:
        if not state["edge_counts"]:
            print("No rides found")
            return

        render_data = _get_render_data()
        if render_data is None:
            print("Render cache missing -- loading graph to rebuild...")
            G = _load_graph([], state)
            render_data = _build_render_cache(G)
        edge_geom, edge_hw = render_data
        _render(edge_geom, state, skip_png=skip_png)
        _export_geojson(edge_geom, state, edge_hw)

        print(f"\nDone in {time.time() - t0:.1f}s (no new rides)")
        return

    # 4. Load, filter to NYC, and resample new rides
    new_rides, n_non_nyc = _load_and_resample(new_files)
    if n_non_nyc:
        # Track non-NYC files so they aren't re-checked next run
        skipped_files = state.get("skipped_files", set())
        nyc_fnames = {f for f, _ in new_rides}
        skipped_files |= {f for f in new_files if f not in nyc_fnames}
        state["skipped_files"] = skipped_files
        print(f"  Filtered out {n_non_nyc} non-NYC ride(s)")

    if not new_rides:
        print("No new NYC rides to process")
        _save_state(state)
        if state["edge_counts"]:
            render_data = _get_render_data()
            if render_data is None:
                print("Render cache missing -- loading graph to rebuild...")
                G = _load_graph([], state)
                render_data = _build_render_cache(G)
            edge_geom, edge_hw = render_data
            _render(edge_geom, state, skip_png=skip_png)
            _export_geojson(edge_geom, state, edge_hw)
        print(f"\nDone in {time.time() - t0:.1f}s")
        return

    total_pts = sum(len(c) for _, c in new_rides)
    print(f"Resampled {len(new_rides)} NYC rides ({total_pts:,} points)")

    # 5. Load or fetch graph
    G = _load_graph(new_rides, state)

    # 6. Build the matcher context (HMM map index or heuristic snap tree).
    # use_cache also refreshes the on-disk map index for worker processes.
    ctx = _build_matcher_context(G, use_cache=True)

    # 7. Map-match new rides
    print(f"Map-matching ({config.MATCHER})...")
    route_cache = _load_route_cache()

    results: list[tuple[str, list[tuple[int, int]], int]] | None = None
    n_workers = _match_worker_count(len(new_rides))
    if n_workers > 1:
        print(f"  Matching on {n_workers} worker processes")
        try:
            results = _match_rides_parallel(new_rides, route_cache, n_workers)
        except Exception as e:
            print(f"  Parallel matching failed ({e!r}) -- falling back to sequential")
            results = None

    if results is None:
        results = []
        for i, (fname, coords) in enumerate(new_rides, 1):
            edges, skipped = _match_one(G, ctx, coords, route_cache)
            results.append((fname, edges, skipped))
            if i % 20 == 0 or i == len(new_rides):
                print(f"  {i}/{len(new_rides)} rides")

    # Apply results in filename order so state is identical regardless of
    # how matching was scheduled.
    total_skipped = 0
    for fname, edges, skipped in sorted(results):
        total_skipped += skipped
        edge_rides = state.setdefault("edge_rides", {})
        for edge in set(edges):
            state["edge_counts"][edge] = state["edge_counts"].get(edge, 0) + 1
            edge_rides.setdefault(edge, []).append(fname)
        state["processed_files"].add(fname)

    if total_skipped:
        print(f"  Skipped {total_skipped:,} segments > {config.MAX_ROUTING_DISTANCE_M}m")

    _save_route_cache(route_cache)
    print(f"  Route cache: {len(route_cache):,} entries saved")

    # 8. Compute stats for the newly processed rides, save state
    _backfill_ride_stats(state)
    _save_state(state)

    # 9. Render
    render_data = _get_render_data(G)
    assert render_data is not None
    edge_geom, edge_hw = render_data
    _render(edge_geom, state, skip_png=skip_png)
    _export_geojson(edge_geom, state, edge_hw)

    # Summary
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  {len(state['processed_files'])} NYC rides")
    print(f"  {len(state.get('skipped_files', set()))} non-NYC rides skipped")
    print(f"  {len(state['edge_counts']):,} unique edges")
