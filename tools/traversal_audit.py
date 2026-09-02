"""Audit the per-ride traversal counts that drive the map's frequency colour.

The map used to count a segment once per ride, so a park lap ridden four times
drew as 1.  Counts now come from edge_speed's GPS pass detector instead, and
this script is the evidence for the two judgement calls that involves:

1. **Is the detector inventing traversals?**  It prints the distribution of
   traversals per (ride, edge) and the overall inflation ratio.  A ratio a few
   percent above 1.0 is what real riding looks like -- round trips and loops
   are a minority of edges.  A much larger one means the detector is firing on
   noise, and config.TRAVERSAL_RESUME_M is the first knob to reach for.  The
   highest-multiplicity pairs are listed with their dates and street names so
   they can be checked against the actual ride.

2. **Is the corridor merge rule still earning its keep?**  merge.py collapses
   parallel ways (a street and the bike lane beside it) into one drawn
   corridor, keeping the larger per-ride count WITHIN each direction of
   travel and adding the two directions.  Plain sum would turn one pass
   drifting between the ways into two; plain max would read an out-and-back
   riding one way out and the other back as one.  --merge runs the whole
   merge under all three rules and reports how far apart they land, so the
   rule can be revisited with numbers rather than argument.

Reads cache/state.pkl, cache/render_cache.pkl and rides/; writes nothing.
Safe to run before the pipeline has ever computed traversals -- it measures
them itself, into a throwaway state.

Usage:
    python tools/traversal_audit.py [--rides N] [--merge]   # from the repo root
"""

from __future__ import annotations

import argparse
import pickle
import random
from collections import Counter
from pathlib import Path
from typing import Any

from bike_routes import config, edge_speed, merge
from bike_routes.export import _geom_len_m

TOP_N = 20


def _read_pickle(path: Path) -> Any:  # noqa: ANN401 -- caller knows the shape
    """Load a pipeline cache, or exit with what is missing."""
    if not path.exists():
        msg = f"{path} not found -- run the pipeline from the repo root first"
        raise SystemExit(msg)
    with path.open("rb") as f:
        return pickle.load(f)


def _measure(
    state: dict[str, Any],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    rides: list[str],
) -> dict[tuple[int, int], dict[str, list[int]]]:
    """Measure traversals for the given rides into a throwaway state.

    Deliberately not read from state["edge_traversals"]: the point is to be
    runnable before a pipeline run, and to re-measure after a threshold change
    without touching the real cache.
    """
    ride_set = set(rides)
    ride_edges: dict[str, list[tuple[int, int]]] = {}
    for key, users in state.get("edge_rides", {}).items():
        for r in set(users):
            if r in ride_set:
                ride_edges.setdefault(r, []).append(key)

    scratch: dict[str, Any] = {"edge_speed": {}, "edge_traversals": {}}
    for i, fname in enumerate(sorted(rides), 1):
        path = Path(config.RIDES_FOLDER) / fname
        keys = ride_edges.get(fname)
        if keys and path.exists():
            edge_speed._fold_ride(scratch, edge_geom, keys, path)  # noqa: SLF001
        if i % 200 == 0:
            print(f"  measured {i:,}/{len(rides):,} rides")
    return scratch["edge_traversals"]


def _report_distribution(
    state: dict[str, Any],
    traversals: dict[tuple[int, int], dict[str, list[int]]],
    rides: list[str],
) -> None:
    """Print how many (ride, edge) pairs got more than one traversal."""
    ride_set = set(rides)
    pairs = sum(len(ride_set.intersection(users)) for users in state.get("edge_rides", {}).values())
    hist = Counter(sum(pair) for per_ride in traversals.values() for pair in per_ride.values())
    repeats = sum(c for n, c in hist.items() if n >= 2)
    extra = sum((n - 1) * c for n, c in hist.items())

    print(f"\nRides measured:            {len(rides):,}")
    print(f"(ride, edge) pairs:        {pairs:,}")
    print(f"  crossed more than once:  {repeats:,} ({100 * repeats / max(pairs, 1):.2f}%)")
    print(f"  inflation ratio:         {(pairs + extra) / max(pairs, 1):.3f}x")
    print("\nTraversals per (ride, edge):")
    # Singles are stored too now (merge.py needs their direction), so the
    # ones line counts every pair that is not a repeat, measured or not.
    print(f"  1  {pairs - repeats:>9,}")
    for n in sorted(n for n in hist if n >= 2):
        print(f"  {n:<2} {hist[n]:>9,}")


