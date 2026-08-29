"""Tests for pipeline state persistence and config-change invalidation."""

from __future__ import annotations

import pickle

import networkx as nx

import bike_routes as br


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = br._empty_state()
    state["processed_files"].add("2024-01-05_08-00-00_-0500.csv")
    state["edge_counts"][(1, 2)] = 3
    br._save_state(state)

    loaded = br._load_state()
    assert loaded["processed_files"] == {"2024-01-05_08-00-00_-0500.csv"}
    assert loaded["edge_counts"] == {(1, 2): 3}


def test_missing_state_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = br._load_state()
    assert state["processed_files"] == set()
    assert state["edge_counts"] == {}


def test_config_change_invalidates_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = br._empty_state()
    state["processed_files"].add("a.csv")
    br._save_state(state)

    # Route cache must be discarded alongside the state
    (tmp_path / br.ROUTE_CACHE_PATH.name).write_bytes(b"stale")

    monkeypatch.setattr(br.config, "HMM_MAX_DIST", 999)
    loaded = br._load_state()
    assert loaded["processed_files"] == set()
    assert not (tmp_path / br.ROUTE_CACHE_PATH.name).exists()


def test_network_type_change_invalidates_graph_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    br._save_state(br._empty_state())

    for p in (br.GRAPH_CACHE_PATH, br.RENDER_CACHE_PATH, br.ROUTE_CACHE_PATH):
        (tmp_path / p.name).write_bytes(b"stale")

    monkeypatch.setattr(br.config, "NETWORK_TYPES", ["bike"])
    loaded = br._load_state()
    assert loaded["processed_files"] == set()
    for p in (br.GRAPH_CACHE_PATH, br.RENDER_CACHE_PATH, br.ROUTE_CACHE_PATH):
        assert not (tmp_path / p.name).exists()


def test_config_hash_stable(monkeypatch):
    h1 = br._config_hash()
    h2 = br._config_hash()
    assert h1 == h2
    monkeypatch.setattr(br.config, "HMM_OBS_NOISE", 99)
    assert br._config_hash() != h1
    # Switching matchers also invalidates
    monkeypatch.setattr(br.config, "MATCHER", "heuristic")
    assert br._config_hash() != h1


def test_version_mismatch_invalidates_graph_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for p in (br.GRAPH_CACHE_PATH, br.RENDER_CACHE_PATH, br.ROUTE_CACHE_PATH):
        (tmp_path / p.name).write_bytes(b"stale")
    (tmp_path / br.CACHE_VERSIONS_PATH.name).write_text('{"osmnx": "0.0", "networkx": "0.0"}')

    assert br._graph_cache_valid() is False
    for p in (br.GRAPH_CACHE_PATH, br.RENDER_CACHE_PATH, br.ROUTE_CACHE_PATH):
        assert not (tmp_path / p.name).exists()


def test_matching_versions_keep_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / br.GRAPH_CACHE_PATH.name).write_bytes(b"graph")
    br._write_cache_versions()

    assert br._graph_cache_valid() is True
    assert (tmp_path / br.GRAPH_CACHE_PATH.name).exists()


def test_missing_stamp_adopts_current_versions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / br.GRAPH_CACHE_PATH.name).write_bytes(b"legacy")

    assert br._graph_cache_valid() is True
    assert (tmp_path / br.CACHE_VERSIONS_PATH.name).exists()
    assert br._graph_cache_valid() is True  # stamp written, still valid


def test_legacy_render_cache_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Oldest format: bare geometry dict, no highway data
    with (tmp_path / br.RENDER_CACHE_PATH.name).open("wb") as f:
        pickle.dump({(1, 2): [(0.0, 0.0), (1.0, 1.0)]}, f)
    assert br._get_render_data() is None

    # Interim format: (geometry, binary-class) 2-tuple without raw tags
    with (tmp_path / br.RENDER_CACHE_PATH.name).open("wb") as f:
        pickle.dump(({(1, 2): [(0.0, 0.0), (1.0, 1.0)]}, {(1, 2): "bike"}), f)
    assert br._get_render_data() is None


def test_render_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    G = nx.MultiDiGraph()
    G.add_node(1, x=0.0, y=0.0)
    G.add_node(2, x=0.001, y=0.0)
    G.add_edge(1, 2, length=100.0, highway="cycleway")

    edge_geom, edge_hw, edge_name = br._build_render_cache(G)
    assert edge_hw[(1, 2)] == "cycleway"
    assert br._classify_hw(edge_hw[(1, 2)]) == "bike"
    assert edge_name == {}  # the test edge is unnamed

    loaded_geom, loaded_hw, loaded_name = br._get_render_data()
    assert loaded_geom == edge_geom
    assert loaded_hw == edge_hw
    assert loaded_name == edge_name


def test_render_cache_keeps_street_names(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    G = nx.MultiDiGraph()
    G.add_node(1, x=0.0, y=0.0)
    G.add_node(2, x=0.001, y=0.0)
    G.add_edge(1, 2, length=100.0, highway="cycleway", name="Kent Avenue")
    # OSM puts a list here where a way carries several names.
    G.add_node(3, x=0.002, y=0.0)
    G.add_edge(2, 3, length=100.0, highway="residential", name=["Bedford Avenue", "NY 27"])

    _geom, _hw, edge_name = br._build_render_cache(G)
    assert edge_name[(1, 2)] == "Kent Avenue"
    assert edge_name[(2, 3)] == "Bedford Avenue"


def test_render_cache_format_bump_invalidates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Previous format: (fmt, geom, hw) with no names -- must not load, or the
    # corridor ranking would silently report nothing.
    with (tmp_path / br.RENDER_CACHE_PATH.name).open("wb") as f:
        pickle.dump(("hw-raw-v1", {(1, 2): [(0.0, 0.0), (1.0, 1.0)]}, {(1, 2): "cycleway"}), f)
    assert br._get_render_data() is None


def test_unreadable_graph_cache_refetches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / br.GRAPH_CACHE_PATH.name).write_bytes(b"not a pickle")
    br._write_cache_versions()

    sentinel = object()
    monkeypatch.setattr(br.graph, "_fetch_graph", lambda _bbox: sentinel)
    state = {"graph_bbox": (-74.0, 40.7, -73.9, 40.8)}

    assert br._load_graph([], state) is sentinel
    assert not (tmp_path / br.GRAPH_CACHE_PATH.name).exists()
