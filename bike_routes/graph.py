"""OSM street graph fetching, merging, and caching."""

from __future__ import annotations

import pickle
from typing import Any

import networkx as nx
import numpy as np
import osmnx as ox

from . import config
from .cache import _graph_cache_valid, _write_cache_versions


def _compute_bbox(all_pts: np.ndarray) -> tuple[float, float, float, float]:
    """Compute bounding box from ride coordinates, clamped to config.NYC_BBOX."""
    lats, lons = all_pts[:, 0], all_pts[:, 1]
    bbox_lat_min, bbox_lon_min, bbox_lat_max, bbox_lon_max = config.NYC_BBOX

    lat_min = max(float(lats.min()), bbox_lat_min)
    lat_max = min(float(lats.max()), bbox_lat_max)
    lon_min = max(float(lons.min()), bbox_lon_min)
    lon_max = min(float(lons.max()), bbox_lon_max)

    buf = 0.005
    return (lon_min - buf, lat_min - buf, lon_max + buf, lat_max + buf)
def _remove_subsumed_edges(G: nx.MultiDiGraph) -> int:
    """Remove edges whose geometry passes through intermediate graph nodes.

    When composing separately-simplified networks (bike/drive/walk), a street
    simplified as one long edge A->D in the drive network may have intermediate
    nodes B, C added by the bike/walk network.  The composed graph then has
    both A->D and A->B, B->C, C->D -- the long edge's geometry overlaps the
    shorter edges, causing visual duplicates on the map.

    Only removes an edge when ALL sub-edges in the chain through intermediate
    nodes actually exist, so the graph stays fully connected.
    """
    node_pos: dict[tuple[float, float], set[int]] = {}
    for n, data in G.nodes(data=True):
        key = (round(data["x"], 7), round(data["y"], 7))
        node_pos.setdefault(key, set()).add(n)

    to_remove = []
    for u, v, k, data in G.edges(data=True, keys=True):
        geom = data.get("geometry")
        if geom is None:
            continue
        coords = list(geom.coords)
        if len(coords) <= 2:
            continue
        chain = [u]
        for coord in coords[1:-1]:
            rounded = (round(coord[0], 7), round(coord[1], 7))
            hit = node_pos.get(rounded, set()) - {u, v}
            if hit:
                chain.append(next(iter(hit)))
        if len(chain) < 2:
            continue
        chain.append(v)
        if all(G.has_edge(a, b) for a, b in zip(chain[:-1], chain[1:])):
            to_remove.append((u, v, k))

    G.remove_edges_from(to_remove)
    return len(to_remove)
def _fetch_graph(bbox: tuple[float, float, float, float]) -> nx.MultiDiGraph:
    """Fetch OSM networks for bbox, merge, and cache."""
    graphs = []
    for nt in config.NETWORK_TYPES:
        print(f"  Fetching OSM '{nt}' network...")
        g = ox.graph_from_bbox(bbox, network_type=nt, simplify=True)
        graphs.append(g)
        print(f"    {g.number_of_nodes():,} nodes, {g.number_of_edges():,} edges")

    G = nx.compose_all(graphs)
    n_removed = _remove_subsumed_edges(G)
    print(f"  Removed {n_removed:,} subsumed edges")
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    print(f"  Merged: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    with config.GRAPH_CACHE_PATH.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    _write_cache_versions()
    print(f"  Cached to {config.GRAPH_CACHE_PATH}")
    return G
def _load_graph(new_rides: list[tuple[str, np.ndarray]], state: dict[str, Any]) -> nx.MultiDiGraph:
    """Load graph from cache or fetch from OSM."""
    if config.GRAPH_CACHE_PATH.exists() and _graph_cache_valid():
        print(f"Loading graph from {config.GRAPH_CACHE_PATH}...")
        try:
            with config.GRAPH_CACHE_PATH.open("rb") as f:
                G = pickle.load(f)
        except Exception as exc:
            print(f"  Graph cache unreadable ({exc!r}) -- refetching from OSM")
            config.GRAPH_CACHE_PATH.unlink()
        else:
            print(f"  {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
            return G

    print("Fetching OSM graph...")

    if state.get("graph_bbox") and not new_rides:
        bbox = state["graph_bbox"]
    elif state.get("graph_bbox"):
        # Expand stored bbox to cover new rides
        bbox = list(state["graph_bbox"])
        new_pts = np.vstack([c for _, c in new_rides])
        bbox[0] = min(bbox[0], float(new_pts[:, 1].min()) - 0.005)
        bbox[1] = min(bbox[1], float(new_pts[:, 0].min()) - 0.005)
        bbox[2] = max(bbox[2], float(new_pts[:, 1].max()) + 0.005)
        bbox[3] = max(bbox[3], float(new_pts[:, 0].max()) + 0.005)
        bbox = tuple(bbox)
    else:
        # First run: all rides are new, compute bbox from them
        all_pts = np.vstack([c for _, c in new_rides])
        bbox = _compute_bbox(all_pts)

    state["graph_bbox"] = bbox

    for p in [config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH]:
        if p.exists():
            p.unlink()

    return _fetch_graph(bbox)
