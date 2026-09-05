"""Audit the ridden map by neighbourhood, against the citywide coverage figure.

The map's one geographic statistic is a single percentage of "the network",
and that network is built from the rides' own bounding box -- so it includes
everywhere a ride has ever strayed to and none of the city nobody rode near.
This script cuts the same measurement into NYC's 2020 Neighborhood
Tabulation Areas and reports what the single number hides:

1. **How much of the denominator is not New York City.**  Every rideable edge
   is placed in an NTA by its midpoint; what lands further than
   `neighborhoods.BOUNDARY_TOLERANCE_M` from all of them is outside the city,
   and the difference between the two percentages is the answer.

2. **Where the riding actually is**, per neighbourhood and per borough, by
   unique kilometres and by passes.  The two rankings mostly agree, and where
   they disagree is a corridor ridden over and over against a neighbourhood
   explored once.

3. **Whether midpoint assignment is good enough.**  `--boundaries` splits
   every edge on the polygons it crosses and reports the metres the midpoint
   rule misplaces, worst offenders first.  Streets are short and this is
   noise; bridges and waterfront paths are neither.

4. **Whether the Citibike docks really do fill in where the rides are thin**,
   which is what `findings/citibike-trips.md` claims.  Per neighbourhood they
   do the opposite.

Reads cache/render_cache.pkl, cache/state.pkl, cache/nta_boundaries.geojson
and (for the dock comparison) the Citibike caches; writes nothing.  The
write-up of what it found is findings/neighborhoods.md.

Usage:
    python tools/neighborhood_audit.py [--top N] [--boundaries]   # repo root
"""

from __future__ import annotations

import argparse
import json
import pickle
from typing import TYPE_CHECKING, Any

from bike_routes import config, neighborhoods
from bike_routes.edge_speed import ride_traversals
from bike_routes.merge import _geom_len_m

if TYPE_CHECKING:
    from pathlib import Path

TOP_N = 15


def _read_pickle(path: Path) -> Any:  # noqa: ANN401 -- caller knows the shape
    """Load a pipeline cache, or exit with what is missing."""
    if not path.exists():
        msg = f"{path} not found -- run the pipeline from the repo root first"
        raise SystemExit(msg)
    with path.open("rb") as f:
        return pickle.load(f)


def _passes(
    state: dict[str, Any],
    edge_rides: dict[tuple[int, int], list[str]],
    keys: list[tuple[int, int]],
) -> int:
    """Total traversals over a set of edges -- passes, not rides."""
    return sum(
        ride_traversals(state, key, ride) for key in keys for ride in set(edge_rides.get(key, ()))
    )


def _report_totals(rows: list[dict[str, Any]], citywide: dict[str, Any] | None) -> None:
    """Print the shipped percentage against the same measurement inside the city."""
    net = sum(r["net_m"] for r in rows) / 1000
    ridden = sum(r["ridden_m"] for r in rows) / 1000
    print("\nThe denominator:")
    if citywide:
        print(f"  rideable network in the graph  {citywide['network_km']:9,.0f} km")
        print(f"  ridden                         {citywide['ridden_km']:9,.1f} km")
        print(f"  coverage as shipped            {citywide['pct']:9.2f}%")
    print(f"  of the graph, inside an NTA    {net:9,.0f} km")
    print(f"  ridden inside an NTA           {ridden:9,.1f} km")
    print(f"  coverage of New York City      {100 * ridden / max(net, 1):9.2f}%")
    if citywide:
        outside = citywide["network_km"] - net
        print(f"  -> {outside:,.0f} km of the denominator is not in a NYC neighbourhood")


