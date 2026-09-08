"""Rank the stretches that are consistently fast, and the ones consistently slow.

The stats panel already ranks corridors by how far their two directions part
(edge_speed._top_corridors).  That question needs three passes each way, so it
can only ever be answered on a two-way street -- and it is answered almost
entirely by bridges, whose signal is a gradient.  A one-way street is invisible
to it, and a one-way street is exactly where a plain speed says something: every
pass is the same direction, so the average is a pace rather than a mixture of
two.

So this ranks absolute speed instead, over a stretch a rider would recognise:
a run of measured chunks along one named street, chained across the edges OSM
happens to have split it into, travelled one way.  A stretch is ranked by the
speed it beats on most passes -- mean minus one standard deviation across the
rides that rode it, and mean plus one for the slow end -- so a street that was
fast once and crawling twice loses to one that is the same every time.  That is
what "consistently" has to mean here: the spread is across passes, not along
the street.

Everything is measured from the ride CSVs, not read from state["edge_speed"]:
the stored record keeps only per-chunk totals, which cannot say how a stretch
varied pass to pass.  The pass rules are shared with the pipeline
(edge_speed._measure_ride, ._admitted_passes, ._pass_chunks), so what is ranked
here is the same metres the map counts.

Reads cache/state.pkl, cache/render_cache.pkl and rides/; writes nothing.

Usage:
    python tools/speed_consistency.py [--rides N] [--sweep]   # from the repo root
"""

from __future__ import annotations

import argparse
import pickle
import statistics
from pathlib import Path
from typing import Any

from bike_routes import config, edge_speed

TOP_N = 12
MIN_PASSES = 5  # rides over the whole stretch before it is ranked
MIN_M = 250.0  # config.SPEED_CORRIDOR_MIN_M: shorter is an anecdote
MIN_COVER = 0.5  # fraction of the stretch a ride must cover to set its pace
KMH_TO_MPH = 0.621371

# One unit is one direction of one chunk: the pipeline's own atom of speed.
Unit = tuple[tuple[int, int], int, int]


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
    edge_name: dict[tuple[int, int], str],
    rides: list[str],
) -> dict[Unit, dict[str, Any]]:
    """Measure every ride's passes, keeping each pass rather than a total.

    Returns one entry per (edge, chunk, direction) actually ridden: its
    geometry oriented along the direction of travel, its street name, and
    what each ride covered of it -- metres and seconds, so the ride's own
    speed over the stretch can be recovered later.
    """
    ride_set = set(rides)
    ride_edges: dict[str, list[tuple[int, int]]] = {}
    for key, users in state.get("edge_rides", {}).items():
        for r in set(users):
            if r in ride_set:
                ride_edges.setdefault(r, []).append(key)

    units: dict[Unit, dict[str, Any]] = {}
    for i, fname in enumerate(sorted(rides), 1):
        path = Path(config.RIDES_FOLDER) / fname
        keys = ride_edges.get(fname)
        if not keys or not path.exists():
            continue
        passes, along, times, slot_keys, slot_len = edge_speed._measure_ride(  # noqa: SLF001
            edge_geom, keys, path
        )
        for s, a, b in edge_speed._admitted_passes(passes, along, times, slot_len):  # noqa: SLF001
            key = slot_keys[s]
            length = slot_len[s]
            nchunk = edge_speed._n_chunks(length)  # noqa: SLF001
            base = edge_speed._FWD if along[b - 1] > along[a] else edge_speed._REV  # noqa: SLF001
            chunks = edge_speed._pass_chunks(along, times, a, b, length, nchunk)  # noqa: SLF001
            for ci, (dist, secs, _moving) in chunks.items():
                if secs <= 0:
                    continue
                unit = units.get((key, ci, base))
                if unit is None:
                    unit = _new_unit(key, ci, base, nchunk, edge_geom, edge_name)
                    if unit is None:
                        continue
                    units[key, ci, base] = unit
                got = unit["rides"].setdefault(fname, [0.0, 0.0])
                got[0] += dist
                got[1] += secs
        if i % 200 == 0:
            print(f"  measured {i:,}/{len(rides):,} rides")
    return units


