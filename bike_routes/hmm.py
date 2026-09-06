"""HMM map-matching via leuvenmapmatching (issue #11).

The heuristic matcher snaps each GPS point independently and routes between
snapped nodes, which oscillates between parallel ways and detours around
blocks (median matched/GPS length ratio ~2.9 before the dcf12f4 fixes).
A Viterbi matcher scores whole paths instead: candidate states per
observation with transition costs, decoded jointly, giving near-1.0 length
ratios on the same rides.

Matches that stop early (lattice dead-ends, typically off-network riding
or GPS teleports) are retried with a wider beam, then resumed past the
dead-end -- the unmatched stretch is counted as a skipped gap, mirroring
the heuristic's skip accounting.  Off-network stretches are fast-forwarded
with a cheap rtree test rather than a match attempt every few points.

The map index (nodes + adjacency) is cached to disk so repeat runs and
worker processes skip both the OSM graph load and the index build.
"""

from __future__ import annotations

import math
import pickle
from typing import TYPE_CHECKING, Any

from leuvenmapmatching.map.inmem import InMemMap
from leuvenmapmatching.matcher.distance import DistanceMatcher

from . import config

if TYPE_CHECKING:
    import networkx as nx
    import numpy as np

HMM_MAP_CACHE_FORMAT = "latlon-v2"  # v2: sidewalk-class edges filtered out

# A (lon, lat) point and an edge polyline of them.
Point = tuple[float, float]
Line = list[Point]


def _inmem_map_from_graph(
    graph: dict[int, tuple[tuple[float, float], list[int]]],
) -> InMemMap:
    """Wrap a prebuilt node/adjacency dict in an InMemMap.

    Passing the graph to the constructor bulk-loads the rtree; feeding the
    same data through add_node/add_edge instead does ~1.2M incremental rtree
    inserts (~2 minutes vs ~4 seconds for the NYC graph).
    """
    return InMemMap("osm", graph=graph, use_latlon=True, use_rtree=True, index_edges=True)


def _sidewalk_params() -> dict[str, Any]:
    """Return the filter settings a matching map was built with.

    Both the cached index and the processing-config hash key off this: the
    map cache is otherwise only invalidated by its format and the graph's
    mtime, so a changed threshold would rematch every ride against a map
    still filtered the old way.
    """
    return {
        "parallel_m": config.SIDEWALK_PARALLEL_M,
        "heading_deg": config.SIDEWALK_HEADING_DEG,
        "tags": sorted(config.SIDEWALK_TAGS),
        "street_tags": sorted(config.SIDEWALK_STREET_TAGS),
    }


def _canon(u: int, v: int) -> tuple[int, int]:
    """Canonical (min, max) node-pair key, as used everywhere else."""
    return (u, v) if u < v else (v, u)


def _bearing(a: Point, b: Point) -> float:
    """Bearing of a (lon, lat) segment in degrees, mod 180 (undirected)."""
    dx = (b[0] - a[0]) * config.M_PER_LON
    dy = (b[1] - a[1]) * config.M_PER_LAT
    return math.degrees(math.atan2(dx, dy)) % 180


def _point_seg_m(p: Point, a: Point, b: Point) -> float:
    """Distance in metres from (lon, lat) p to segment a-b."""
    px, py = p[0] * config.M_PER_LON, p[1] * config.M_PER_LAT
    ax, ay = a[0] * config.M_PER_LON, a[1] * config.M_PER_LAT
    dx = b[0] * config.M_PER_LON - ax
    dy = b[1] * config.M_PER_LAT - ay
    span = dx * dx + dy * dy
    t = 0.0 if span == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _edge_lines(G: nx.MultiDiGraph) -> dict[tuple[int, int], tuple[str, Line]]:
    """One highway tag and (lon, lat) polyline per canonical node pair."""
    lines: dict[tuple[int, int], list[tuple[float, float]]] = {}
    tags: dict[tuple[int, int], str] = {}
    for u, v, data in G.edges(data=True):
        key = _canon(int(u), int(v))
        hw = data.get("highway", "")
        if isinstance(hw, list):
            hw = hw[0] if hw else ""
        tags.setdefault(key, hw)
        if key in lines:
            continue
        if "geometry" in data:
            xs, ys = data["geometry"].xy
            lines[key] = list(zip(xs, ys))
        else:
            lines[key] = [
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"]),
            ]
    return {k: (tags[k], line) for k, line in lines.items()}