def _report_areas(rows: list[dict[str, Any]], top: int) -> None:
    """Rank neighbourhoods by ridden km, and again by passes."""
    touched = [r for r in rows if r["ridden_m"] > 0]
    have_net = [r for r in rows if r["net_m"] > 0]
    lengths = sorted(r["ridden_m"] for r in touched)
    print(f"\nNeighbourhoods with any ridden street: {len(touched)}/{len(have_net)} in the graph")
    if touched:
        print(f"  median ridden in one:     {lengths[len(lengths) // 2] / 1000:.2f} km")
        print(f"  under 500 m:              {sum(1 for m in lengths if m < 500)}")
    total = sum(r["ridden_m"] for r in rows)
    for n in (5, 20, 50):
        share = sum(lengths[-n:]) / max(total, 1)
        print(f"  top {n:<3}hold                 {100 * share:.0f}% of ridden km")

    # Passes here are over the same rideable edges the coverage column is
    # measured on, so the two agree with each other. The map's own popup
    # counts every drawn corridor in the area, sidewalks and service roads
    # included, and is roughly 3x larger for it.
    print(f"\nTop {top} by ridden km (passes over rideable edges only):")
    print(f"  {'neighbourhood':44} {'bo':2} {'ridden':>8} {'of it':>6} {'rides':>6} {'passes':>7}")
    for r in sorted(touched, key=lambda r: -r["ridden_m"])[:top]:
        pct = 100 * r["ridden_m"] / r["net_m"]
        print(
            f"  {r['name'][:44]:44} {r['boro']:2} {r['ridden_m'] / 1000:6.1f} km "
            f"{pct:5.1f}% {len(r['rides']):6,} {r['passes']:7,}"
        )

    # Passes per ridden km separates a corridor ridden daily from a
    # neighbourhood ridden across once. Both ends are worth seeing: the
    # question is whether "rode through" and "explored" are different shapes
    # in this data or the same one.
    ranked = sorted(
        (r for r in touched if r["ridden_m"] >= 500),
        key=lambda r: -r["passes"] / (r["ridden_m"] / 1000),
    )
    for label, subset in (("Rode through", ranked[:8]), ("Explored", ranked[-8:][::-1])):
        print(f"\n{label} -- passes per ridden km:")
        for r in subset:
            print(
                f"  {r['name'][:40]:40} {r['boro']:2} "
                f"{r['passes'] / (r['ridden_m'] / 1000):6.0f}/km  "
                f"{r['ridden_m'] / 1000:5.1f} km  {len(r['rides']):4,} rides  "
                f"{100 * r['ridden_m'] / r['net_m']:5.1f}% covered"
            )


def _report_boroughs(rows: list[dict[str, Any]]) -> None:
    """Roll the areas up, which is where the citywide number is most wrong."""
    boros: dict[str, list[float]] = {}
    for r in rows:
        if r["net_m"] <= 0:
            continue
        b = boros.setdefault(r["boro"], [0.0, 0.0, 0, 0])
        b[0] += r["ridden_m"]
        b[1] += r["net_m"]
        b[2] += 1 if r["ridden_m"] > 0 else 0
        b[3] += 1
    print("\nBy borough:")
    print(f"  {'':3} {'ridden':>9} {'network':>9} {'covered':>8}  areas touched")
    for name, (ridden, net, touched, total) in sorted(boros.items(), key=lambda kv: -kv[1][0]):
        print(
            f"  {name:3} {ridden / 1000:8.1f} km {net / 1000:8.0f} km "
            f"{100 * ridden / net:7.1f}%  {touched}/{total}"
        )


def _report_boundaries(
    areas: neighborhoods.Areas,
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_hw: dict[tuple[int, int], str],
    edge_name: dict[tuple[int, int], str],
    state: dict[str, Any],
) -> None:
    """Split every ridden edge on the boundaries it crosses, and compare."""
    from shapely.geometry import LineString  # noqa: PLC0415 -- only this report needs it

    edge_counts = state["edge_counts"]
    keys = [
        k
        for k in edge_geom
        if k in edge_counts and edge_hw.get(k, "") not in config.COVERAGE_EXCLUDE
    ]
    placed = areas.locate([neighborhoods._midpoint(edge_geom[k]) for k in keys])  # noqa: SLF001
    total = misplaced = 0.0
    crossers = 0
    worst: list[tuple[float, float, str, str]] = []
    for key, home in zip(keys, placed):
        line = LineString(edge_geom[key])
        length = _geom_len_m(edge_geom[key])
        total += length
        candidates = [int(i) for i in areas._tree.query(line)]  # noqa: SLF001
        if len(candidates) <= 1 or not line.length:
            continue
        elsewhere = length
        for i in candidates:
            share = line.intersection(areas.shapes[i]).length / line.length
            if i == home:
                elsewhere -= share * length
        if elsewhere <= 1.0:
            continue
        crossers += 1
        misplaced += elsewhere
        name = edge_name.get(key) or f"unnamed {key}"
        worst.append((elsewhere, length, name, areas.names[home] if home >= 0 else "outside"))

    print(f"\nMidpoint assignment, over {total / 1000:,.1f} km of ridden street:")
    print(f"  {crossers:,} edges cross a boundary")
    print(f"  {misplaced / 1000:,.1f} km ({100 * misplaced / max(total, 1):.2f}%) of ridden length")
    print("  sits outside the neighbourhood its midpoint fell in.")
    print("\n  Worst placed -- these are the ones splitting would fix:")
    for out_m, length, name, home in sorted(worst, reverse=True)[:10]:
        print(f"    {out_m:6.0f} m of {length:7.0f} m  {name[:42]:42} -> {home}")