def _report_top_pairs(
    traversals: dict[tuple[int, int], dict[str, list[int]]],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_name: dict[tuple[int, int], str],
) -> None:
    """List the highest counts, which are the ones worth eyeballing."""
    rows = [
        (sum(pair), key, ride)
        for key, per_ride in traversals.items()
        for ride, pair in per_ride.items()
    ]
    rows.sort(key=lambda r: (-r[0], r[2]))
    print(f"\nTop {TOP_N} (ride, edge) pairs -- check these against the ride:")
    print(f"  {'x':>3}  {'length':>7}  {'date':<10}  street")
    for n, key, ride in rows[:TOP_N]:
        length = _geom_len_m(edge_geom[key]) if key in edge_geom else 0.0
        name = edge_name.get(key) or f"unnamed {key}"
        print(f"  {n:>3}  {length:>6.0f}m  {ride[:10]:<10}  {name}")


def _report_merge_rule(
    state: dict[str, Any],
    traversals: dict[tuple[int, int], dict[str, list[int]]],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_name: dict[tuple[int, int], str],
) -> None:
    """Run the whole merge under both rules and report the disagreement."""

    def features() -> list[dict[str, Any]]:
        out = []
        for key, users in state.get("edge_rides", {}).items():
            if key not in edge_geom:
                continue
            per_ride = traversals.get(key, {})
            out.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": edge_geom[key]},
                    "properties": {
                        "_rides": {r: tuple(per_ride.get(r, (0, 0))) for r in set(users)},
                        "_name": edge_name.get(key),
                    },
                }
            )
        return out

    def summarize(label: str) -> dict[str, int]:
        out = merge._merge_parallel_features(features())  # noqa: SLF001
        counts = {}
        for f in out:
            mid = f["geometry"]["coordinates"][len(f["geometry"]["coordinates"]) // 2]
            counts[f"{round(mid[0], 5)},{round(mid[1], 5)}"] = f["properties"]["ride_count"]
        total = sum(counts.values())
        print(f"  {label:<5} corridors {len(out):,}  total {total:,}  max {max(counts.values()):,}")
        return counts

    print("\nMerge rule: shipped (max per direction) vs the two it replaced:")
    by_dir = summarize("dir")

    original = merge._merge_ride_counts  # noqa: SLF001

    def collapsed(dst: dict, src: dict, *, flip: bool) -> None:  # noqa: ARG001
        """Plain max: what the map did before direction was recorded."""
        for r, pair in src.items():
            n = pair[0] + pair[1]
            # A ride with nothing measured still belongs to the corridor, so
            # insert it rather than comparing it away: _ride_passes floors it.
            if r not in dst or n > dst[r][0]:
                dst[r] = (n, 0)

    def summed(dst: dict, src: dict, *, flip: bool) -> None:  # noqa: ARG001
        for r, pair in src.items():
            dst[r] = (dst.get(r, (0, 0))[0] + pair[0] + pair[1], 0)

    by_rule = {}
    for label, fn in (("max", collapsed), ("sum", summed)):
        merge._merge_ride_counts = fn  # noqa: SLF001
        try:
            by_rule[label] = summarize(label)
        finally:
            merge._merge_ride_counts = original  # noqa: SLF001

    for label in ("max", "sum"):
        other = by_rule[label]
        shared = set(by_dir) & set(other)
        differ = [k for k in shared if by_dir[k] != other[k]]
        delta = sum(other[k] - by_dir[k] for k in differ)
        print(
            f"  vs {label}: {len(differ):,}/{len(shared):,} corridors differ "
            f"({100 * len(differ) / max(len(shared), 1):.1f}%), {delta:+,} passes"
        )
    print("  Every one of those is either a genuine out-and-back on separately")
    print("  mapped directions, or one pass double-counted. Spot-check a few.")


def main() -> None:
    """Measure traversals for a sample of rides and report on them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rides", type=int, metavar="N", help="sample only N rides (default: all)")
    parser.add_argument(
        "--merge", action="store_true", help="also compare the max and sum merge rules (slow)"
    )
    args = parser.parse_args()

    state = _read_pickle(config.STATE_CACHE_PATH)
    cached = _read_pickle(config.RENDER_CACHE_PATH)
    if not (isinstance(cached, tuple) and len(cached) == 4):
        msg = "render cache is in a legacy format -- run the pipeline once to rebuild"
        raise SystemExit(msg)
    _fmt, edge_geom, _edge_hw, edge_name = cached

    rides = sorted(state.get("processed_files", ()))
    if args.rides is not None and args.rides < len(rides):
        rides = sorted(random.Random(0).sample(rides, args.rides))  # noqa: S311 -- not security

    print(f"Measuring {len(rides):,} of {len(state.get('processed_files', ())):,} rides...")
    traversals = _measure(state, edge_geom, rides)

    _report_distribution(state, traversals, rides)
    _report_top_pairs(traversals, edge_geom, edge_name)
    if args.merge:
        if args.rides is not None:
            print("\nNote: --rides sampled, so merge totals below are of that sample only.")
        _report_merge_rule(state, traversals, edge_geom, edge_name)


if __name__ == "__main__":
    main()
