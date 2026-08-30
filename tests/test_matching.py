"""Tests for graph handling and map-matching on synthetic street grids."""

from __future__ import annotations

import networkx as nx
import numpy as np
from conftest import add_street, lonlat
from shapely.geometry import LineString

from bike_routes import config, graph, matching


def _match(G, coords, route_cache=None):
    snap_data = matching._build_snap_tree(G)
    return matching._map_match_ride(
        G,
        coords,
        config.SNAP_TOLERANCE_M,
        route_cache if route_cache is not None else {},
        *snap_data,
    )


def _trace(points_m):
    """Build a (lat, lon) coords array from local meter offsets."""
    out = []
    for x, y in points_m:
        lon, lat = lonlat(x, y)
        out.append((lat, lon))
    return np.array(out)


# -- _path_to_edges --------------------------------------------------------


def test_path_to_edges_canonical(grid_graph):
    edges = matching._path_to_edges(grid_graph, [0, 1, 2], max_dist=1000)
    assert edges == [(0, 1), (1, 2)]


def test_path_to_edges_reversed_path_canonicalizes(grid_graph):
    edges = matching._path_to_edges(grid_graph, [2, 1, 0], max_dist=1000)
    assert edges == [(1, 2), (0, 1)]


def test_path_to_edges_exceeds_max_dist(grid_graph):
    # Two 100m blocks > 150m limit
    assert matching._path_to_edges(grid_graph, [0, 1, 2], max_dist=150) is None


# -- _remove_subsumed_edges --------------------------------------------------


def _chain_graph():
    """Three collinear nodes 1-2-3 with short edges between them."""
    G = nx.MultiDiGraph()
    for i, x in enumerate([0.0, 100.0, 200.0], start=1):
        lon, lat = lonlat(x, 0.0)
        G.add_node(i, x=lon, y=lat)
    add_street(G, 1, 2)
    add_street(G, 2, 3)
    return G


def test_remove_subsumed_edge():
    G = _chain_graph()
    geom = LineString([(G.nodes[i]["x"], G.nodes[i]["y"]) for i in (1, 2, 3)])
    G.add_edge(1, 3, length=200.0, highway="residential", geometry=geom)
    n = graph._remove_subsumed_edges(G)
    assert n == 1
    assert not G.has_edge(1, 3)
    assert G.has_edge(1, 2)
    assert G.has_edge(2, 3)


def test_keeps_edge_when_midpoint_is_not_a_node():
    G = _chain_graph()
    # Geometry passes through a point that is NOT a graph node
    mid = lonlat(150.0, 5.0)
    geom = LineString([(G.nodes[1]["x"], G.nodes[1]["y"]), mid, (G.nodes[3]["x"], G.nodes[3]["y"])])
    G.add_edge(1, 3, length=210.0, highway="residential", geometry=geom)
    n = graph._remove_subsumed_edges(G)
    assert n == 0
    assert G.has_edge(1, 3)


# -- Map-matching ------------------------------------------------------------


def test_match_straight_street(grid_graph):
    # Ride east along the bottom row, offset 3m north of the centerline
    coords = _trace([(x, 3.0) for x in range(0, 401, 20)])
    edges, skipped = _match(grid_graph, coords)
    assert skipped == 0
    assert edges == [(0, 1), (1, 2), (2, 3), (3, 4)]


def test_match_turn(grid_graph):
    # East two blocks along row 0, then north two blocks up column 2
    east = [(x, 2.0) for x in range(0, 201, 20)]
    north = [(202.0, float(y)) for y in range(20, 201, 20)]
    coords = _trace(east + north)
    edges, skipped = _match(grid_graph, coords)
    assert skipped == 0
    assert edges == [(0, 1), (1, 2), (2, 12), (12, 22)]


def test_match_routes_across_gap():
    """Non-adjacent snapped nodes are connected via shortest-path routing."""
    G = nx.MultiDiGraph()
    for i, x in enumerate([0.0, 100.0, 200.0], start=1):
        lon, lat = lonlat(x, 0.0)
        G.add_node(i, x=lon, y=lat)
    add_street(G, 1, 2)
    add_street(G, 2, 3)

    coords = _trace([(0.0, 0.0), (200.0, 0.0)])  # only endpoints, 200m apart
    route_cache = {}
    edges, skipped = _match(G, coords, route_cache)
    assert skipped == 0
    assert edges == [(1, 2), (2, 3)]
    assert route_cache[(1, 3)] == [(1, 2), (2, 3)]


def test_match_skips_unroutable_gap():
    """Pairs further apart than MAX_ROUTING_DISTANCE_M are skipped, not routed."""
    G = nx.MultiDiGraph()
    positions = {1: 0.0, 2: 100.0, 3: 2900.0, 4: 3000.0}
    for n, x in positions.items():
        lon, lat = lonlat(x, 0.0)
        G.add_node(n, x=lon, y=lat)
    add_street(G, 1, 2)
    add_street(G, 3, 4)

    coords = _trace([(0.0, 0.0), (3000.0, 0.0)])
    edges, skipped = _match(G, coords)
    assert edges == []
    assert skipped == 1


def test_loop_removal_collapses_nearby_stub():
    """A quick out-and-back onto a 30m stub is removed from the match."""
    G = nx.MultiDiGraph()
    for n, (x, y) in {1: (0.0, 0.0), 2: (100.0, 0.0), 3: (0.0, 30.0)}.items():
        lon, lat = lonlat(x, y)
        G.add_node(n, x=lon, y=lat)
    add_street(G, 1, 2)
    add_street(G, 1, 3, highway="footway")

    coords = _trace([(0.0, 0.0), (0.0, 30.0), (0.0, 0.0), (20.0, 0.0), (60.0, 0.0), (100.0, 0.0)])
    edges, skipped = _match(G, coords)
    assert skipped == 0
    assert edges == [(1, 2)]  # the (1,3) stub detour is collapsed


def test_backtrack_preserves_forward_progress(grid_graph):
    """Returning to the start must not erase the outbound match.

    Detour nodes beyond LOOP_MAX_DETOUR_M gate the loop deletion: without
    the gate, an out-and-back ride would collapse to nothing.
    """
    east = [(x, 2.0) for x in range(0, 201, 20)]
    back = [(x, 2.0) for x in range(180, -1, -20)]
    coords = _trace(east + back)
    edges, skipped = _match(grid_graph, coords)
    assert skipped == 0
    assert edges == [(0, 1), (1, 2)]


def test_snap_ignores_far_points(grid_graph):
    # A point 500m from any street contributes nothing
    coords = _trace([(0.0, 3.0), (200.0, 500.0 + 400.0), (100.0, 3.0)])
    edges, _skipped = _match(grid_graph, coords)
    assert edges == [(0, 1)]


def test_densification_snaps_mid_long_edge():
    """GPS points mid-way along a 400m edge still snap to it via virtual points."""
    G = nx.MultiDiGraph()
    for n, x in {1: 0.0, 2: 400.0}.items():
        lon, lat = lonlat(x, 0.0)
        G.add_node(n, x=lon, y=lat)
    add_street(G, 1, 2)

    coords = _trace([(150.0, 3.0), (250.0, 3.0)])
    edges, skipped = _match(G, coords)
    assert skipped == 0
    assert edges == [(1, 2)]