def _report_citibike(areas: neighborhoods.Areas, rows: list[dict[str, Any]], top: int) -> None:
    """Check the 'docks cluster where the heatmap is thin' claim per area."""
    if not config.CITIBIKE_TRIPS_PATH.exists() or not config.CITIBIKE_STATIONS_PATH.exists():
        print("\nNo Citibike caches; skipping the dock comparison.")
        return
    with config.CITIBIKE_TRIPS_PATH.open() as f:
        trips = json.load(f)
    with config.CITIBIKE_STATIONS_PATH.open() as f:
        stations = json.load(f)

    names = [n for n in trips.get("docks", ()) if n in stations]
    placed = areas.locate([(stations[n]["lon"], stations[n]["lat"]) for n in names])
    dock_area = dict(zip(names, placed))
    ends: dict[int, int] = {}
    for t in trips["trips"]:
        for end in ("a", "b"):
            area = dock_area.get(t[end])
            if area is not None and area >= 0:
                ends[area] = ends.get(area, 0) + 1

    unplaced = len(trips.get("docks", ())) - len(names)
    empty = [i for i in ends if rows[i]["ridden_m"] == 0]
    print(f"\nCitibike docks by neighbourhood ({unplaced} docks have no coordinates):")
    print(f"  {len(ends)} neighbourhoods have a dock endpoint")
    print(f"  of those, with no ridden street at all: {len(empty)}")
    print(f"\n  {'neighbourhood':40} {'bo':2} {'dock ends':>9} {'ridden':>8} {'covered':>8}")
    for area, n in sorted(ends.items(), key=lambda kv: -kv[1])[:top]:
        r = rows[area]
        pct = 100 * r["ridden_m"] / r["net_m"] if r["net_m"] else 0
        print(
            f"  {r['name'][:40]:40} {r['boro']:2} {n:9,} {r['ridden_m'] / 1000:6.1f} km {pct:7.1f}%"
        )
    print("\n  Spearman against the busiest dock neighbourhoods:")
    pairs = sorted(ends.items(), key=lambda kv: -kv[1])
    counts = [n for _, n in pairs]
    print(f"    vs coverage %  {_spearman(counts, [_pct(rows[i]) for i, _ in pairs]):+.3f}")
    print(f"    vs ridden km   {_spearman(counts, [rows[i]['ridden_m'] for i, _ in pairs]):+.3f}")
    print("  Positive means the docks are busiest where the map is thickest.")


def _pct(row: dict[str, Any]) -> float:
    """Share of a neighbourhood's rideable network that has been ridden."""
    return 100 * row["ridden_m"] / row["net_m"] if row["net_m"] else 0.0


def _spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, with no scipy import for two dozen points."""
    import numpy as np  # noqa: PLC0415 -- keep the import next to its use

    ra = np.argsort(np.argsort(np.asarray(a, dtype=float)))
    rb = np.argsort(np.argsort(np.asarray(b, dtype=float)))
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> None:
    """Measure the ridden map per neighbourhood and report on it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--top", type=int, default=TOP_N, metavar="N", help="rows per ranking")
    parser.add_argument(
        "--boundaries",
        action="store_true",
        help="also split every ridden edge on the polygons it crosses (slow)",
    )
    args = parser.parse_args()

    if not neighborhoods.ensure_boundaries():
        raise SystemExit(1)
    areas = neighborhoods.load_areas()
    if areas is None:
        msg = f"{config.NTA_CACHE_PATH} is unusable"
        raise SystemExit(msg)

    state = _read_pickle(config.STATE_CACHE_PATH)
    cached = _read_pickle(config.RENDER_CACHE_PATH)
    if not (isinstance(cached, tuple) and len(cached) == 4):
        msg = "render cache is in a legacy format -- run the pipeline once to rebuild"
        raise SystemExit(msg)
    _fmt, edge_geom, edge_hw, edge_name = cached

    print(f"Placing {len(edge_geom):,} edges in {len(areas)} neighbourhoods...")
    rows = neighborhoods.measure(areas, edge_geom, edge_hw, state)

    # measure() carries the ride set; passes need the traversal counts, which
    # only this report reads, so they are added here rather than shipped.
    edge_rides = state.get("edge_rides", {})
    per_area: dict[int, list[tuple[int, int]]] = {}
    keys = [k for k in edge_geom if edge_hw.get(k, "") not in config.COVERAGE_EXCLUDE]
    for key, area in zip(keys, areas.locate([neighborhoods._midpoint(edge_geom[k]) for k in keys])):  # noqa: SLF001
        if area >= 0 and key in edge_rides:
            per_area.setdefault(area, []).append(key)
    for i, row in enumerate(rows):
        row["passes"] = _passes(state, edge_rides, per_area.get(i, []))

    from bike_routes.export import _coverage_summary  # noqa: PLC0415 -- avoids a cycle at import

    _report_totals(rows, _coverage_summary(edge_geom, edge_hw, state))
    _report_areas(rows, args.top)
    _report_boroughs(rows)
    _report_citibike(areas, rows, args.top)
    if args.boundaries:
        _report_boundaries(areas, edge_geom, edge_hw, edge_name, state)


if __name__ == "__main__":
    main()
