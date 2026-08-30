"""Tests for pipeline state persistence and config-change invalidation."""

from __future__ import annotations

import os
import pickle

import networkx as nx

from bike_routes import cache, config, graph, render


def _cache_path(tmp_path, path):
    """Return tmp_path/<relative cache path>, creating the cache dir first."""
    full = tmp_path / path
    full.parent.mkdir(parents=True, exist_ok=True)
    return full


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = cache._empty_state()
    state["processed_files"].add("2024-01-05_08-00-00_-0500.csv")
    state["edge_counts"][(1, 2)] = 3
    cache._save_state(state)

    loaded = cache._load_state()
    assert loaded["processed_files"] == {"2024-01-05_08-00-00_-0500.csv"}
    assert loaded["edge_counts"] == {(1, 2): 3}


def test_missing_state_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = cache._load_state()
    assert state["processed_files"] == set()
    assert state["edge_counts"] == {}


def test_config_change_invalidates_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = cache._empty_state()
    state["processed_files"].add("a.csv")
    cache._save_state(state)

    # Route cache must be discarded alongside the state
    _cache_path(tmp_path, config.ROUTE_CACHE_PATH).write_bytes(b"stale")

    monkeypatch.setattr(config, "HMM_MAX_DIST", 999)
    loaded = cache._load_state()
    assert loaded["processed_files"] == set()
    assert not (tmp_path / config.ROUTE_CACHE_PATH).exists()


def test_network_type_change_invalidates_graph_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache._save_state(cache._empty_state())

    for p in (config.GRAPH_CACHE_PATH, config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH):
        _cache_path(tmp_path, p).write_bytes(b"stale")

    monkeypatch.setattr(config, "NETWORK_TYPES", ["bike"])
    loaded = cache._load_state()
    assert loaded["processed_files"] == set()
    for p in (config.GRAPH_CACHE_PATH, config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH):
        assert not (tmp_path / p).exists()


def test_config_hash_stable(monkeypatch):
    h1 = cache._config_hash()
    h2 = cache._config_hash()
    assert h1 == h2
    monkeypatch.setattr(config, "HMM_OBS_NOISE", 99)
    assert cache._config_hash() != h1
    # Switching matchers also invalidates
    monkeypatch.setattr(config, "MATCHER", "heuristic")
    assert cache._config_hash() != h1


def test_version_mismatch_invalidates_graph_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for p in (config.GRAPH_CACHE_PATH, config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH):
        _cache_path(tmp_path, p).write_bytes(b"stale")
    _cache_path(tmp_path, config.CACHE_VERSIONS_PATH).write_text(
        '{"osmnx": "0.0", "networkx": "0.0"}'
    )

    assert cache._graph_cache_valid() is False
    for p in (config.GRAPH_CACHE_PATH, config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH):
        assert not (tmp_path / p).exists()


def test_matching_versions_keep_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cache_path(tmp_path, config.GRAPH_CACHE_PATH).write_bytes(b"graph")
    cache._write_cache_versions()

    assert cache._graph_cache_valid() is True
    assert (tmp_path / config.GRAPH_CACHE_PATH).exists()


def test_missing_stamp_adopts_current_versions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cache_path(tmp_path, config.GRAPH_CACHE_PATH).write_bytes(b"legacy")

    assert cache._graph_cache_valid() is True
    assert (tmp_path / config.CACHE_VERSIONS_PATH).exists()
    assert cache._graph_cache_valid() is True  # stamp written, still valid


def test_legacy_render_cache_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Oldest format: bare geometry dict, no highway data
    with _cache_path(tmp_path, config.RENDER_CACHE_PATH).open("wb") as f:
        pickle.dump({(1, 2): [(0.0, 0.0), (1.0, 1.0)]}, f)
    assert render._get_render_data() is None

    # Interim format: (geometry, binary-class) 2-tuple without raw tags
    with _cache_path(tmp_path, config.RENDER_CACHE_PATH).open("wb") as f:
        pickle.dump(({(1, 2): [(0.0, 0.0), (1.0, 1.0)]}, {(1, 2): "bike"}), f)
    assert render._get_render_data() is None


