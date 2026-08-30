"""Command-line entry point for the pipeline."""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from . import config
from .cache import (
    _load_route_cache,
    _load_state,
    _migrate_legacy_caches,
    _save_route_cache,
    _save_state,
)
from .edge_speed import _backfill_edge_speeds
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


def _apply_results(
    state: dict[str, Any],
    batch: list[tuple[str, list[tuple[int, int]], int]],
) -> int:
    """Fold matched edges into state; returns the count of skipped segments.

    Application order is free: edge_counts accumulates by addition, and
    edge_rides is only ever read via min() and set() in export.py, so folding
    results chunk by chunk is equivalent to one globally sorted pass.
    """
    edge_rides = state.setdefault("edge_rides", {})
    total_skipped = 0
    for fname, edges, skipped in sorted(batch):
        total_skipped += skipped
        for edge in set(edges):
            state["edge_counts"][edge] = state["edge_counts"].get(edge, 0) + 1
            edge_rides.setdefault(edge, []).append(fname)
        state["processed_files"].add(fname)
    return total_skipped


def _ready_results(
    pending: dict[str, list[tuple[str, list[tuple[int, int]], int]]],
    seg_total: Counter[str],
    batch: list[tuple[str, list[tuple[int, int]], int]],
) -> list[tuple[str, list[tuple[int, int]], int]]:
    """Buffer results, releasing only files whose every segment has landed.

    A file split at GPS gaps (gps.py) yields several entries in new_rides but
    is a single entry in processed_files.  Releasing one early would let a
    checkpoint mark the file done while its other segments are still
    outstanding, silently dropping them when the run resumes.
    """
    ready: list[tuple[str, list[tuple[int, int]], int]] = []
    for result in batch:
        fname = result[0]
        pending.setdefault(fname, []).append(result)
        if len(pending[fname]) == seg_total[fname]:
            ready.extend(pending.pop(fname))
    return ready


def _finalize(
    state: dict[str, Any],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_hw: dict[tuple[int, int], str],
    edge_name: dict[tuple[int, int], str],
    *,
    skip_png: bool,
) -> None:
    """Backfill edge speeds, then render and export.

    Speed measurement needs edge_geom, which only exists once the render
    cache is loaded -- so unlike _backfill_ride_stats it cannot live in the
    matching checkpoint.  All three export paths funnel through here.
    """
    n_speed = _backfill_edge_speeds(state, edge_geom)
    if n_speed:
        print(f"Measured edge speeds for {n_speed:,} ride(s)")
        _save_state(state)
    _render(edge_geom, state, skip_png=skip_png)
    _export_geojson(edge_geom, state, edge_hw, edge_name)


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

    # 1. Load state (relocating any caches left over from the flat layout)
    _migrate_legacy_caches()
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
        edge_geom, edge_hw, edge_name = render_data
        _finalize(state, edge_geom, edge_hw, edge_name, skip_png=skip_png)

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
            edge_geom, edge_hw, edge_name = render_data
            _finalize(state, edge_geom, edge_hw, edge_name, skip_png=skip_png)
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

    # Results are folded into state as they arrive and written out every
    # config.CHECKPOINT_EVERY_RIDES rides, so an interrupted run resumes from
    # the last checkpoint instead of rematching everything.
    # Files split at GPS gaps are held back until complete (_ready_results).
    seg_total = Counter(f for f, _ in new_rides)
    pending: dict[str, list[tuple[str, list[tuple[int, int]], int]]] = {}
    total_skipped = 0
    n_since_save = 0

    def _checkpoint() -> None:
        nonlocal n_since_save
        _save_route_cache(route_cache)
        _backfill_ride_stats(state)
        _save_state(state)
        n_since_save = 0
        print(f"  Checkpoint: {len(state['processed_files']):,} rides saved")

    def _fold(batch: list[tuple[str, list[tuple[int, int]], int]]) -> None:
        nonlocal total_skipped, n_since_save
        ready = _ready_results(pending, seg_total, batch)
        if not ready:
            return
        total_skipped += _apply_results(state, ready)
        n_since_save += len(ready)
        if n_since_save >= config.CHECKPOINT_EVERY_RIDES:
            _checkpoint()

    n_workers = _match_worker_count(len(new_rides))
    matched = False
    if n_workers > 1:
        print(f"  Matching on {n_workers} worker processes")
        try:
            _match_rides_parallel(new_rides, route_cache, n_workers, on_chunk=_fold)
            matched = True
        except Exception as e:
            print(f"  Parallel matching failed ({e!r}) -- falling back to sequential")

    if not matched:
        # Whatever the parallel attempt folded is already in processed_files;
        # drop its half-finished files and rematch only what is still missing.
        pending.clear()
        remaining = [(f, c) for f, c in new_rides if f not in state["processed_files"]]
        for i, (fname, coords) in enumerate(remaining, 1):
            edges, skipped = _match_one(G, ctx, coords, route_cache)
            _fold([(fname, edges, skipped)])
            if i % 20 == 0 or i == len(remaining):
                print(f"  {i}/{len(remaining)} rides")

    if total_skipped:
        print(f"  Skipped {total_skipped:,} segments > {config.MAX_ROUTING_DISTANCE_M}m")

    # 8. Final save, covering everything since the last checkpoint
    _checkpoint()
    print(f"  Route cache: {len(route_cache):,} entries saved")

    # 9. Render
    render_data = _get_render_data(G)
    assert render_data is not None
    edge_geom, edge_hw, edge_name = render_data
    _finalize(state, edge_geom, edge_hw, edge_name, skip_png=skip_png)

    # Summary
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  {len(state['processed_files'])} NYC rides")
    print(f"  {len(state.get('skipped_files', set()))} non-NYC rides skipped")
    print(f"  {len(state['edge_counts']):,} unique edges")
