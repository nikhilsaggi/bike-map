"""Tests for the HMM map-matcher (synthetic grid, no OSM data)."""

from __future__ import annotations

import os
import time

import networkx as nx
import numpy as np
import pytest
from conftest import add_street, lonlat

from bike_routes import config, hmm


@pytest.fixture
def grid_mmap(grid_graph):
    return hmm._build_inmem_map(grid_graph)


def _track(points_m):
    """Build an (N,2) [lat,lon] array from local meter offsets."""
    return np.array([[lat, lon] for lon, lat in (lonlat(x, y) for x, y in points_m)])


def test_straight_street_matches_exact_edges(grid_mmap):
    # Ride along the bottom row (nodes 0-1-2-3), points every 20m with
    # small lateral noise.
    pts = [(x, 5.0 if (x // 20) % 2 else -5.0) for x in range(0, 301, 20)]
    edges, skipped = hmm._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped == 0
    assert edges == [(0, 1), (1, 2), (2, 3)]


def test_l_shaped_ride(grid_mmap):
    # East along the bottom row then north up a column: 0-1-2 then 2-12-22
    pts = [(x, 0.0) for x in range(0, 201, 20)] + [(200.0, y) for y in range(20, 201, 20)]
    edges, skipped = hmm._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped == 0
    assert edges == [(0, 1), (1, 2), (2, 12), (12, 22)]


def test_gps_teleport_becomes_skipped_gap(grid_mmap):
    # Points jump far outside the grid mid-ride (matcher dead-end), then
    # return: the unmatched stretch is skipped, both ends still match.
    pts = (
        [(x, 0.0) for x in range(0, 101, 20)]
        + [(5000.0, 5000.0)] * 3
        + [(x, 400.0) for x in range(100, 201, 20)]
    )
    edges, skipped = hmm._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped >= 1
    assert (0, 1) in edges  # start matched
    # end matched: top row nodes are 40-44, e.g. (40+? ,) row 4 edges
    assert any(u >= 40 for u, _v in edges) or any(v >= 40 for _u, v in edges)


def test_match_one_dispatches_hmm(grid_graph, grid_mmap, monkeypatch):
    monkeypatch.setattr(config, "MATCHER", "hmm")
    ctx = ("hmm", grid_mmap)
    pts = [(x, 0.0) for x in range(0, 201, 20)]
    edges, skipped = hmm._match_one(grid_graph, ctx, _track(pts), {})
    assert edges == [(0, 1), (1, 2)]
    assert skipped == 0


def test_build_matcher_context_respects_config(grid_graph, monkeypatch):
    monkeypatch.setattr(config, "MATCHER", "hmm")
    kind, _data = hmm._build_matcher_context(grid_graph)
    assert kind == "hmm"
    monkeypatch.setattr(config, "MATCHER", "heuristic")
    kind, _data = hmm._build_matcher_context(grid_graph)
    assert kind == "heuristic"


def test_map_cache_roundtrip(grid_mmap, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HMM_MAP_CACHE_PATH", tmp_path / "hmm_map_cache.pkl")
    monkeypatch.setattr(config, "GRAPH_CACHE_PATH", tmp_path / "osm_graph_cache.pkl")
    hmm._save_inmem_map_cache(grid_mmap)
    loaded = hmm._load_cached_inmem_map()
    assert loaded is not None
    assert loaded.graph == grid_mmap.graph
    # A cached map matches identically to a freshly built one
    pts = [(x, 0.0) for x in range(0, 201, 20)]
    edges, skipped = hmm._map_match_ride_hmm(loaded, _track(pts))
    assert skipped == 0
    assert edges == [(0, 1), (1, 2)]


def test_map_cache_stale_when_graph_newer(grid_mmap, tmp_path, monkeypatch):
    cache = tmp_path / "hmm_map_cache.pkl"
    graph_cache = tmp_path / "osm_graph_cache.pkl"
    monkeypatch.setattr(config, "HMM_MAP_CACHE_PATH", cache)
    monkeypatch.setattr(config, "GRAPH_CACHE_PATH", graph_cache)
    hmm._save_inmem_map_cache(grid_mmap)
    graph_cache.write_bytes(b"newer graph")
    newer = time.time() + 60
    os.utime(graph_cache, (newer, newer))
    assert hmm._load_cached_inmem_map() is None


def test_map_cache_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HMM_MAP_CACHE_PATH", tmp_path / "absent.pkl")
    assert hmm._load_cached_inmem_map() is None


def test_matcher_context_uses_cache(grid_graph, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MATCHER", "hmm")
    monkeypatch.setattr(config, "HMM_MAP_CACHE_PATH", tmp_path / "hmm_map_cache.pkl")
    monkeypatch.setattr(config, "GRAPH_CACHE_PATH", tmp_path / "osm_graph_cache.pkl")
    kind, mmap1 = hmm._build_matcher_context(grid_graph, use_cache=True)
    assert kind == "hmm"
    assert config.HMM_MAP_CACHE_PATH.exists()  # cache written on miss
    _kind, mmap2 = hmm._build_matcher_context(grid_graph, use_cache=True)
    assert mmap2.graph == mmap1.graph  # second call served from cache


def test_deep_dead_end_keeps_prefix(grid_mmap):
    # Dead end deeper than HMM_RETRY_WINDOW (30 pts): the narrow-beam prefix
    # is kept and only a window is re-decoded wide -- the whole prefix and the
    # post-gap tail must both survive.
    pts = (
        [(x, 0.0) for x in range(0, 401, 20)]  # bottom row, 21 pts
        + [(400.0, y) for y in range(20, 401, 20)]  # up the last column, 20 pts
        + [(50000.0, 50000.0)] * 5  # teleport at obs ~41 > window
        + [(x, 400.0) for x in range(380, 199, -20)]  # back along the top row
    )
    edges, skipped = hmm._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped >= 1
    assert (0, 1) in edges  # prefix start
    assert (3, 4) in edges  # prefix end of bottom row
    assert (34, 44) in edges  # prefix top of column
    assert (42, 43) in edges  # tail matched after the gap


def test_long_offgrid_stretch_fast_forwards(grid_mmap):
    # A long teleported stretch (300 off-grid points) is fast-forwarded with
    # the cheap rtree test rather than a match attempt every few points, and
    # the on-grid tail is still recovered in full.
    pts = (
        [(x, 0.0) for x in range(0, 101, 20)]
        + [(50000.0, 50000.0)] * 300
        + [(x, 400.0) for x in range(0, 201, 20)]
    )
    edges, skipped = hmm._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped >= 1
    assert (0, 1) in edges  # start matched
    assert (40, 41) in edges  # top-row tail matched from its first block


# -- Sidewalk filtering -----------------------------------------------------


def _sidewalk_graph():
    """Grid with a sidewalk beside one street, a crossing, and a greenway.

    Nodes 0-1 are 100 m of street; 100-101 is a footway 8 m to its north;
    200-201 is a footway 200 m north of everything (a greenway); 300-301 is
    a footway perpendicular to the street.
    """
    G = nx.MultiDiGraph()
    for nid, (x, y) in {
        0: (0.0, 0.0),
        1: (100.0, 0.0),
        100: (0.0, 8.0),
        101: (100.0, 8.0),
        200: (0.0, 200.0),
        201: (100.0, 200.0),
        300: (50.0, 0.0),
        301: (50.0, 40.0),
    }.items():
        lon, lat = lonlat(x, y)
        G.add_node(nid, x=lon, y=lat)
    add_street(G, 0, 1, highway="secondary")
    for u, v in ((100, 101), (200, 201), (300, 301)):
        add_street(G, u, v, highway="footway")
    return G


def test_sidewalk_beside_a_street_is_filtered_out():
    G = _sidewalk_graph()
    sidewalks = hmm._sidewalk_edges(G)
    assert (100, 101) in sidewalks  # 8 m from the street, parallel
    assert (200, 201) not in sidewalks  # a greenway: no roadway beside it
    assert (300, 301) not in sidewalks  # perpendicular, so not accompanying
    assert (0, 1) not in sidewalks  # the street itself is never a candidate


def test_only_roadways_make_a_footway_a_sidewalk():
    # The Hudson River Park case: a service way beside an esplanade must not
    # class it as a sidewalk, whatever the distance.
    G = _sidewalk_graph()
    for _u, _v, data in G.edges(data=True):
        if data["highway"] == "secondary":
            data["highway"] = "service"
    assert hmm._sidewalk_edges(G) == set()


def test_map_index_leaves_sidewalks_out():
    mmap = hmm._build_inmem_map(_sidewalk_graph())
    assert mmap.size() == 8  # every node kept, whatever happened to its edges
    assert 101 not in mmap.graph[100][1]
    assert 201 in mmap.graph[200][1]


def test_map_cache_rejects_a_differently_filtered_index(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HMM_MAP_CACHE_PATH", tmp_path / "hmm.pkl")
    monkeypatch.setattr(config, "GRAPH_CACHE_PATH", tmp_path / "graph.pkl")
    hmm._save_inmem_map_cache(hmm._build_inmem_map(_sidewalk_graph()))
    assert hmm._load_cached_inmem_map() is not None
    monkeypatch.setattr(config, "SIDEWALK_PARALLEL_M", 30.0)
    assert hmm._load_cached_inmem_map() is None