def _sidewalk_edges(G: nx.MultiDiGraph) -> set[tuple[int, int]]:
    """Canonical keys of footway/steps edges that a roadway runs beside.

    A sidewalk is defined by the roadway it accompanies -- parallel, and
    within config.SIDEWALK_PARALLEL_M for most of its length -- which is what
    separates it from a greenway, an esplanade or a park path.  Only actual
    roadways count as that accompaniment (config.SIDEWALK_STREET_TAGS); pier
    access ways run alongside the Hudson River Park Esplanade and a bridleway
    alongside Central Park's West Drive, so letting service ways or other
    paths vote classes both as sidewalk at every threshold.

    Roadway segments go into a fixed-cell grid whose cells are wider than the
    threshold, so the 3x3 neighbourhood around a point contains every segment
    that could be within it.
    """
    cell = 0.00035  # ~30m: comfortably wider than SIDEWALK_PARALLEL_M
    lines = _edge_lines(G)
    grid: dict[tuple[int, int], list[tuple[Point, Point, float]]] = {}
    for hw, line in lines.values():
        if hw not in config.SIDEWALK_STREET_TAGS:
            continue
        for a, b in zip(line, line[1:]):
            entry = (a, b, _bearing(a, b))
            for x, y in (a, b, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)):
                grid.setdefault((int(x / cell), int(y / cell)), []).append(entry)

    def parallel_dist(mid: Point, heading: float) -> float:
        """Distance to the nearest roughly-parallel roadway segment."""
        cx, cy = int(mid[0] / cell), int(mid[1] / cell)
        best = float("inf")
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for a, b, bearing in grid.get((cx + i, cy + j), ()):
                    off = abs(bearing - heading) % 180
                    if min(off, 180 - off) > config.SIDEWALK_HEADING_DEG:
                        continue
                    best = min(best, _point_seg_m(mid, a, b))
        return best

    sidewalks: set[tuple[int, int]] = set()
    for key, (hw, line) in lines.items():
        if hw not in config.SIDEWALK_TAGS:
            continue
        dists = sorted(
            parallel_dist(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), _bearing(a, b))
            for a, b in zip(line, line[1:])
        )
        if dists and dists[len(dists) // 2] <= config.SIDEWALK_PARALLEL_M:
            sidewalks.add(key)
    return sidewalks


def _build_inmem_map(G: nx.MultiDiGraph) -> InMemMap:
    """Build the leuvenmapmatching graph index from the OSM graph.

    Sidewalk-class edges are left out: see _sidewalk_edges.  Nodes are kept
    whatever happens to their edges, so the index still has one entry per
    graph node and the cache's size check stays meaningful.
    """
    print("Building HMM map index...")
    sidewalks = _sidewalk_edges(G)
    graph: dict[int, tuple[tuple[float, float], list[int]]] = {}
    for nid, data in G.nodes(data=True):
        graph[int(nid)] = ((data["y"], data["x"]), [])
    dropped = 0
    for u, v in G.edges():
        ui, vi = int(u), int(v)
        if _canon(ui, vi) in sidewalks:
            dropped += 1
            continue
        nbrs = graph[ui][1]
        if vi not in nbrs:
            nbrs.append(vi)
    mmap = _inmem_map_from_graph(graph)
    print(
        f"  {G.number_of_nodes():,} nodes indexed, "
        f"{len(sidewalks):,} sidewalk edges left out ({dropped:,} directed)"
    )
    return mmap


def _save_inmem_map_cache(mmap: InMemMap) -> None:
    """Persist the map index's node/adjacency dict for cheap reloads."""
    try:
        config.HMM_MAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with config.HMM_MAP_CACHE_PATH.open("wb") as f:
            pickle.dump(
                {
                    "format": HMM_MAP_CACHE_FORMAT,
                    "sidewalk": _sidewalk_params(),
                    "graph": mmap.graph,
                },
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )
    except Exception as exc:
        print(f"  Could not write {config.HMM_MAP_CACHE_PATH}: {exc}")


def _load_cached_inmem_map() -> InMemMap | None:
    """Load the map index from disk; None if missing or older than the graph cache."""
    path = config.HMM_MAP_CACHE_PATH
    if not path.exists():
        return None
    if (
        config.GRAPH_CACHE_PATH.exists()
        and path.stat().st_mtime < config.GRAPH_CACHE_PATH.stat().st_mtime
    ):
        return None
    try:
        with path.open("rb") as f:
            payload = pickle.load(f)
        if payload.get("format") != HMM_MAP_CACHE_FORMAT:
            return None
        if payload.get("sidewalk") != _sidewalk_params():
            return None
        return _inmem_map_from_graph(payload["graph"])
    except Exception:
        return None


def _new_matcher(mmap: InMemMap) -> DistanceMatcher:
    return DistanceMatcher(
        mmap,
        max_dist=config.HMM_MAX_DIST,
        max_dist_init=config.HMM_MAX_DIST,
        obs_noise=config.HMM_OBS_NOISE,
        obs_noise_ne=config.HMM_OBS_NOISE_NE,
        min_prob_norm=config.HMM_MIN_PROB_NORM,
        non_emitting_states=True,
        max_lattice_width=config.HMM_LATTICE_WIDTH,
    )


def _trim_overshoot(states: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Trim single-observation overshoot at either end of a matched pass.

    The first/last GPS point often projects exactly onto a node, tying with
    a perpendicular edge that the beam then picks, adding a spurious
    ~1-block spur.
    """
    if len(states) >= 2 and states[-1] != states[-2]:
        states = states[:-1]
    if len(states) >= 2 and states[0] != states[1]:
        states = states[1:]
    return states


def _map_match_ride_hmm(mmap: InMemMap, coords: np.ndarray) -> tuple[list[tuple[int, int]], int]:
    """Match one resampled ride; returns (canonical edge sequence, skipped gaps).

    Consecutive duplicate edges are collapsed (the lattice emits one state
    per observation); repeated traversals elsewhere in the ride survive,
    matching the heuristic's output shape.

    The full-segment wide-beam retry is deliberate: a windowed retry (keep
    the narrow-beam prefix, re-decode only around the dead end) was
    evaluated in July 2026 and rejected -- on rides where the narrow beam
    dead-ends, its path is measurably worse over the WHOLE ride (p90 length
    ratio 1.129 -> 1.167, one ride 1.002 -> 1.209), and the retry's full
    re-decode is what rescues it.  The speedup was only ~1.25x.
    """
    track: list[tuple[float, float]] = [(float(la), float(lo)) for la, lo in coords]
    edges: list[tuple[int, int]] = []
    skipped = 0
    pos = 0
    while pos < len(track) - 1:
        sub = track[pos:]
        states: list[tuple[int, int]] = []
        last_idx = 0
        try:
            matcher = _new_matcher(mmap)
            states, last_idx = matcher.match(sub)
            if (
                last_idx < len(sub) - 1
                and config.HMM_LATTICE_WIDTH_RETRY > config.HMM_LATTICE_WIDTH
            ):
                states, last_idx = matcher.increase_max_lattice_width(
                    config.HMM_LATTICE_WIDTH_RETRY
                )
        except Exception:  # a failed stretch becomes a skipped gap
            states, last_idx = [], 0
        for u, v in _trim_overshoot(states):
            if u == v:
                continue
            canon = (u, v) if u < v else (v, u)
            if not edges or edges[-1] != canon:
                edges.append(canon)
        if last_idx >= len(sub) - 1:
            break
        # Dead end: resume past it and count the gap.  Points with no
        # network within matching range (ferries, tunnels, out-of-town GPS)
        # are fast-forwarded with a cheap rtree test instead of paying a
        # failed match attempt every few points.
        pos += max(last_idx, 0) + config.HMM_FAIL_SKIP_POINTS
        while pos < len(track) - 1 and not _near_network(mmap, track[pos]):
            pos += 1
        skipped += 1
    return edges, skipped


def _near_network(mmap: InMemMap, loc: tuple[float, float]) -> bool:
    """Cheap test for any edge bounding box within matching range of loc.

    Conservative in the right direction: a miss guarantees no edge is within
    HMM_MAX_DIST (boxes contain their edges), a false positive just means one
    regular match attempt.
    """
    bb = mmap.box_around_point(loc, config.HMM_MAX_DIST)
    return next(mmap.rtree.intersection(bb), None) is not None


def _build_matcher_context(G: nx.MultiDiGraph, *, use_cache: bool = False) -> tuple[str, Any]:
    """Build the per-process matching context for the configured matcher.

    With use_cache, the HMM map index is loaded from disk when fresh, and
    written back after a rebuild so worker processes (and the next run) can
    load it without the OSM graph.
    """
    if config.MATCHER == "hmm":
        if use_cache:
            mmap = _load_cached_inmem_map()
            if mmap is not None and mmap.size() == G.number_of_nodes():
                return ("hmm", mmap)
        mmap = _build_inmem_map(G)
        if use_cache:
            _save_inmem_map_cache(mmap)
        return ("hmm", mmap)
    from .matching import _build_snap_tree  # noqa: PLC0415 -- avoid circular import

    return ("heuristic", _build_snap_tree(G))


def _match_one(
    G: nx.MultiDiGraph | None,
    ctx: tuple[str, Any],
    coords: np.ndarray,
    route_cache: dict[tuple[int, int], list[tuple[int, int]] | None],
) -> tuple[list[tuple[int, int]], int]:
    """Match one ride with whichever matcher the context was built for.

    G may be None for the HMM matcher (cache-only workers); the heuristic
    matcher requires the full graph for its routing fallback.
    """
    kind, data = ctx
    if kind == "hmm":
        return _map_match_ride_hmm(data, coords)
    from .matching import _map_match_ride  # noqa: PLC0415 -- avoid circular import

    return _map_match_ride(G, coords, config.SNAP_TOLERANCE_M, route_cache, *data)
