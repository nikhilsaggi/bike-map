"""Direction-split per-edge traversal speed, backfilled from raw GPS timestamps.

The matching pipeline throws timestamps away (gps.py reads only lon/lat and
resamples by distance), so speed is recovered here in a separate pass that
never re-runs the matcher: for each processed ride we already know which
edges it used (state["edge_rides"]), so the raw trace only has to be
projected onto that ride's own ~58 edges rather than the whole network.

CHUNKING
--------
Speed is accumulated per ~config.SPEED_CHUNK_M chunk along an edge, not per
edge.  OSM ways are not uniform: the Manhattan Bridge bike path is a single
2163 m edge, and measured end to end its climb cancels its descent exactly
(10.37 vs 10.23 mph -- no signal at all).  Chunking recovers the gradient.
The median edge is 63 m and stays a single chunk, so this costs nothing for
the street grid.

ORIENTATION INVARIANT
---------------------
"Forward" means travel along the stored coordinate order of
edge_geom[key] -- NOT from min(u,v) to max(u,v).  render.py builds
edge_geom from whichever parallel directed edge was shortest, so roughly a
tenth of geometries run backwards relative to their canonical node key.
Anchoring to the node key would silently invert those against the line
actually drawn on the map.  Each record stores the chord bearing of the
geometry it was measured against, so a consumer can detect a rebuilt,
flipped render cache and reverse both the chunk order and the buckets.

Backfill state lives outside cache._processing_config(): adding or changing
anything here must never invalidate the config hash and force a rematch.
Use config.SPEED_VERSION to recompute instead.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from . import config
from .cache import _save_state
from .ride_stats import _parse_ride_timestamp

# One chunk record: [f_dist, f_time, f_moving, f_n, r_dist, r_time, r_moving, r_n].
# All eight combine by addition, so folding order cannot affect the result.
_FWD = 0
_REV = 4
_CHUNK_W = 8

# A stored edge record is {"b": chord bearing when measured, "c": [chunk, ...]}.


def _new_chunk() -> list[float]:
    """Return a zeroed chunk record."""
    return [0.0] * _CHUNK_W


def _swap_dirs(chunk: list[float]) -> list[float]:
    """Return a copy of a chunk with its forward and reverse buckets exchanged."""
    return [*chunk[_REV : _REV + 4], *chunk[_FWD : _FWD + 4]]


def _chord_bearing(coords: list[tuple[float, float]]) -> float:
    """Bearing in degrees of a (lon, lat) line's first->last chord, in the metric frame."""
    x0, y0 = coords[0][0] * config.M_PER_LON, coords[0][1] * config.M_PER_LAT
    x1, y1 = coords[-1][0] * config.M_PER_LON, coords[-1][1] * config.M_PER_LAT
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0


def _bearing_flipped(stored: float, current: float) -> bool:
    """Report whether two bearings point more than 90 degrees apart (anti-parallel)."""
    d = abs(stored - current) % 360.0
    return min(d, 360.0 - d) > 90.0


def _n_chunks(length_m: float) -> int:
    """Chunks an edge of this length is divided into.

    A pure function of the geometry, so every ride folding into the same edge
    agrees on the layout no matter what order they are processed in.
    """
    return max(1, min(config.SPEED_MAX_CHUNKS, round(length_m / config.SPEED_CHUNK_M)))


