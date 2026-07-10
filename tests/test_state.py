"""Tests for pipeline state persistence and config-change invalidation."""

from __future__ import annotations

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

    monkeypatch.setattr(br, "SNAP_TOLERANCE_M", 999)
    loaded = br._load_state()
    assert loaded["processed_files"] == set()
    assert not (tmp_path / br.ROUTE_CACHE_PATH.name).exists()


def test_network_type_change_invalidates_graph_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    br._save_state(br._empty_state())

    for p in (br.GRAPH_CACHE_PATH, br.RENDER_CACHE_PATH, br.ROUTE_CACHE_PATH):
        (tmp_path / p.name).write_bytes(b"stale")

    monkeypatch.setattr(br, "NETWORK_TYPES", ["bike"])
    loaded = br._load_state()
    assert loaded["processed_files"] == set()
    for p in (br.GRAPH_CACHE_PATH, br.RENDER_CACHE_PATH, br.ROUTE_CACHE_PATH):
        assert not (tmp_path / p.name).exists()


def test_config_hash_stable(monkeypatch):
    h1 = br._config_hash()
    h2 = br._config_hash()
    assert h1 == h2
    monkeypatch.setattr(br, "HEADING_PENALTY", 0.5)
    assert br._config_hash() != h1
