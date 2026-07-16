"""Map-matching GPS traces to street edges, with parallel workers."""

from __future__ import annotations

import os
import pickle
from collections import ChainMap
from concurrent.futures import ProcessPoolExecutor
from typing import TYPE_CHECKING, Any

import numpy as np
import osmnx as ox
from scipy.spatial import cKDTree

from . import config
from .cache import _load_route_cache
from .gps import haversine_m

if TYPE_CHECKING:
    import networkx as nx


def _path_to_edges(
    G: nx.MultiDiGraph,
    path_nodes: list[int],
    max_dist: float,
    straight_dist: float = 0.0,
) -> list[tuple[int, int]] | None:
    """Convert path nodes to canonical edge pairs in a single pass.

    Returns edge list or None if cumulative length exceeds max_dist or
    the detour ratio (route length / straight-line distance) exceeds
    MAX_ROUTE_DETOUR.
    """
    max_by_detour = straight_dist * config.MAX_ROUTE_DETOUR if straight_dist > 0 else float("inf")
    length = 0.0
    result = []
    for a, b in zip(path_nodes[:-1], path_nodes[1:]):
        canon = (min(a, b), max(a, b))
        edge_data = G[canon[0]][canon[1]] if G.has_edge(canon[0], canon[1]) else G[a][b]
        key = min(edge_data, key=lambda k: edge_data[k].get("length", 0))
        length += edge_data[key].get("length", 0)
        if length > max_dist or length > max_by_detour:
            return None
        result.append(canon)
    return result
def _build_snap_tree(
    G: nx.MultiDiGraph,
) -> tuple[
    cKDTree,
    np.ndarray,
    float,
    np.ndarray,
    np.ndarray,
    dict[int, int],
    dict[int, set[int]],
    dict[tuple[int, int], float],
]:
    """Build a cKDTree and adjacency data for edge-based snapping.

    Long edges (>150m) get virtual intermediate points so bridge/highway
    GPS points can find the edge even when endpoints are far away.
    """
    node_data = list(G.nodes(data=True))
    node_ids = np.array([n[0] for n in node_data])
    lats = np.array([n[1]["y"] for n in node_data])
    lons = np.array([n[1]["x"] for n in node_data])
    R = 6_371_000
    mean_lat_rad = np.radians(lats.mean())
    cos_mlr = np.cos(mean_lat_rad)
    real_xs = np.radians(lons) * R * cos_mlr
    real_ys = np.radians(lats) * R
    node_idx = {int(nid): i for i, nid in enumerate(node_ids)}
    adj = {}
    for u, v in G.edges():
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)

    edge_hw = {}
    for u, v, data in G.edges(data=True):
        hw = data.get("highway", "")
        p = min(config.HW_PENALTY.get(h, 0) for h in hw) if isinstance(hw, list) else config.HW_PENALTY.get(hw, 0)
        for key in ((u, v), (v, u)):
            if key not in edge_hw or p < edge_hw[key]:
                edge_hw[key] = p

    extra_x, extra_y, extra_nid = [], [], []
    seen = set()
    for u, v in G.edges():
        canon = (min(u, v), max(u, v))
        if canon in seen:
            continue
        seen.add(canon)
        ui, vi = node_idx.get(int(u)), node_idx.get(int(v))
        if ui is None or vi is None:
            continue
        dx = real_xs[ui] - real_xs[vi]
        dy = real_ys[ui] - real_ys[vi]
        length = (dx * dx + dy * dy) ** 0.5
        if length < config.DENSIFY_M:
            continue
        n_pts = int(length / config.DENSIFY_M)
        for j in range(1, n_pts + 1):
            t = j / (n_pts + 1)
            extra_x.append(real_xs[ui] + t * (real_xs[vi] - real_xs[ui]))
            extra_y.append(real_ys[ui] + t * (real_ys[vi] - real_ys[ui]))
            extra_nid.append(u)

    if extra_x:
        tree_xs = np.concatenate([real_xs, np.array(extra_x)])
        tree_ys = np.concatenate([real_ys, np.array(extra_y)])
        tree_nids = np.concatenate([node_ids, np.array(extra_nid)])
        print(f"  Densified {len(extra_x):,} virtual points on {len(seen):,} long edges")
    else:
        tree_xs = real_xs
        tree_ys = real_ys
        tree_nids = node_ids

    tree = cKDTree(np.column_stack([tree_xs, tree_ys]))
    return tree, tree_nids, mean_lat_rad, real_xs, real_ys, node_idx, adj, edge_hw