def _new_unit(
    key: tuple[int, int],
    ci: int,
    base: int,
    nchunk: int,
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_name: dict[tuple[int, int], str],
) -> dict[str, Any] | None:
    """Build the record for one chunk-direction, or None if it cannot be cut.

    The geometry is stored pointing the way the rider went, which is what
    lets consecutive units be chained end to end whichever way the edge
    itself is stored.
    """
    coords = [tuple(c) for c in edge_geom.get(key, [])]
    if len(coords) < 2:
        return None
    pieces = edge_speed._chunk_slices(coords, nchunk)  # noqa: SLF001
    if len(pieces) != nchunk:
        return None
    piece = pieces[ci]
    if base == edge_speed._REV:  # noqa: SLF001
        piece = piece[::-1]
    return {
        "piece": piece,
        "m": edge_speed._line_len(piece),  # noqa: SLF001
        "name": edge_name.get(key),
        "rides": {},
    }


def _usable(units: dict[Unit, dict[str, Any]], min_passes: int) -> dict[Unit, dict[str, Any]]:
    """Drop the chunk-directions too rarely ridden to chain or to rank."""
    return {
        u: d
        for u, d in units.items()
        if d["name"]
        and len(d["rides"]) >= min_passes
        and sum(r[0] for r in d["rides"].values()) >= config.SPEED_MIN_DIST_M
    }


def _chain(units: dict[Unit, dict[str, Any]]) -> list[list[Unit]]:
    """Chain units into stretches: one street, one way, end to end.

    Within an edge the chunks are already in order; across edges a unit's
    last point is the next one's first, which is the graph node they share.
    Where a name continues into more than one edge (a fork, a street meeting
    itself) the straightest continuation wins, so the chain does not depend
    on dictionary order.  Each unit is used once, so stretches are disjoint
    and no metre is ranked twice.
    """
    starts: dict[tuple[float, float], list[Unit]] = {}
    for u, d in sorted(units.items()):
        starts.setdefault(_at(d["piece"][0]), []).append(u)

    def successors(u: Unit) -> list[Unit]:
        key, ci, base = u
        nxt = (key, ci + 1, base) if base == edge_speed._FWD else (key, ci - 1, base)  # noqa: SLF001
        if nxt in units:
            return [nxt]
        end = _at(units[u]["piece"][-1])
        return [
            v for v in starts.get(end, []) if v[0] != key and units[v]["name"] == units[u]["name"]
        ]

    def straightest(u: Unit, options: list[Unit]) -> Unit:
        heading = _bearing(units[u]["piece"])
        return min(options, key=lambda v: (_turn(heading, _bearing(units[v]["piece"])), v))

    has_predecessor = {v for u in units for v in successors(u)}
    used: set[Unit] = set()
    chains: list[list[Unit]] = []
    seeds = [u for u in sorted(units) if u not in has_predecessor] + sorted(units)
    for seed in seeds:
        if seed in used:
            continue
        chain: list[Unit] = []
        cur: Unit | None = seed
        while cur is not None and cur not in used:
            used.add(cur)
            chain.append(cur)
            options = [v for v in successors(cur) if v not in used]
            cur = straightest(cur, options) if options else None
        chains.append(chain)
    return chains


def _at(point: tuple[float, float]) -> tuple[float, float]:
    """Round a coordinate to the precision two edges of one node agree on."""
    return (round(point[0], 6), round(point[1], 6))


def _bearing(piece: list[tuple[float, float]]) -> float:
    """Bearing of travel along an already-oriented chunk."""
    return edge_speed._chord(piece)[0]  # noqa: SLF001


