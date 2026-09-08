"""OSM street graph fetching, merging, and caching."""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING, Any

import networkx as nx
import numpy as np
import osmnx as ox
from shapely import affinity, wkt
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from . import config
from .cache import _graph_cache_valid, _write_cache_versions

if TYPE_CHECKING:
    from shapely.geometry.base import BaseGeometry


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


def _outside_runs(coords: np.ndarray) -> list[np.ndarray]:
    """Split one ride's [lat, lon] track into its runs outside config.NYC_BBOX.

    Each run keeps the in-box fix on either side of it, so the corridor built
    from it reaches the box rather than stopping one fix short and leaving
    the matcher a gap at the boundary.
    """
    lat_min, lon_min, lat_max, lon_max = config.NYC_BBOX
    inside = (
        (coords[:, 0] >= lat_min)
        & (coords[:, 0] <= lat_max)
        & (coords[:, 1] >= lon_min)
        & (coords[:, 1] <= lon_max)
    )
    keep = ~inside
    if not keep.any():
        return []
    keep[:-1] |= ~inside[1:]
    keep[1:] |= ~inside[:-1]
    idx = np.flatnonzero(keep)
    breaks = np.flatnonzero(np.diff(idx) > 1) + 1
    return [coords[run] for run in np.split(idx, breaks)]


def _corridor(rides: list[tuple[str, np.ndarray]]) -> BaseGeometry | None:
    """Buffer whatever the rides do outside the box, or None if they never leave.

    The tracks are buffered in a metre-scaled space and scaled back, rather
    than in degrees, so the corridor is the same width at Poughkeepsie as at
    the Battery.  Rides arrive resampled and already split at GPS gaps, so a
    run's fixes are metres apart and joining them cannot rope in a straight
    line across a gap the rider did not ride.
    """
    parts: list[BaseGeometry] = []
    for _, coords in rides:
        for run in _outside_runs(coords):
            xy = np.column_stack([run[:, 1] * config.M_PER_LON, run[:, 0] * config.M_PER_LAT])
            parts.append(LineString(xy) if len(xy) > 1 else Point(xy[0]))
    if not parts:
        return None
    # Each track is simplified and buffered on its own, and the union comes
    # last.  Unioning the tracks first nodes them at every self-crossing --
    # these are out-and-back rides, so they cross themselves constantly --
    # which doubles the vertex count instead of cutting it and leaves the
    # buffer minutes of work.  Simplified separately, 23,000 fixes become
    # ~600 vertices and the whole corridor builds in under a tenth of a
    # second.  Simplification runs before the buffer as well as after, so
    # each pass only ever cuts a corner the extra CORRIDOR_SIMPLIFY_M of
    # width puts back.
    slack = config.CORRIDOR_SIMPLIFY_M
    width = config.CORRIDOR_BUFFER_M + slack
    blobs = [track.simplify(slack).buffer(width) for track in parts]
    corridor = unary_union(blobs).simplify(slack)
    return affinity.scale(corridor, 1 / config.M_PER_LON, 1 / config.M_PER_LAT, origin=(0, 0))


def _fetch_region(rides: list[tuple[str, np.ndarray]]) -> BaseGeometry:
    """Return the area to fetch OSM for: the city box the rides reach, plus corridors.

    Fetching the rides' whole extent as one box would buy thousands of square
    kilometres of Hudson Valley nobody has ridden, and every rideable metre
    of it would land in the graph.  A ride that leaves the box brings back a
    corridor around its own track instead.
    """
    all_pts = np.vstack([c for _, c in rides])
    region = box(*_compute_bbox(all_pts))
    corridor = _corridor(rides)
    return region if corridor is None else unary_union([region, corridor])


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


def _fetch_graph(region: BaseGeometry) -> nx.MultiDiGraph:
    """Fetch OSM networks for the region, merge, and cache."""
    graphs = []
    for nt in config.NETWORK_TYPES:
        print(f"  Fetching OSM '{nt}' network...")
        g = ox.graph_from_polygon(region, network_type=nt, simplify=True)
        graphs.append(g)
        print(f"    {g.number_of_nodes():,} nodes, {g.number_of_edges():,} edges")

    G = nx.compose_all(graphs)
    n_removed = _remove_subsumed_edges(G)
    print(f"  Removed {n_removed:,} subsumed edges")
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    print(f"  Merged: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    config.GRAPH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.GRAPH_CACHE_PATH.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    _write_cache_versions()
    print(f"  Cached to {config.GRAPH_CACHE_PATH}")
    return G


def _region_km2(region: BaseGeometry) -> float:
    """Area of a lat/lon region in square kilometres, near enough to print."""
    return region.area * config.M_PER_LAT * config.M_PER_LON / 1e6


def _stored_region(state: dict[str, Any]) -> BaseGeometry | None:
    """Return the region a previous run fetched, or None if state names none.

    States written before the region existed carry only ``graph_bbox``; that
    box is exactly what was fetched, so it is read back as the region.
    """
    stored = state.get("graph_region")
    if stored:
        return wkt.loads(stored)
    bbox = state.get("graph_bbox")
    return box(*bbox) if bbox else None


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

    region = _stored_region(state)
    if new_rides:
        # Grow the stored region to cover the new rides; it never shrinks, so
        # a graph already fetched for a ride stays fetched for it.
        fresh = _fetch_region(new_rides)
        region = fresh if region is None else unary_union([region, fresh])
    if region is None:
        msg = "No rides and no stored region -- nothing to fetch a graph for"
        raise RuntimeError(msg)

    state["graph_region"] = region.wkt
    state["graph_bbox"] = region.bounds
    print(f"  Region: {_region_km2(region):,.0f} km2")

    for p in [config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH]:
        if p.exists():
            p.unlink()

    return _fetch_graph(region)