def _map_match_ride(
    G: nx.MultiDiGraph,
    coords: np.ndarray,
    snap_tol: float,
    route_cache: dict[tuple[int, int], list[tuple[int, int]] | None],
    snap_tree: cKDTree,
    tree_node_ids: np.ndarray,
    mean_lat_rad: float,
    node_xs: np.ndarray,
    node_ys: np.ndarray,
    node_idx_map: dict[int, int],
    adj: dict[int, set[int]],
    edge_hw: dict[tuple[int, int], float],
) -> tuple[list[tuple[int, int]], int]:
    """Snap points to nearest OSM edges, route between them."""
    lats, lons = coords[:, 0], coords[:, 1]
    R = 6_371_000
    cos_mlr = np.cos(mean_lat_rad)
    q_xs = np.radians(lons) * R * cos_mlr
    q_ys = np.radians(lats) * R

    K = 5
    snap_dists, snap_idxs = snap_tree.query(np.column_stack([q_xs, q_ys]), k=K)
    snap_tol_sq = snap_tol * snap_tol

    # Precompute GPS headings for heading-aware snapping
    _math_deg = np.degrees
    _math_atan2 = np.arctan2
    _math_sqrt = np.sqrt
    gps_headings = np.empty(len(coords))
    gps_heading_valid = np.zeros(len(coords), dtype=bool)
    for i in range(len(coords)):
        j0 = max(0, i - 3)
        j1 = min(len(coords) - 1, i + 3)
        dx = q_xs[j1] - q_xs[j0]
        dy = q_ys[j1] - q_ys[j0]
        if dx * dx + dy * dy > 25:
            gps_headings[i] = _math_deg(_math_atan2(dx, dy)) % 360
            gps_heading_valid[i] = True

    deduped = []
    hp = config.HEADING_PENALTY
    for i in range(len(coords)):
        best_score = float(snap_tol)
        best_node = None
        px, py = q_xs[i], q_ys[i]
        use_hdg = gps_heading_valid[i]
        gps_hdg = gps_headings[i]

        for k in range(K):
            if snap_dists[i, k] > snap_tol * 1.5:
                break
            if best_score < 3:
                break
            nd = tree_node_ids[snap_idxs[i, k]]
            nd_ridx = node_idx_map.get(int(nd))
            if nd_ridx is None:
                continue
            nd_x, nd_y = node_xs[nd_ridx], node_ys[nd_ridx]

            for neighbor in adj.get(nd, ()):
                nb_idx = node_idx_map.get(neighbor)
                if nb_idx is None:
                    continue
                nb_x, nb_y = node_xs[nb_idx], node_ys[nb_idx]
                ex, ey = nb_x - nd_x, nb_y - nd_y
                len_sq = ex * ex + ey * ey
                if len_sq < 0.01:
                    d_sq = (px - nd_x) ** 2 + (py - nd_y) ** 2
                else:
                    t = ((px - nd_x) * ex + (py - nd_y) * ey) / len_sq
                    if t < 0:
                        t = 0
                    elif t > 1:
                        t = 1
                    cx = nd_x + t * ex
                    cy = nd_y + t * ey
                    d_sq = (px - cx) ** 2 + (py - cy) ** 2
                if d_sq > snap_tol_sq:
                    continue
                d = _math_sqrt(d_sq)
                score = d + edge_hw.get((nd, neighbor), 0)
                if use_hdg and len_sq > 1:
                    eb = _math_deg(_math_atan2(ex, ey)) % 360
                    diff = abs(gps_hdg - eb) % 360
                    if diff > 180:
                        diff = 360 - diff
                    if diff > 90:
                        diff = 180 - diff
                    score += hp * diff
                if score < best_score:
                    best_score = score
                    d_nd_sq = (px - nd_x) ** 2 + (py - nd_y) ** 2
                    d_nb_sq = (px - nb_x) ** 2 + (py - nb_y) ** 2
                    best_node = nd if d_nd_sq < d_nb_sq else neighbor

        if best_node is None:
            continue
        if not deduped or best_node != deduped[-1]:
            deduped.append(best_node)

    if len(deduped) < 2:
        return [], 0

    # Remove short loops (A→...→A) caused by parallel footways/alleys,
    # but only when detour nodes stay close to anchor (preserves forward progress)
    cleaned = []
    lw = config.LOOP_WINDOW
    detour_sq = config.LOOP_MAX_DETOUR_M * config.LOOP_MAX_DETOUR_M
    for node in deduped:
        found_loop = False
        for k in range(len(cleaned) - 1, max(len(cleaned) - lw, -1), -1):
            if cleaned[k] == node:
                found_loop = True
                ar = node_idx_map.get(int(node))
                if ar is not None:
                    ax, ay = node_xs[ar], node_ys[ar]
                    too_far = False
                    for mn in cleaned[k + 1 :]:
                        mr = node_idx_map.get(int(mn))
                        if mr is not None:
                            dx = node_xs[mr] - ax
                            dy = node_ys[mr] - ay
                            if dx * dx + dy * dy > detour_sq:
                                too_far = True
                                break
                    if not too_far:
                        del cleaned[k + 1 :]
                else:
                    del cleaned[k + 1 :]
                break
        if not found_loop:
            cleaned.append(node)
    deduped = cleaned

    if len(deduped) < 2:
        return [], 0

    # Phase 1: classify pairs
    _PENDING = object()
    pairs: list[list[tuple[int, int, int]] | None | object] = []
    pending_idx = []
    origs, dests = [], []

    for i in range(len(deduped) - 1):
        u, v = deduped[i], deduped[i + 1]
        canon = (min(u, v), max(u, v))

        if G.has_edge(u, v):
            pairs.append([(canon[0], canon[1])])
        elif canon in route_cache:
            pairs.append(route_cache[canon])
        else:
            straight = haversine_m(
                G.nodes[u]["y"],
                G.nodes[u]["x"],
                G.nodes[v]["y"],
                G.nodes[v]["x"],
            )
            if straight > config.MAX_ROUTING_DISTANCE_M:
                pairs.append(None)
            else:
                pairs.append(_PENDING)
                pending_idx.append(i)
                origs.append(u)
                dests.append(v)

    # Phase 2: batch-route pending pairs (igraph backend)
    if pending_idx:
        paths = ox.shortest_path(G, origs, dests, weight="length", cpus=1)
        for idx, path in zip(pending_idx, paths):
            u, v = deduped[idx], deduped[idx + 1]
            canon = (min(u, v), max(u, v))
            straight = float(
                haversine_m(G.nodes[u]["y"], G.nodes[u]["x"], G.nodes[v]["y"], G.nodes[v]["x"])
            )
            result = (
                None
                if path is None
                else _path_to_edges(G, path, config.MAX_ROUTING_DISTANCE_M, straight)
            )
            route_cache[canon] = result
            pairs[idx] = result

    # Phase 3: bridge across failed segments
    #
    # When routing between consecutive nodes A→B fails (no path or
    # excessive detour), the node sequence may contain a "bad" node
    # that's on a disconnected subnetwork (e.g. a footway parallel to
    # a road).  Instead of leaving a gap, try to connect the last good
    # node directly to the node after the failed one.
    def _try_route(u: int, v: int) -> list[tuple[int, int]] | None:
        canon = (min(u, v), max(u, v))
        if G.has_edge(u, v):
            return [(canon[0], canon[1])]
        if canon in route_cache:
            return route_cache[canon]
        straight = float(
            haversine_m(G.nodes[u]["y"], G.nodes[u]["x"], G.nodes[v]["y"], G.nodes[v]["x"])
        )
        if straight > config.MAX_ROUTING_DISTANCE_M:
            return None
        try:
            path = ox.shortest_path(G, u, v, weight="length", cpus=1)
        except Exception:
            path = None
        result = (
            None if path is None else _path_to_edges(G, path, config.MAX_ROUTING_DISTANCE_M, straight)
        )
        route_cache[canon] = result
        return result

    edges: list[tuple[int, int]] = []
    skipped = 0
    last_good = deduped[0]
    i = 0
    while i < len(pairs):
        src = deduped[i]
        dst = deduped[i + 1]
        r = pairs[i]
        if r is not None and r is not _PENDING and last_good == src:
            edges.extend(r)
            last_good = dst
            i += 1
            continue
        # Either the pre-computed route failed, or we haven't reached
        # deduped[i] (last_good != src).  Try to bridge from last_good
        # to subsequent nodes.
        bridged = False
        for j in range(i, min(i + 4, len(pairs))):
            target = deduped[j + 1]
            if target == last_good:
                i = j + 1
                bridged = True
                break
            bridge = _try_route(last_good, target)
            if bridge is not None:
                edges.extend(bridge)
                last_good = target
                i = j + 1
                bridged = True
                break
        if not bridged:
            skipped += 1
            i += 1

    return edges, skipped
