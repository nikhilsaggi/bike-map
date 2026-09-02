"""Parallel-feature merging for the GeoJSON export."""

from __future__ import annotations

import math
from typing import Any

from . import config

# A per-ride traversal count is a [forward, reverse] pair, oriented to its own
# feature's stored vertex order.  Pairs, not totals, because merging needs
# direction (see _merge_ride_counts).
Pair = tuple[int, int]


def _chord(coords: list) -> tuple[float, float]:
    """Metric first-to-last vector of a line."""
    return (
        (coords[-1][0] - coords[0][0]) * config.M_PER_LON,
        (coords[-1][1] - coords[0][1]) * config.M_PER_LAT,
    )


def _opposed(a: list, b: list) -> bool:
    """Report whether two aligned lines are stored in opposite vertex order.

    Merging only ever compares near-parallel members, so the sign of the
    chord dot product is the whole question.  A ring's chord is ~zero and its
    sign meaningless; the dot product is then ~0, which reads as not opposed
    -- the conservative answer, since a wrong flip could only ever raise a
    count.
    """
    (ax, ay), (bx, by) = _chord(a), _chord(b)
    return ax * bx + ay * by < 0


def _merge_ride_counts(dst: dict[str, Pair], src: dict[str, Pair], *, flip: bool) -> None:
    """Fold src's per-ride passes into dst: max per direction, sum across them.

    The features merged here are parallel representations of one physical
    corridor -- a street and the bike lane beside it, 20 m apart.  Plain sum
    would turn a single pass drifting from one to the other into two
    traversals of a corridor it crossed once, the over-count this map must
    not invent.  Plain max fixed that but paid for it the other way: an
    out-and-back riding the lane north and the street south read as one, and
    on real rides that is the dominant error -- a 99%-retraced ride scored
    16% of its edges as repeated.

    Max *within* a direction and sum *across* the two settles both, because
    the two mistakes differ in direction and nothing else.  Drift is one
    direction recorded twice, so max keeps it at one.  An out-and-back is
    each direction recorded once, so the sum is two.  With every pass running
    one way this is exactly the old max rule.

    flip re-expresses src against dst's vertex order; the caller decides it
    with _opposed, since either feature's stored order is arbitrary.
    """
    for r, pair in src.items():
        f, v = (pair[1], pair[0]) if flip else (pair[0], pair[1])
        df, dv = dst.get(r, (0, 0))
        dst[r] = (max(df, f), max(dv, v))


def _ride_passes(pair: Pair) -> int:
    """Return the passes one ride made along one feature; at least 1.

    edge_speed leaves an unmeasured ride at (0, 0) so it cannot be attributed
    to a direction.  The floor lands here instead, once per feature per ride,
    which keeps the guarantee that measurement can raise a count but never
    take an edge off the map.
    """
    return max(1, pair[0] + pair[1])


def _ride_total(counts: dict[str, Pair]) -> int:
    """Total traversals a feature carries, across every ride."""
    return sum(_ride_passes(p) for p in counts.values())


def _covers(counts: dict[str, Pair], other: dict[str, Pair]) -> bool:
    """Report whether other already accounts for every traversal in counts.

    A ride other has never seen contributes nothing -- _ride_passes' floor
    applies to a ride that is present, so it must not be reached through a
    default, or an empty other would appear to cover everything.
    """
    return all(
        (_ride_passes(other[r]) if r in other else 0) >= _ride_passes(p) for r, p in counts.items()
    )


def _oriented(counts: dict[str, Pair], src_geom: list, dst_geom: list) -> dict[str, Pair]:
    """Copy counts, re-expressed against dst_geom's vertex order."""
    if not _opposed(src_geom, dst_geom):
        return dict(counts)
    return {r: (v, f) for r, (f, v) in counts.items()}