def _turn(a: float, b: float) -> float:
    """Absolute angle between two bearings, in degrees."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _summarize(
    chain: list[Unit],
    units: dict[Unit, dict[str, Any]],
    every_unit: dict[Unit, dict[str, Any]],
    min_passes: int,
    min_cover: float,
) -> dict[str, Any] | None:
    """Collapse a stretch into one ranked row, or None if too little rode it.

    A ride sets the stretch's pace only if it covered min_cover of it: a
    rider who turned off after one block says nothing about the mile, and
    averaging their block in as an equal would let the shortest sample decide
    the ranking.
    """
    total = sum(units[u]["m"] for u in chain)
    covered: dict[str, list[float]] = {}
    for u in chain:
        for ride, (dist, secs) in units[u]["rides"].items():
            got = covered.setdefault(ride, [0.0, 0.0])
            got[0] += dist
            got[1] += secs
    speeds = [
        3.6 * dist / secs
        for dist, secs in covered.values()
        if secs > 0 and dist >= min_cover * total
    ]
    if len(speeds) < min_passes:
        return None
    mean = statistics.mean(speeds)
    sd = statistics.pstdev(speeds)
    middle = units[chain[len(chain) // 2]]["piece"]
    return {
        "name": units[chain[0]]["name"],
        "m": total,
        "edges": len({u[0] for u in chain}),
        "one_way": not any(_opposite(u) in every_unit for u in chain),
        "n": len(speeds),
        "mean": mean,
        "sd": sd,
        "floor": mean - sd,
        "ceiling": mean + sd,
        "dir": edge_speed._octant(_bearing(middle)),  # noqa: SLF001
        "at": middle[len(middle) // 2],
    }


def _opposite(unit: Unit) -> Unit:
    """Return the same chunk, ridden the other way."""
    key, ci, base = unit
    return (key, ci, edge_speed._REV if base == edge_speed._FWD else edge_speed._FWD)  # noqa: SLF001


def _stretches(
    units: dict[Unit, dict[str, Any]],
    min_passes: int,
    min_m: float,
    min_cover: float,
) -> list[dict[str, Any]]:
    """Every rankable stretch, measured.

    Chains are built from the units that clear the pass floor, but whether a
    stretch was ever ridden against the flow is asked of every unit measured:
    one pass the other way is enough to say the street is not one-way.
    """
    usable = _usable(units, min_passes)
    out = []
    for chain in _chain(usable):
        if sum(usable[u]["m"] for u in chain) < min_m:
            continue
        row = _summarize(chain, usable, units, min_passes, min_cover)
        if row:
            out.append(row)
    return out


def _ranked(rows: list[dict[str, Any]], field: str, *, fastest: bool) -> list[dict[str, Any]]:
    """Sort by the given bound, one entry per street and direction.

    A street ridden the same way twice in one list is two stretches of it,
    and the second says nothing the first did not -- the same rule
    _top_corridors uses to stop one bridge filling the panel.
    """
    ordered = sorted(rows, key=lambda r: (-r[field] if fastest else r[field], r["name"]))
    seen: set[tuple[str, str]] = set()
    out = []
    for r in ordered:
        if (r["name"], r["dir"]) in seen:
            continue
        seen.add((r["name"], r["dir"]))
        out.append(r)
    return out


def _mph(kmh: float) -> float:
    """Speeds are stored in km/h and read in mph, like the map's."""
    return kmh * KMH_TO_MPH


def _print_rows(title: str, rows: list[dict[str, Any]], top: int) -> None:
    """Print one ranking, with what each row is a claim about.

    The midpoint is there to be pasted into a map: a ranking of streets is
    only worth as much as the stretch it names can be found and checked.
    """
    print(f"\n{title}")
    print(
        f"  {'mph':>12}  {'rides':>5}  {'length':>7}  {'dir':>3}  {'way':>4}  {'lat,lon':<19}  street"
    )
    for r in rows[:top]:
        lon, lat = r["at"]
        print(
            f"  {_mph(r['mean']):5.1f} +-{_mph(r['sd']):4.1f}  {r['n']:>5}  "
            f"{r['m']:>6.0f}m  {r['dir']:>3}  {'one' if r['one_way'] else 'both':>4}  "
            f"{lat:.5f},{lon:.5f}  {r['name']}"
        )


def _print_coverage(
    units: dict[Unit, dict[str, Any]],
    rows: list[dict[str, Any]],
    rides: list[str],
) -> None:
    """Print how much of the riding survives to be ranked at all.

    The one-way count is the reason this ranking exists beside the
    direction-split one: a stretch never ridden against the flow can never
    appear in that list, whatever it does here.
    """
    usable = _usable(units, MIN_PASSES)
    ranked_km = sum(r["m"] for r in rows) / 1000
    one_way = sum(1 for r in rows if r["one_way"])
    print(f"\nRides measured:            {len(rides):,}")
    print(f"Chunk-directions:          {len(units):,}")
    print(f"  ridden {MIN_PASSES}+ times:        {len(usable):,} ({_km(usable.values()):.1f} km)")
    print(f"Stretches {MIN_M:.0f} m or longer:   {len(rows):,} ({ranked_km:.1f} km)")
    print(f"  never ridden the other way: {one_way:,}")
    spread = [r["sd"] / r["mean"] for r in rows if r["mean"] > 0]
    if spread:
        print(
            f"  pass-to-pass spread:     {100 * statistics.median(spread):.0f}% of the mean, median"
        )