_worker_graph_ctx: tuple[Any, ...] | None = None
_worker_route_cache: dict[tuple[int, int], list[tuple[int, int]] | None] | None = None
def _match_worker_init() -> None:
    """Load the graph and build the matcher context once per worker process."""
    from .hmm import _build_matcher_context  # noqa: PLC0415 -- avoid circular import

    global _worker_graph_ctx, _worker_route_cache  # noqa: PLW0603 -- pool initializer pattern
    with config.GRAPH_CACHE_PATH.open("rb") as f:
        G = pickle.load(f)
    _worker_graph_ctx = (G, _build_matcher_context(G))
    _worker_route_cache = _load_route_cache()
def _match_chunk(
    chunk: list[tuple[str, np.ndarray]],
) -> tuple[
    list[tuple[str, list[tuple[int, int]], int]],
    dict[tuple[int, int], list[tuple[int, int]] | None],
]:
    """Match one chunk of rides in a worker.

    Returns per-ride results and the route-cache entries first computed
    during this chunk (always empty for the HMM matcher).
    """
    from .hmm import _match_one  # noqa: PLC0415 -- avoid circular import

    assert _worker_graph_ctx is not None
    assert _worker_route_cache is not None
    G, ctx = _worker_graph_ctx
    new_entries: dict[tuple[int, int], list[tuple[int, int]] | None] = {}
    cache = ChainMap(new_entries, _worker_route_cache)
    results = []
    for fname, coords in chunk:
        edges, skipped = _match_one(G, ctx, coords, cache)
        results.append((fname, edges, skipped))
    _worker_route_cache.update(new_entries)
    return results, new_entries
