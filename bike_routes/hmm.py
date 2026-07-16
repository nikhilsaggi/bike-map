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
the heuristic's skip accounting.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from leuvenmapmatching.map.inmem import InMemMap
from leuvenmapmatching.matcher.distance import DistanceMatcher

from . import config

if TYPE_CHECKING:
    import networkx as nx
    import numpy as np


def _build_inmem_map(G: nx.MultiDiGraph) -> InMemMap:
    """Build the leuvenmapmatching graph index from the OSM graph."""
    print("Building HMM map index...")
    mmap = InMemMap("osm", use_latlon=True, use_rtree=True, index_edges=True)
    for nid, data in G.nodes(data=True):
        mmap.add_node(int(nid), (data["y"], data["x"]))
    for u, v in G.edges():
        mmap.add_edge(int(u), int(v))
    print(f"  {G.number_of_nodes():,} nodes indexed")
    return mmap


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


def _map_match_ride_hmm(
    mmap: InMemMap, coords: np.ndarray
) -> tuple[list[tuple[int, int]], int]:
    """Match one resampled ride; returns (canonical edge sequence, skipped gaps).

    Consecutive duplicate edges are collapsed (the lattice emits one state
    per observation); repeated traversals elsewhere in the ride survive,
    matching the heuristic's output shape.
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
            if last_idx < len(sub) - 1 and config.HMM_LATTICE_WIDTH_RETRY > config.HMM_LATTICE_WIDTH:
                states, last_idx = matcher.increase_max_lattice_width(
                    config.HMM_LATTICE_WIDTH_RETRY
                )
        except Exception:  # a failed stretch becomes a skipped gap
            states, last_idx = [], 0
        # Trim single-observation overshoot at either end: the first/last GPS
        # point often projects exactly onto a node, tying with a perpendicular
        # edge that the beam then picks, adding a spurious ~1-block spur.
        if len(states) >= 2 and states[-1] != states[-2]:
            states = states[:-1]
        if len(states) >= 2 and states[0] != states[1]:
            states = states[1:]
        for u, v in states:
            if u == v:
                continue
            canon = (u, v) if u < v else (v, u)
            if not edges or edges[-1] != canon:
                edges.append(canon)
        if last_idx >= len(sub) - 1:
            break
        # Dead end: resume past it and count the gap
        pos += max(last_idx, 0) + config.HMM_FAIL_SKIP_POINTS
        skipped += 1
    return edges, skipped


def _build_matcher_context(G: nx.MultiDiGraph) -> tuple[str, Any]:
    """Build the per-process matching context for the configured matcher."""
    if config.MATCHER == "hmm":
        return ("hmm", _build_inmem_map(G))
    from .matching import _build_snap_tree  # noqa: PLC0415 -- avoid circular import

    return ("heuristic", _build_snap_tree(G))


def _match_one(
    G: nx.MultiDiGraph,
    ctx: tuple[str, Any],
    coords: np.ndarray,
    route_cache: dict[tuple[int, int], list[tuple[int, int]] | None],
) -> tuple[list[tuple[int, int]], int]:
    """Match one ride with whichever matcher the context was built for."""
    kind, data = ctx
    if kind == "hmm":
        return _map_match_ride_hmm(data, coords)
    from .matching import _map_match_ride  # noqa: PLC0415 -- avoid circular import

    return _map_match_ride(G, coords, config.SNAP_TOLERANCE_M, route_cache, *data)
