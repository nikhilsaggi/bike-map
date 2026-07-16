"""Tests for the HMM map-matcher (synthetic grid, no OSM data)."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest
from conftest import lonlat

import bike_routes as br


@pytest.fixture
def grid_mmap(grid_graph):
    return br._build_inmem_map(grid_graph)


def _track(points_m):
    """Build an (N,2) [lat,lon] array from local meter offsets."""
    return np.array([[lat, lon] for lon, lat in (lonlat(x, y) for x, y in points_m)])


def test_straight_street_matches_exact_edges(grid_mmap):
    # Ride along the bottom row (nodes 0-1-2-3), points every 20m with
    # small lateral noise.
    pts = [(x, 5.0 if (x // 20) % 2 else -5.0) for x in range(0, 301, 20)]
    edges, skipped = br._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped == 0
    assert edges == [(0, 1), (1, 2), (2, 3)]


def test_l_shaped_ride(grid_mmap):
    # East along the bottom row then north up a column: 0-1-2 then 2-12-22
    pts = [(x, 0.0) for x in range(0, 201, 20)] + [(200.0, y) for y in range(20, 201, 20)]
    edges, skipped = br._map_match_ride_hmm(grid_mmap, _track(pts))
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
    edges, skipped = br._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped >= 1
    assert (0, 1) in edges  # start matched
    # end matched: top row nodes are 40-44, e.g. (40+? ,) row 4 edges
    assert any(u >= 40 for u, _v in edges) or any(v >= 40 for _u, v in edges)


def test_match_one_dispatches_hmm(grid_graph, grid_mmap, monkeypatch):
    monkeypatch.setattr(br.config, "MATCHER", "hmm")
    ctx = ("hmm", grid_mmap)
    pts = [(x, 0.0) for x in range(0, 201, 20)]
    edges, skipped = br._match_one(grid_graph, ctx, _track(pts), {})
    assert edges == [(0, 1), (1, 2)]
    assert skipped == 0


def test_build_matcher_context_respects_config(grid_graph, monkeypatch):
    monkeypatch.setattr(br.config, "MATCHER", "hmm")
    kind, _data = br._build_matcher_context(grid_graph)
    assert kind == "hmm"
    monkeypatch.setattr(br.config, "MATCHER", "heuristic")
    kind, _data = br._build_matcher_context(grid_graph)
    assert kind == "heuristic"


def test_map_cache_roundtrip(grid_mmap, tmp_path, monkeypatch):
    monkeypatch.setattr(br.config, "HMM_MAP_CACHE_PATH", tmp_path / "hmm_map_cache.pkl")
    monkeypatch.setattr(br.config, "GRAPH_CACHE_PATH", tmp_path / "osm_graph_cache.pkl")
    br._save_inmem_map_cache(grid_mmap)
    loaded = br._load_cached_inmem_map()
    assert loaded is not None
    assert loaded.graph == grid_mmap.graph
    # A cached map matches identically to a freshly built one
    pts = [(x, 0.0) for x in range(0, 201, 20)]
    edges, skipped = br._map_match_ride_hmm(loaded, _track(pts))
    assert skipped == 0
    assert edges == [(0, 1), (1, 2)]


def test_map_cache_stale_when_graph_newer(grid_mmap, tmp_path, monkeypatch):
    cache = tmp_path / "hmm_map_cache.pkl"
    graph_cache = tmp_path / "osm_graph_cache.pkl"
    monkeypatch.setattr(br.config, "HMM_MAP_CACHE_PATH", cache)
    monkeypatch.setattr(br.config, "GRAPH_CACHE_PATH", graph_cache)
    br._save_inmem_map_cache(grid_mmap)
    graph_cache.write_bytes(b"newer graph")
    newer = time.time() + 60
    os.utime(graph_cache, (newer, newer))
    assert br._load_cached_inmem_map() is None


def test_map_cache_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(br.config, "HMM_MAP_CACHE_PATH", tmp_path / "absent.pkl")
    assert br._load_cached_inmem_map() is None


def test_matcher_context_uses_cache(grid_graph, tmp_path, monkeypatch):
    monkeypatch.setattr(br.config, "MATCHER", "hmm")
    monkeypatch.setattr(br.config, "HMM_MAP_CACHE_PATH", tmp_path / "hmm_map_cache.pkl")
    monkeypatch.setattr(br.config, "GRAPH_CACHE_PATH", tmp_path / "osm_graph_cache.pkl")
    kind, mmap1 = br._build_matcher_context(grid_graph, use_cache=True)
    assert kind == "hmm"
    assert br.config.HMM_MAP_CACHE_PATH.exists()  # cache written on miss
    _kind, mmap2 = br._build_matcher_context(grid_graph, use_cache=True)
    assert mmap2.graph == mmap1.graph  # second call served from cache


def test_long_offgrid_stretch_fast_forwards(grid_mmap):
    # A long teleported stretch (300 off-grid points) is fast-forwarded with
    # the cheap rtree test rather than a match attempt every few points, and
    # the on-grid tail is still recovered in full.
    pts = (
        [(x, 0.0) for x in range(0, 101, 20)]
        + [(50000.0, 50000.0)] * 300
        + [(x, 400.0) for x in range(0, 201, 20)]
    )
    edges, skipped = br._map_match_ride_hmm(grid_mmap, _track(pts))
    assert skipped >= 1
    assert (0, 1) in edges  # start matched
    assert (40, 41) in edges  # top-row tail matched from its first block