def _load_ride_track(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Load a ride CSV as ((N,2) lat/lon array, (N,) epoch seconds).

    Returns None when the file is unreadable, has under two rows, or has
    timestamps that ride_stats._parse_ride_timestamp cannot interpret.
    """
    lats: list[float] = []
    lons: list[float] = []
    times: list[float] = []
    try:
        with path.open() as f:
            next(f)  # header
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) < 3:
                    continue
                dt = _parse_ride_timestamp(parts[2])
                if dt is None:
                    continue
                lons.append(float(parts[0]))
                lats.append(float(parts[1]))
                times.append(dt.timestamp())
    except Exception:
        return None
    if len(lats) < 2:
        return None
    return np.column_stack([np.array(lats), np.array(lons)]), np.array(times)


def _densify(coords: list[tuple[float, float]]) -> tuple[list[tuple[float, float]], list[float]]:
    """Sample a (lon, lat) line every config.SPEED_SAMPLE_M in the metric frame.

    Returns (points_xy, along_m) where along_m is each sample's distance from
    the line's start along the polyline.
    """
    pts = [(c[0] * config.M_PER_LON, c[1] * config.M_PER_LAT) for c in coords]
    out_xy = [pts[0]]
    out_along = [0.0]
    total = 0.0
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        d = config.SPEED_SAMPLE_M - carry
        while d <= seg:
            t = d / seg
            out_xy.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            out_along.append(total + d)
            d += config.SPEED_SAMPLE_M
        carry = seg - (d - config.SPEED_SAMPLE_M)
        total += seg
    out_xy.append(pts[-1])
    out_along.append(total)
    return out_xy, out_along


def _build_index(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    keys: list[tuple[int, int]],
) -> tuple[cKDTree, np.ndarray, np.ndarray, np.ndarray, list[tuple[int, int]], list[float]] | None:
    """Build a sample-point KD-tree over the given edges.

    Returns (tree, sample_xy, slot_of_sample, along_of_sample, slot_keys,
    slot_length_m), or None when no edge has usable geometry.
    """
    xs: list[tuple[float, float]] = []
    slots: list[int] = []
    alongs: list[float] = []
    slot_keys: list[tuple[int, int]] = []
    slot_len: list[float] = []
    for key in keys:
        coords = edge_geom.get(key)
        if not coords or len(coords) < 2:
            continue
        pts, along = _densify(coords)
        if along[-1] <= 0:
            continue
        slot = len(slot_keys)
        slot_keys.append(key)
        slot_len.append(along[-1])
        xs.extend(pts)
        alongs.extend(along)
        slots.extend([slot] * len(pts))
    if not xs:
        return None
    arr = np.array(xs)
    return (
        cKDTree(arr),
        arr,
        np.array(slots),
        np.array(alongs),
        slot_keys,
        slot_len,
    )


def _assign(
    tree: cKDTree,
    sample_xy: np.ndarray,
    sample_slot: np.ndarray,
    sample_along: np.ndarray,
    track_xy: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Assign each fix to an edge slot and an exact along-position.

    Hysteresis keeps a fix on the previous fix's edge when that edge is nearly
    as close as the best one, which stops a trace from oscillating between a
    bike lane and its parallel roadway.  The along-position is refined by
    projecting onto the chord to the neighbouring sample, removing the
    +/-SPEED_SAMPLE_M/2 quantization that would otherwise blur chunk edges.
    Slot is -1 where no edge sample lies within config.SPEED_SNAP_M.
    """
    k = min(4, len(sample_slot))
    dists, idxs = tree.query(track_xy, k=k)
    if k == 1:
        dists = dists[:, None]
        idxs = idxs[:, None]

    n = len(track_xy)
    slot_out = np.full(n, -1, dtype=np.int64)
    along_out = np.zeros(n)
    prev = -1
    for i in range(n):
        best_d = dists[i, 0]
        if not np.isfinite(best_d) or best_d > config.SPEED_SNAP_M:
            prev = -1
            continue
        pick = 0
        if prev >= 0:
            limit = best_d * config.SPEED_HYSTERESIS
            for c in range(k):
                if dists[i, c] > limit:
                    break
                if sample_slot[idxs[i, c]] == prev:
                    pick = c
                    break
        j = int(idxs[i, pick])
        slot = int(sample_slot[j])

        # Refine along by projecting onto the chord to an adjacent sample of
        # the same edge; samples are SPEED_SAMPLE_M apart, so this is a good
        # local approximation of the polyline.
        nb = -1
        if j + 1 < len(sample_slot) and sample_slot[j + 1] == slot:
            nb = j + 1
        elif j - 1 >= 0 and sample_slot[j - 1] == slot:
            nb = j - 1
        along = sample_along[j]
        if nb >= 0:
            dx = sample_xy[nb, 0] - sample_xy[j, 0]
            dy = sample_xy[nb, 1] - sample_xy[j, 1]
            seg_sq = dx * dx + dy * dy
            if seg_sq > 0:
                t = ((track_xy[i, 0] - sample_xy[j, 0]) * dx) + (
                    (track_xy[i, 1] - sample_xy[j, 1]) * dy
                )
                t = min(1.0, max(0.0, t / seg_sq))
                along = sample_along[j] + t * (sample_along[nb] - sample_along[j])

        slot_out[i] = slot
        along_out[i] = along
        prev = slot
    return slot_out, along_out


def _runs(
    slot: np.ndarray,
    times: np.ndarray,
    track_xy: np.ndarray,
) -> list[tuple[int, int, int]]:
    """Split a fix-by-fix assignment into contiguous single-edge runs.

    Returns [(slot, start_index, stop_index)] with stop exclusive.  A run is
    cut when the edge changes, a fix is unassigned, the recording gap exceeds
    config.SPEED_MAX_FIX_GAP_S (auto-pause, a stop, signal loss), or the fixes
    jump more than config.MAX_GPS_GAP_M apart.
    """
    out: list[tuple[int, int, int]] = []
    n = len(slot)
    i = 0
    while i < n:
        if slot[i] < 0:
            i += 1
            continue
        s = slot[i]
        j = i + 1
        while j < n and slot[j] == s:
            dt = times[j] - times[j - 1]
            if dt <= 0 or dt > config.SPEED_MAX_FIX_GAP_S:
                break
            step = math.hypot(
                track_xy[j, 0] - track_xy[j - 1, 0],
                track_xy[j, 1] - track_xy[j - 1, 1],
            )
            if step > config.MAX_GPS_GAP_M:
                break
            j += 1
        if j - i >= 2:
            out.append((int(s), i, j))
        i = max(j, i + 1)
    return out


def _split_monotonic(along: np.ndarray, i: int, j: int, tol: float) -> list[tuple[int, int]]:
    """Split [i, j) into maximal runs of consistent along-line direction.

    A rider who turns around mid-street traverses one edge twice in one
    continuous run of fixes; measured end to end that is zero net displacement
    and would be discarded, losing both passes.  Reversals shorter than tol are
    GPS wobble and are ignored; a real turnaround cuts the run at the extremum
    it reached.
    """
    out: list[tuple[int, int]] = []
    start = i
    d = 0
    ext_i = i
    for k in range(i + 1, j):
        v = along[k]
        if d == 0:
            if abs(v - along[start]) >= tol:
                d = 1 if v > along[start] else -1
                ext_i = k
            continue
        if (v - along[ext_i]) * d > 0:
            ext_i = k
        elif abs(v - along[ext_i]) >= tol:
            if ext_i > start:
                out.append((start, ext_i + 1))
            start = ext_i
            d = 1 if v > along[ext_i] else -1
            ext_i = k
    if j - 1 > start:
        out.append((start, j))
    return out


def _fold_ride(
    state: dict[str, Any],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    keys: list[tuple[int, int]],
    path: Path,
) -> int:
    """Measure one ride's edge traversals into state["edge_speed"]. Returns runs folded."""
    track = _load_ride_track(path)
    if track is None:
        return 0
    latlon, times = track
    index = _build_index(edge_geom, keys)
    if index is None:
        return 0
    tree, sample_xy, sample_slot, sample_along, slot_keys, slot_len = index

    track_xy = np.column_stack([latlon[:, 1] * config.M_PER_LON, latlon[:, 0] * config.M_PER_LAT])
    slot, along = _assign(tree, sample_xy, sample_slot, sample_along, track_xy)
    edge_speed = state["edge_speed"]
    folded = 0

    raw_runs = _runs(slot, times, track_xy)
    passes = [
        (s, a, b)
        for s, i, j in raw_runs
        for a, b in _split_monotonic(along, i, j, config.SPEED_REVERSAL_M)
    ]

    for s, i, j in passes:
        length = slot_len[s]
        dist = abs(along[j - 1] - along[i])
        dt = times[j - 1] - times[i]
        if dt <= 0 or dist <= 0:
            continue
        if 3.6 * dist / dt > config.SPEED_MAX_KMH:
            continue
        if dist < min(config.SPEED_MIN_PASS_M, 0.5 * length):
            continue

        key = slot_keys[s]
        nchunk = _n_chunks(length)
        rec = edge_speed.get(key)
        if rec is None or len(rec["c"]) != nchunk:
            rec = {"b": _chord_bearing(edge_geom[key]), "c": [_new_chunk() for _ in range(nchunk)]}
            edge_speed[key] = rec
        chunks = rec["c"]
        # Direction is decided once for the whole run, so GPS wobble inside a
        # run cannot flip individual steps into the opposite bucket.
        base = _FWD if along[j - 1] > along[i] else _REV
        touched: set[int] = set()
        for a, b in zip(range(i, j - 1), range(i + 1, j)):
            step_dt = times[b] - times[a]
            step_d = abs(along[b] - along[a])
            # Per-step guard, not just per-run: a fix joining the edge from a
            # side street can snap to the start and then jump tens of metres
            # along, which would otherwise land as an implausible sprint in
            # whichever chunk owns the edge's first metres.
            if step_dt <= 0 or 3.6 * step_d / step_dt > config.SPEED_MAX_KMH:
                continue
            mid = 0.5 * (along[a] + along[b])
            ci = min(nchunk - 1, max(0, int(mid / length * nchunk)))
            c = chunks[ci]
            c[base] += step_d
            c[base + 1] += step_dt
            if 3.6 * step_d / step_dt >= config.SPEED_MOVING_KMH:
                c[base + 2] += step_dt
            touched.add(ci)
        for ci in touched:
            chunks[ci][base + 3] += 1
        folded += 1
    return folded


def _records_well_formed(edge_speed: object) -> bool:
    """Sanity-check stored records against the current layout.

    SPEED_VERSION is the intended invalidation lever, but a format change
    that forgets to bump it would otherwise be read as valid and corrupt the
    map silently.  Checking one record is cheap and fails closed.
    """
    if not edge_speed:
        return True
    if not isinstance(edge_speed, dict):
        return False
    rec = next(iter(edge_speed.values()))
    return (
        isinstance(rec, dict)
        and isinstance(rec.get("c"), list)
        and bool(rec["c"])
        and len(rec["c"][0]) == _CHUNK_W
    )


def _backfill_edge_speeds(
    state: dict[str, Any],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
) -> int:
    """Measure per-edge speeds for processed rides that don't have them yet.

    Mirrors ride_stats._backfill_ride_stats: incremental, keyed on rides
    already folded in, and safe to interrupt (checkpointed).  Rides whose CSV
    is missing on disk are skipped and retried next run.
    """
    if state.get("speed_version") != config.SPEED_VERSION or not _records_well_formed(
        state.get("edge_speed")
    ):
        state["edge_speed"] = {}
        state["speed_rides"] = set()
        state["speed_version"] = config.SPEED_VERSION
    state.setdefault("edge_speed", {})
    done: set[str] = state.setdefault("speed_rides", set())

    pending = sorted(state["processed_files"] - done)
    if not pending:
        return 0
    pending_set = set(pending)
    ride_edges: dict[str, list[tuple[int, int]]] = {}
    for key, rides in state.get("edge_rides", {}).items():
        for r in rides:
            if r in pending_set:
                ride_edges.setdefault(r, []).append(key)

    n = 0
    for fname in pending:
        path = Path(config.RIDES_FOLDER) / fname
        if not path.exists():
            continue
        keys = ride_edges.get(fname)
        if keys:
            _fold_ride(state, edge_geom, keys, path)
        # Mark done even when the ride yielded nothing, so it isn't retried.
        done.add(fname)
        n += 1
        if n % config.CHECKPOINT_EVERY_RIDES == 0:
            print(f"    ... measured {n:,}/{len(pending):,} rides")
            _save_state(state)
    return n


def _chunk_speed_kmh(chunk: list[float], base: int) -> float | None:
    """Return the published speed for one direction of a chunk, or None below threshold."""
    dist, time_s, _moving, n = chunk[base : base + 4]
    if n < config.SPEED_MIN_PASSES or dist < config.SPEED_MIN_DIST_M or time_s <= 0:
        return None
    return 3.6 * dist / time_s


def _speed_summary(edge_speed: dict[tuple[int, int], dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate chunk speeds into the map's stable colour domain."""
    speeds = [
        v
        for rec in edge_speed.values()
        for chunk in rec["c"]
        for base in (_FWD, _REV)
        if (v := _chunk_speed_kmh(chunk, base)) is not None
    ]
    if not speeds:
        return None
    arr = np.array(speeds)
    return {
        "lo": round(float(np.percentile(arr, 10)), 1),
        "med": round(float(np.percentile(arr, 50)), 1),
        "hi": round(float(np.percentile(arr, 90)), 1),
        "n": len(speeds),
        "split_n": config.SPEED_SPLIT_PASSES,
    }


def _chunk_slices(coords: list[tuple[float, float]], n: int) -> list[list[tuple[float, float]]]:
    """Cut a (lon, lat) line into n contiguous equal-length slices.

    Slices share their boundary vertices, so the drawn corridor is visually
    identical to the undivided line.  Degenerate slices (a boundary landing
    on an existing vertex) are never emitted; if the cut cannot produce n
    usable pieces the whole line is returned unsplit.
    """
    if n <= 1 or len(coords) < 2:
        return [list(coords)]
    seg = [
        math.hypot(
            (b[0] - a[0]) * config.M_PER_LON,
            (b[1] - a[1]) * config.M_PER_LAT,
        )
        for a, b in zip(coords, coords[1:])
    ]
    total = sum(seg)
    if total <= 0:
        return [list(coords)]

    bounds = [k * total / n for k in range(1, n)]
    out: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = [coords[0]]
    acc = 0.0
    bi = 0
    for idx, (a, b) in enumerate(zip(coords, coords[1:])):
        length = seg[idx]
        if length == 0:
            continue
        while bi < len(bounds) and bounds[bi] <= acc + length:
            t = (bounds[bi] - acc) / length
            pt = (
                round(a[0] + t * (b[0] - a[0]), 6),
                round(a[1] + t * (b[1] - a[1]), 6),
            )
            if pt != cur[-1]:
                cur.append(pt)
            if len(cur) >= 2:
                out.append(cur)
                cur = [pt]
            bi += 1
        if b != cur[-1]:
            cur.append(b)
        acc += length
    if len(cur) >= 2:
        out.append(cur)
    return out if len(out) == n else [list(coords)]


def _oriented_chunks(
    rec: dict[str, Any] | None, coords: list[tuple[float, float]]
) -> list[list[float]] | None:
    """Return a record's chunks re-expressed against the given geometry.

    Speed was measured against the geometry as it stood then.  If the render
    cache has since been rebuilt with the opposite vertex order, both the
    chunk order and the direction buckets have to reverse so that "forward"
    still means "along the line we are about to draw".
    """
    if not rec or not rec.get("c"):
        return None
    chunks = rec["c"]
    if _bearing_flipped(rec["b"], _chord_bearing(coords)):
        return [_swap_dirs(c) for c in reversed(chunks)]
    return [list(c) for c in chunks]