def _match_worker_count(n_rides: int) -> int:
    """Worker processes to use for map matching.

    Overridable via the MATCH_WORKERS env var (1 = sequential).  Parallel
    matching only pays off when many rides amortize each worker's graph
    load (tens of seconds and ~1-2 GB RAM per worker), so small batches
    run sequentially and workers are capped at 4.
    """
    env = os.environ.get("MATCH_WORKERS")
    if env is not None:
        return max(1, int(env))
    if n_rides < config.MATCH_PARALLEL_MIN_RIDES or not config.GRAPH_CACHE_PATH.exists():
        return 1
    return min(4, os.cpu_count() or 1)
def _match_rides_parallel(
    new_rides: list[tuple[str, np.ndarray]],
    route_cache: dict[tuple[int, int], list[tuple[int, int]] | None],
    n_workers: int,
) -> list[tuple[str, list[tuple[int, int]], int]]:
    """Map-match rides across worker processes.

    Route-cache entries are merged into `route_cache` as chunks complete.
    """
    chunks = [
        new_rides[i : i + config.MATCH_CHUNK_SIZE]
        for i in range(0, len(new_rides), config.MATCH_CHUNK_SIZE)
    ]
    results: list[tuple[str, list[tuple[int, int]], int]] = []
    with ProcessPoolExecutor(max_workers=n_workers, initializer=_match_worker_init) as pool:
        for chunk_results, new_entries in pool.map(_match_chunk, chunks):
            route_cache.update(new_entries)
            results.extend(chunk_results)
            print(
                f"  {len(results)}/{len(new_rides)} rides "
                f"(route cache: {len(route_cache):,} entries)"
            )
    return results