def _km(units: Any) -> float:  # noqa: ANN401 -- any iterable of unit records
    """Kilometres of the given chunk-directions."""
    return sum(u["m"] for u in units) / 1000


def _print_sweep(units: dict[Unit, dict[str, Any]]) -> None:
    """Show how far the two lists move when the floors move.

    A ranking that reshuffles when the bar moves is partly a ranking of the
    bar, and this prints how much of each top ten survives the move.  Read
    the length and coverage rows as stability -- they keep 6 to 9 of 10 --
    and the pass floor as what it is: a bar on the evidence, not on the
    stretch.  Lowering it to three admits stretches ridden three times,
    which is where a lucky run outranks a street; raising it to eight leaves
    only the most-ridden streets in the pool at all.  The names change
    because the pool does.
    """
    base_rows = _stretches(units, MIN_PASSES, MIN_M, MIN_COVER)
    base = {
        "fast": _names(_ranked(base_rows, "floor", fastest=True)),
        "slow": _names(_ranked(base_rows, "ceiling", fastest=False)),
    }
    print("\nSensitivity -- top ten kept, against the defaults:")
    print(f"  {'passes':>6} {'min m':>6} {'cover':>6}  {'rows':>5}  {'fast':>5}  {'slow':>5}")
    for passes, min_m, cover in [
        (3, MIN_M, MIN_COVER),
        (8, MIN_M, MIN_COVER),
        (MIN_PASSES, 150.0, MIN_COVER),
        (MIN_PASSES, 400.0, MIN_COVER),
        (MIN_PASSES, MIN_M, 0.3),
        (MIN_PASSES, MIN_M, 0.8),
    ]:
        rows = _stretches(units, passes, min_m, cover)
        fast = _names(_ranked(rows, "floor", fastest=True))
        slow = _names(_ranked(rows, "ceiling", fastest=False))
        print(
            f"  {passes:>6} {min_m:>6.0f} {cover:>6.1f}  {len(rows):>5}  "
            f"{len(base['fast'] & fast):>4}  {len(base['slow'] & slow):>5}"
        )


def _names(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """Reduce a ranking to its top ten, as street-and-direction pairs."""
    return {(r["name"], r["dir"]) for r in rows[:10]}


def main() -> None:
    """Print the fastest and slowest stretches, and what they rest on."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rides", type=int, default=0, help="measure only the first N rides")
    ap.add_argument("--top", type=int, default=TOP_N, help="rows per list")
    ap.add_argument("--sweep", action="store_true", help="also test the floors")
    args = ap.parse_args()

    state = _read_pickle(config.STATE_CACHE_PATH)
    cached = _read_pickle(config.RENDER_CACHE_PATH)
    if not isinstance(cached, tuple) or cached[0] != config.RENDER_CACHE_FORMAT:
        msg = "render cache is stale -- run the pipeline to rebuild it"
        raise SystemExit(msg)
    _fmt, edge_geom, _edge_hw, edge_name = cached

    rides = sorted(state.get("processed_files", ()))
    if args.rides:
        rides = rides[: args.rides]
    if not rides:
        msg = "no processed rides in cache/state.pkl"
        raise SystemExit(msg)

    print(f"Measuring {len(rides):,} rides ...")
    units = _measure(state, edge_geom, edge_name, rides)
    rows = _stretches(units, MIN_PASSES, MIN_M, MIN_COVER)
    _print_coverage(units, rows, rides)
    _print_rows(
        f"Consistently fastest -- ranked by mean less one deviation, {MIN_PASSES}+ rides:",
        _ranked(rows, "floor", fastest=True),
        args.top,
    )
    _print_rows(
        "Consistently slowest -- ranked by mean plus one deviation:",
        _ranked(rows, "ceiling", fastest=False),
        args.top,
    )
    if args.sweep:
        _print_sweep(units)


if __name__ == "__main__":
    main()