def _sample_line(coords: list) -> list[tuple[float, float, float]]:
    """Sample (x_m, y_m, heading_deg mod 180) every config.MERGE_SAMPLE_M along a line."""
    pts = [(c[0] * config.M_PER_LON, c[1] * config.M_PER_LAT) for c in coords]

    def head(x0: float, y0: float, x1: float, y1: float) -> float:
        return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0

    out = [(pts[0][0], pts[0][1], head(*pts[0], *pts[1]))]
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        h = head(x0, y0, x1, y1)
        d = config.MERGE_SAMPLE_M - carry
        while d <= seg:
            t = d / seg
            out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0), h))
            d += config.MERGE_SAMPLE_M
        carry = seg - (d - config.MERGE_SAMPLE_M)
    out.append((pts[-1][0], pts[-1][1], head(*pts[-2], *pts[-1])))
    return out


def _heading_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _sample_hits(
    samples: list[list[tuple[float, float, float]]],
) -> list[list[set[int]]]:
    """Find aligned neighbours for every sample of every feature.

    For each feature, for each of its samples, the set of OTHER features
    with an aligned sample within config.MERGE_TOL_M.
    """
    cell = config.MERGE_TOL_M
    grid: dict[tuple[int, int], list[tuple[int, float, float, float]]] = {}
    for i, pts in enumerate(samples):
        for x, y, h in pts:
            grid.setdefault((int(x // cell), int(y // cell)), []).append((i, x, y, h))
    tol_sq = config.MERGE_TOL_M * config.MERGE_TOL_M
    hits: list[list[set[int]]] = []
    for i, pts in enumerate(samples):
        rows = []
        for x, y, h in pts:
            gx, gy = int(x // cell), int(y // cell)
            hit: set[int] = set()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, jx, jy, jh in grid.get((gx + dx, gy + dy), ()):
                        if j == i or j in hit:
                            continue
                        ddx, ddy = jx - x, jy - y
                        if (
                            ddx * ddx + ddy * ddy <= tol_sq
                            and _heading_diff(h, jh) <= config.MERGE_HEADING_DEG
                        ):
                            hit.add(j)
            rows.append(hit)
        hits.append(rows)
    return hits


def _dense_point_grid(
    features: list[dict[str, Any]], cell: float, step: float = 4.0
) -> dict[tuple[int, int], list[tuple[int, float, float, float, float]]]:
    """Build a spatial grid over all feature geometries.

    Cells hold (feature_idx, x_m, y_m, lon, lat) points sampled every
    `step` m along every feature's geometry.
    """
    grid: dict[tuple[int, int], list[tuple[int, float, float, float, float]]] = {}
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        pts = [(c[0] * config.M_PER_LON, c[1] * config.M_PER_LAT) for c in coords]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            n = max(1, int(seg // step))
            for k in range(n):
                t = k / n
                x, y = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
                grid.setdefault((int(x // cell), int(y // cell)), []).append(
                    (i, x, y, x / config.M_PER_LON, y / config.M_PER_LAT)
                )
        x, y = pts[-1]
        grid.setdefault((int(x // cell), int(y // cell)), []).append(
            (i, x, y, coords[-1][0], coords[-1][1])
        )
    return grid


def _geom_len_m(coords: list[tuple[float, float]]) -> float:
    """Length in metres of a (lon, lat) coordinate sequence."""
    return sum(
        math.hypot((lon1 - lon0) * config.M_PER_LON, (lat1 - lat0) * config.M_PER_LAT)
        for (lon0, lat0), (lon1, lat1) in zip(coords, coords[1:])
    )


def _drop_redundant_rings(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop small closed-ring features whose rides all appear nearby.

    Map-matching a ride onto both ways of a parallel pair (roadway + bike
    lane) plus two crossing links yields a small closed box hanging off the
    corridor.  Rings add nothing -- merged ride sets already count their
    rides on the adjacent corridor -- but draw as boxy notches along
    avenues and greenways.  A ring is dropped only when every one of its
    rides appears on a non-ring feature within config.RING_NEAR_M, so no ride
    disappears from the map.
    """

    def is_ring(f: dict[str, Any]) -> bool:
        c = f["geometry"]["coordinates"]
        gap = math.hypot(
            (c[-1][0] - c[0][0]) * config.M_PER_LON, (c[-1][1] - c[0][1]) * config.M_PER_LAT
        )
        # Min length keeps short straight corridor pieces (whose endpoints
        # are trivially close together) from being mistaken for rings.
        return (
            gap <= config.RING_MAX_GAP_M
            and config.RING_MIN_LEN_M <= _geom_len_m(c) <= config.RING_MAX_LEN_M
        )

    ring_idx = {i for i, f in enumerate(features) if is_ring(f)}
    if not ring_idx:
        return features

    others = [f for i, f in enumerate(features) if i not in ring_idx]
    grid = _dense_point_grid(others, config.RING_NEAR_M)
    near_sq = config.RING_NEAR_M * config.RING_NEAR_M

    out = []
    dropped = 0
    for i, f in enumerate(features):
        if i not in ring_idx:
            out.append(f)
            continue
        rides = f["properties"]["_rides"]
        # Totals only: _covers compares floored per-ride passes, so which way
        # round a neighbour stores its geometry cannot matter here.
        covered: dict[str, Pair] = {}
        for x, y, _h in _sample_line(f["geometry"]["coordinates"]):
            gx, gy = int(x // config.RING_NEAR_M), int(y // config.RING_NEAR_M)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, jx, jy, _lon, _lat in grid.get((gx + dx, gy + dy), ()):
                        if (jx - x) ** 2 + (jy - y) ** 2 <= near_sq:
                            for r, pair in others[j]["properties"]["_rides"].items():
                                n = _ride_passes(pair)
                                if n > (_ride_passes(covered[r]) if r in covered else 0):
                                    covered[r] = (n, 0)
            if _covers(rides, covered):
                break
        if _covers(rides, covered):
            dropped += 1
        else:
            out.append(f)
    print(f"  Dropped {dropped:,} redundant ring features")
    return out


def _harmonize_representatives(features: list[dict[str, Any]]) -> None:
    """Swap cluster representatives to maximise endpoint continuity (in place).

    Per-cluster selection prefers the most-ridden member, which along a
    corridor of parallel ways can still alternate sides block by block --
    every alternation draws a lateral Z-jog.  Consecutive edges of the same
    physical way share graph nodes, so their endpoints match exactly.  Each
    feature re-picks from its recorded alternative geometries ("_alts") the
    one whose endpoints touch the most neighbouring features, iterating
    until stable so consistent chains propagate along the corridor.
    """

    def ekey(pt: list | tuple) -> tuple[float, float]:
        return (round(pt[0], 6), round(pt[1], 6))

    n_swapped = 0
    for _ in range(4):
        counts: dict[tuple[float, float], int] = {}
        for f in features:
            c = f["geometry"]["coordinates"]
            for end in (0, -1):
                k = ekey(c[end])
                counts[k] = counts.get(k, 0) + 1

        changed = 0
        for f in features:
            alts = f["properties"].get("_alts")
            if not alts:
                continue
            cur = f["geometry"]["coordinates"]

            def score(c: list, cur: list = cur, counts: dict = counts) -> int:
                s = 0
                for end in (0, -1):
                    k = ekey(c[end])
                    n = counts.get(k, 0)
                    if ekey(cur[end]) == k:
                        n -= 1  # don't count this feature's own endpoint
                    if n > 0:
                        s += 1
                return s

            best_c, best_s = cur, score(cur)
            for a in alts:
                if a is cur:
                    continue
                s = score(a)
                if s > best_s:
                    best_s, best_c = s, a
            if best_c is not cur:
                for end in (0, -1):
                    counts[ekey(cur[end])] -= 1
                    k = ekey(best_c[end])
                    counts[k] = counts.get(k, 0) + 1
                f["geometry"]["coordinates"] = best_c
                changed += 1
        n_swapped += changed
        if not changed:
            break
    print(f"  Harmonized {n_swapped:,} cluster representatives for continuity")


def _average_parallel_geometry(features: list[dict[str, Any]]) -> None:
    """Replace representatives with their cluster's lateral centerline (in place).

    A cluster of parallel ways is drawn with one member's geometry, so the
    line sits on whichever way was picked -- and where the pick changes
    along a corridor, the drawn line steps sideways.  Averaging each vertex
    with the nearest heading-aligned point on every other cluster member
    puts the line midway between the parallel ways instead.  The average is
    unweighted: adjacent clusters of the same physical ways then land on
    the same centerline regardless of their ride balance, and junction
    endpoints averaged from the same nodes coincide exactly.
    """
    tol_sq = config.MERGE_TOL_M * config.MERGE_TOL_M
    n_avg = 0
    for f in features:
        cluster = f["properties"].get("_cluster")
        if not cluster or len(cluster) < 2:
            continue
        base = f["geometry"]["coordinates"]
        others = [g for g in cluster if g is not base]
        if not others:
            continue
        other_samples = [_sample_line(g) for g in others]
        pts = [(c[0] * config.M_PER_LON, c[1] * config.M_PER_LAT) for c in base]
        new_coords = []
        pulled = False
        for vi, (x, y) in enumerate(pts):
            x0, y0 = pts[max(vi - 1, 0)]
            x1, y1 = pts[min(vi + 1, len(pts) - 1)]
            hdg = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0
            acc_x, acc_y, k = x, y, 1
            for samples in other_samples:
                best = None
                best_d = tol_sq
                for sx, sy, sh in samples:
                    if _heading_diff(hdg, sh) > config.MERGE_HEADING_DEG:
                        continue
                    d = (sx - x) ** 2 + (sy - y) ** 2
                    if d < best_d:
                        best_d = d
                        best = (sx, sy)
                if best is not None:
                    acc_x += best[0]
                    acc_y += best[1]
                    k += 1
            if k > 1:
                pulled = True
            new_coords.append(
                (round(acc_x / k / config.M_PER_LON, 6), round(acc_y / k / config.M_PER_LAT, 6))
            )
        if pulled:
            f["geometry"]["coordinates"] = new_coords
            n_avg += 1
    print(f"  Averaged {n_avg:,} representatives onto cluster centerlines")


def _snap_endpoints(features: list[dict[str, Any]]) -> None:
    """Reconnect merged features at junctions (in place).

    Merging keeps one geometry per parallel cluster, so adjacent features
    along a corridor can come from different parallel ways offset 10-20m
    laterally, leaving gaps and jogs at junctions.  Any endpoint that is
    not already touching another feature is bridged to the nearest point
    on a neighbouring feature within config.MERGE_SNAP_M.  Short gaps (up to
    config.MERGE_MOVE_M) move the endpoint itself, slightly rotating the last
    segment; longer gaps append a connector segment so the line's true
    geometry is not distorted -- an appended right-angle elbow on a short
    gap reads as a notch in the corridor.
    """
    cell = config.MERGE_SNAP_M
    grid = _dense_point_grid(features, cell)

    snap_sq = config.MERGE_SNAP_M * config.MERGE_SNAP_M
    connect_sq = config.MERGE_CONNECT_M * config.MERGE_CONNECT_M
    move_sq = config.MERGE_MOVE_M * config.MERGE_MOVE_M
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        for end in (0, -1):
            lon, lat = coords[end][0], coords[end][1]
            x, y = lon * config.M_PER_LON, lat * config.M_PER_LAT
            gx, gy = int(x // cell), int(y // cell)
            best = None
            best_d = snap_sq
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, jx, jy, jlon, jlat in grid.get((gx + dx, gy + dy), ()):
                        if j == i:
                            continue
                        d = (jx - x) ** 2 + (jy - y) ** 2
                        if d < best_d:
                            best_d = d
                            best = (jlon, jlat)
            if best is not None and best_d > connect_sq:
                new_pt = (round(best[0], 6), round(best[1], 6))
                if best_d <= move_sq and len(coords) > 2:
                    coords[end] = new_pt
                elif end == 0:
                    coords.insert(0, new_pt)
                else:
                    coords.append(new_pt)


def _merge_parallel_features(
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge GeoJSON features representing the same physical road corridor.

    Parallel OSM ways (divided carriageways, protected bike lanes, footways
    alongside roads) get map-matched separately, drawing 2+ lines 10-20m
    apart for the same street.  Merging is driven by heading-aligned sample
    coverage: fraction of one feature's points lying within config.MERGE_TOL_M of
    the other's.  Adjacent segments along a street have near-zero mutual
    coverage, so transitive chains cannot form the way endpoint matching
    allowed.

    Phase 1 clusters features that mutually cover each other and keeps a
    minimal set of member geometries covering the cluster extent.  Phase 2
    drops features almost entirely covered by the union of the others
    (e.g. long ways spanning several already-covered blocks).

    Each input feature must carry properties["_rides"]: how many times each
    ride traversed that edge, keyed by ride id.  Merging keeps the larger
    count per ride (_merge_ride_counts), so a corridor can never claim more
    traversals of itself than any one of its parallel members recorded, no
    matter how features combine.  Returns finalized features with
    ride_count/rides and no _rides.
    """
    tol_sq = config.MERGE_TOL_M * config.MERGE_TOL_M
    samples = [_sample_line(f["geometry"]["coordinates"]) for f in features]
    hits = _sample_hits(samples)

    # covered[i][j] = number of i's samples matched by feature j
    covered: list[dict[int, int]] = []
    for rows in hits:
        row_counts: dict[int, int] = {}
        for hit in rows:
            for j in hit:
                row_counts[j] = row_counts.get(j, 0) + 1
        covered.append(row_counts)

    def cov(i: int, j: int) -> float:
        return covered[i].get(j, 0) / len(samples[i])

    # -- Phase 1: cluster mutually-covering features (Union-Find) --
    parent = list(range(len(features)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    n_pairs = 0
    for i, row_counts in enumerate(covered):
        for j in row_counts:
            if (
                j > i
                and cov(i, j) >= config.MERGE_MUTUAL_COV
                and cov(j, i) >= config.MERGE_MUTUAL_COV
            ):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
                n_pairs += 1

    clusters: dict[int, list[int]] = {}
    for i in range(len(features)):
        clusters.setdefault(find(i), []).append(i)

    def line_len(i: int) -> float:
        pts = samples[i]
        return math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])

    merged: list[dict[str, Any]] = []
    for members in clusters.values():
        # Accumulate in the first member's frame, then re-express the total
        # for whichever members are kept -- their vertex orders are unrelated.
        ref = features[members[0]]["geometry"]["coordinates"]
        rides: dict[str, Pair] = {}
        for i in members:
            geom = features[i]["geometry"]["coordinates"]
            _merge_ride_counts(rides, features[i]["properties"]["_rides"], flip=_opposed(ref, geom))
        alts: list[list] = []
        if len(members) == 1:
            keep = members
        else:
            # Greedy set-cover: keep member geometries until config.MERGE_KEEP_COV of
            # the cluster's sampled extent lies within config.MERGE_TOL_M of a kept
            # one.  Tight clusters keep a single line; staggered fragment
            # chains at junctions keep 2-3 instead of losing extent.
            all_pts = [(x, y) for i in members for (x, y, _h) in samples[i]]
            cov_flags = [False] * len(all_pts)
            keep = []
            # Prefer the most-ridden member as the kept geometry: rides stay
            # on the same physical way through consecutive blocks, so this
            # picks laterally-consistent representatives along a corridor
            # (keeping the longest instead makes adjacent blocks alternate
            # between parallel ways, fragmenting the drawn line).
            remaining = sorted(
                members,
                key=lambda m: (_ride_total(features[m]["properties"]["_rides"]), line_len(m)),
                reverse=True,
            )

            def mark(m: int, all_pts: list, cov_flags: list) -> int:
                gained = 0
                for k, (x, y) in enumerate(all_pts):
                    if cov_flags[k]:
                        continue
                    for jx, jy, _jh in samples[m]:
                        if (jx - x) ** 2 + (jy - y) ** 2 <= tol_sq:
                            cov_flags[k] = True
                            gained += 1
                            break
                return gained

            while remaining and (
                not keep or sum(cov_flags) / len(cov_flags) < config.MERGE_KEEP_COV
            ):
                m = remaining.pop(0)
                if mark(m, all_pts, cov_flags) == 0 and keep:
                    continue
                keep.append(m)

            if len(keep) == 1:
                # Alternative representatives for the continuity pass: any
                # member that alone covers the cluster extent as well as
                # the chosen one could stand in for it.
                for m in members:
                    flags = [False] * len(all_pts)
                    if mark(m, all_pts, flags) / len(all_pts) >= config.MERGE_KEEP_COV:
                        alts.append(features[m]["geometry"]["coordinates"])
                if len(alts) < 2:
                    alts = []
        cluster_geoms = (
            [features[i]["geometry"]["coordinates"] for i in members] if len(members) > 1 else []
        )
        # Phase 1 builds fresh feature dicts, so the street name has to be
        # carried across explicitly.  Take the most-ridden named member: the
        # kept geometry can be an unnamed service way running alongside the
        # named street the rides actually belong to.
        cluster_name = next(
            (
                nm
                for m in sorted(
                    members,
                    key=lambda m: _ride_total(features[m]["properties"]["_rides"]),
                    reverse=True,
                )
                if (nm := features[m]["properties"].get("_name"))
            ),
            None,
        )
        merged.extend(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": features[m]["geometry"]["coordinates"],
                },
                "properties": {
                    "_rides": _oriented(rides, ref, features[m]["geometry"]["coordinates"]),
                    "_alts": alts,
                    "_cluster": cluster_geoms,
                    "_name": cluster_name,
                },
            }
            for m in keep
        )

    # -- Phase 2: absorb spans redundant with the union of other features --
    m_samples = [_sample_line(f["geometry"]["coordinates"]) for f in merged]
    m_hits = _sample_hits(m_samples)
    m_covered: list[dict[int, int]] = []
    for rows in m_hits:
        row_counts = {}
        for hit in rows:
            for j in hit:
                row_counts[j] = row_counts.get(j, 0) + 1
        m_covered.append(row_counts)

    active = [True] * len(merged)
    n_absorbed = 0
    # Absorb low-ridership features first: a busy corridor must never be
    # dropped in favour of a near-empty parallel path that happens to cover
    # it -- by the time a busy feature is considered, its low-count cover
    # has already been absorbed, so the busy feature survives.
    order = sorted(
        range(len(merged)),
        key=lambda i: (_ride_total(merged[i]["properties"]["_rides"]), -len(m_samples[i])),
    )

    def union_cov(i: int) -> float:
        """Fraction of i's samples covered by comparably-ridden active features.

        Near-empty parallel paths must not count as cover for a busy
        corridor -- absorbing the corridor would leave its street drawn
        only by an almost-invisible low-count line.
        """
        min_rides = _ride_total(merged[i]["properties"]["_rides"]) / 2
        rows = m_hits[i]
        n = sum(
            1
            for hit in rows
            if any(
                active[j] and _ride_total(merged[j]["properties"]["_rides"]) >= min_rides
                for j in hit
            )
        )
        return n / len(rows)

    for i in order:
        if union_cov(i) < config.MERGE_ABSORB_COV:
            continue
        receivers = [
            j
            for j in {j for hit in m_hits[i] for j in hit if active[j]}
            if m_covered[j].get(i, 0) / len(m_samples[j]) >= config.MERGE_TRANSFER_COV
        ]
        if not receivers:
            continue
        active[i] = False
        n_absorbed += 1
        for j in receivers:
            _merge_ride_counts(
                merged[j]["properties"]["_rides"],
                merged[i]["properties"]["_rides"],
                flip=_opposed(
                    merged[j]["geometry"]["coordinates"], merged[i]["geometry"]["coordinates"]
                ),
            )
            if not merged[j]["properties"].get("_name"):
                merged[j]["properties"]["_name"] = merged[i]["properties"].get("_name")

    # Restore pass: absorbing feature B can erode the cover that justified
    # absorbing feature A earlier, leaving a hole in the drawn corridor.
    # Reactivate any absorbed feature no longer covered by the final set.
    while True:
        restored = 0
        for i in order:
            if active[i]:
                continue
            if union_cov(i) < config.MERGE_ABSORB_COV:
                active[i] = True
                n_absorbed -= 1
                restored += 1
        if not restored:
            break

    survivors = [f for i, f in enumerate(merged) if active[i]]
    _harmonize_representatives(survivors)
    _average_parallel_geometry(survivors)
    # Snap first: the box artifacts only become closed rings once their
    # endpoints are bridged to the corridor.  Snap again after dropping so
    # endpoints that had bridged onto a dropped ring re-attach to the
    # corridor (a moved endpoint can also invalidate another endpoint's
    # attachment; the second pass heals those too).
    _snap_endpoints(survivors)
    survivors = _drop_redundant_rings(survivors)
    _snap_endpoints(survivors)

    out = []
    for f in survivors:
        rides = f["properties"].pop("_rides")
        f["properties"].pop("_alts", None)
        f["properties"].pop("_cluster", None)
        f["properties"]["ride_count"] = _ride_total(rides)
        # Sorted ride filenames, one entry per traversal; the export maps them
        # to ride indices, and repeats survive as repeats so the page's count
        # is passes rather than distinct rides.
        f["properties"]["rides"] = sorted(
            r for r, pair in rides.items() for _ in range(_ride_passes(pair))
        )
        out.append(f)

    print(
        f"  Merged parallel features: {len(features):,} -> {len(out):,} "
        f"({n_pairs:,} mutual pairs, {n_absorbed:,} redundant spans absorbed)"
    )
    return out


def _audit_merge(features: list[dict[str, Any]]) -> None:
    """Print post-merge regression metrics.

    Residual duplicate pairs: features >= 30m that still mutually cover each
    other (should have been merged).  Dangling endpoints: endpoints near
    another feature (within config.MERGE_SNAP_M) but not touching it (beyond
    config.MERGE_CONNECT_M) -- broken corridor joins.  Healthy baseline as of
    2026-07: ~50 duplicate pairs (mostly cluster siblings converged onto
    the same centerline by averaging), ~0.0% dangling.
    """
    samples = [_sample_line(f["geometry"]["coordinates"]) for f in features]
    hits = _sample_hits(samples)
    covered: list[dict[int, int]] = []
    for rows in hits:
        row_counts: dict[int, int] = {}
        for hit in rows:
            for j in hit:
                row_counts[j] = row_counts.get(j, 0) + 1
        covered.append(row_counts)

    def length_m(i: int) -> float:
        pts = [
            (c[0] * config.M_PER_LON, c[1] * config.M_PER_LAT)
            for c in features[i]["geometry"]["coordinates"]
        ]
        return sum(math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:]))

    lens = [length_m(i) for i in range(len(features))]
    dup_pairs = 0
    dup_km = 0.0
    for i, row_counts in enumerate(covered):
        for j, n in row_counts.items():
            if j <= i or min(lens[i], lens[j]) < 30.0:
                continue
            ci = n / len(samples[i])
            cj = covered[j].get(i, 0) / len(samples[j])
            if ci >= config.MERGE_MUTUAL_COV and cj >= config.MERGE_MUTUAL_COV:
                dup_pairs += 1
                dup_km += min(lens[i], lens[j]) / 1000

    grid = _dense_point_grid(features, config.MERGE_SNAP_M)
    snap_sq = config.MERGE_SNAP_M * config.MERGE_SNAP_M
    connect_sq = config.MERGE_CONNECT_M * config.MERGE_CONNECT_M
    dangling = 0
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        for end in (0, -1):
            x = coords[end][0] * config.M_PER_LON
            y = coords[end][1] * config.M_PER_LAT
            gx, gy = int(x // config.MERGE_SNAP_M), int(y // config.MERGE_SNAP_M)
            best = snap_sq
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, jx, jy, _jlon, _jlat in grid.get((gx + dx, gy + dy), ()):
                        if j == i:
                            continue
                        d = (jx - x) ** 2 + (jy - y) ** 2
                        best = min(best, d)
            if connect_sq < best < snap_sq:
                dangling += 1

    total_ends = 2 * len(features)
    pct = 100 * dangling / total_ends if total_ends else 0.0
    print(
        f"  Audit: {dup_pairs:,} residual duplicate pairs ({dup_km:.1f} km), "
        f"{dangling:,}/{total_ends:,} dangling endpoints ({pct:.1f}%)"
    )
    if dup_pairs > 100 or pct > 2.0:
        print("  WARNING: merge regression suspected -- metrics well above baseline")