def test_render_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    G = nx.MultiDiGraph()
    G.add_node(1, x=0.0, y=0.0)
    G.add_node(2, x=0.001, y=0.0)
    G.add_edge(1, 2, length=100.0, highway="cycleway")

    edge_geom, edge_hw, edge_name = render._build_render_cache(G)
    assert edge_hw[(1, 2)] == "cycleway"
    assert render._classify_hw(edge_hw[(1, 2)]) == "bike"
    assert edge_name == {}  # the test edge is unnamed

    loaded_geom, loaded_hw, loaded_name = render._get_render_data()
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

    _geom, _hw, edge_name = render._build_render_cache(G)
    assert edge_name[(1, 2)] == "Kent Avenue"
    assert edge_name[(2, 3)] == "Bedford Avenue"


def test_render_cache_format_bump_invalidates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Previous format: (fmt, geom, hw) with no names -- must not load, or the
    # corridor ranking would silently report nothing.
    with _cache_path(tmp_path, config.RENDER_CACHE_PATH).open("wb") as f:
        pickle.dump(("hw-raw-v1", {(1, 2): [(0.0, 0.0), (1.0, 1.0)]}, {(1, 2): "cycleway"}), f)
    assert render._get_render_data() is None


def test_unreadable_graph_cache_refetches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cache_path(tmp_path, config.GRAPH_CACHE_PATH).write_bytes(b"not a pickle")
    cache._write_cache_versions()

    sentinel = object()
    monkeypatch.setattr(graph, "_fetch_graph", lambda _bbox: sentinel)
    state = {"graph_bbox": (-74.0, 40.7, -73.9, 40.8)}

    assert graph._load_graph([], state) is sentinel
    assert not (tmp_path / config.GRAPH_CACHE_PATH).exists()


def test_legacy_caches_migrate_into_cache_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / config.GRAPH_CACHE_PATH.name).write_bytes(b"graph")
    (tmp_path / config.STATE_CACHE_PATH.name).write_bytes(b"state")

    cache._migrate_legacy_caches()

    assert (tmp_path / config.GRAPH_CACHE_PATH).read_bytes() == b"graph"
    assert (tmp_path / config.STATE_CACHE_PATH).read_bytes() == b"state"
    assert not (tmp_path / config.GRAPH_CACHE_PATH.name).exists()


def test_migration_preserves_mtime_ordering(tmp_path, monkeypatch):
    # _load_cached_inmem_map only trusts the hmm map if it is newer than the
    # graph it was built from; a copy would restamp both and invert that.
    monkeypatch.chdir(tmp_path)
    graph_legacy = tmp_path / config.GRAPH_CACHE_PATH.name
    hmm_legacy = tmp_path / config.HMM_MAP_CACHE_PATH.name
    graph_legacy.write_bytes(b"graph")
    hmm_legacy.write_bytes(b"hmm")
    os.utime(graph_legacy, (1_000_000, 1_000_000))
    os.utime(hmm_legacy, (2_000_000, 2_000_000))

    cache._migrate_legacy_caches()

    graph_mtime = (tmp_path / config.GRAPH_CACHE_PATH).stat().st_mtime
    assert (tmp_path / config.HMM_MAP_CACHE_PATH).stat().st_mtime > graph_mtime


def test_migration_keeps_the_relocated_copy(tmp_path, monkeypatch):
    # A half-migrated tree must not lose the newer file under cache/.
    monkeypatch.chdir(tmp_path)
    (tmp_path / config.STATE_CACHE_PATH.name).write_bytes(b"legacy")
    _cache_path(tmp_path, config.STATE_CACHE_PATH).write_bytes(b"current")

    cache._migrate_legacy_caches()

    assert (tmp_path / config.STATE_CACHE_PATH).read_bytes() == b"current"
    assert (tmp_path / config.STATE_CACHE_PATH.name).read_bytes() == b"legacy"
