"""Pipeline state, config-hash invalidation, and cache management."""

from __future__ import annotations

import hashlib
import json
import pickle
from typing import Any

import networkx as nx
import osmnx as ox

from . import config


def _processing_config() -> dict[str, Any]:
    """Return params that affect map-matching results (change triggers full reprocess)."""
    shared = {
        "matcher": config.MATCHER,
        "max_gps_gap_m": config.MAX_GPS_GAP_M,
        "resample_spacing_m": config.RESAMPLE_SPACING_M,
        "network_types": sorted(config.NETWORK_TYPES),
        "edge_key_format": "node_pair",
    }
    if config.MATCHER == "hmm":
        return {
            **shared,
            "hmm_max_dist": config.HMM_MAX_DIST,
            "hmm_obs_noise": config.HMM_OBS_NOISE,
            "hmm_obs_noise_ne": config.HMM_OBS_NOISE_NE,
            "hmm_min_prob_norm": config.HMM_MIN_PROB_NORM,
            "hmm_lattice_width": config.HMM_LATTICE_WIDTH,
            "hmm_lattice_width_retry": config.HMM_LATTICE_WIDTH_RETRY,
            "hmm_fail_skip_points": config.HMM_FAIL_SKIP_POINTS,
        }
    return {
        **shared,
        "snap_tolerance_m": config.SNAP_TOLERANCE_M,
        "max_routing_distance_m": config.MAX_ROUTING_DISTANCE_M,
        "max_route_detour": config.MAX_ROUTE_DETOUR,
        "snap_method": "edge_heading",
        "heading_penalty": config.HEADING_PENALTY,
        "loop_window": config.LOOP_WINDOW,
        "loop_max_detour_m": config.LOOP_MAX_DETOUR_M,
        "hw_penalty": sorted(config.HW_PENALTY.items()),
    }
def _config_hash() -> str:
    """Return a hash of the current processing config."""
    return hashlib.sha1(json.dumps(_processing_config(), sort_keys=True).encode()).hexdigest()
def _empty_state() -> dict[str, Any]:
    """Return a fresh pipeline state dict."""
    return {
        "config_hash": _config_hash(),
        "config": _processing_config(),
        "processed_files": set(),
        "skipped_files": set(),
        "edge_counts": {},
        "edge_rides": {},
        "graph_bbox": None,
    }
def _load_state() -> dict[str, Any]:
    """Load cached state, invalidating if processing config changed."""
    if not config.STATE_CACHE_PATH.exists():
        return _empty_state()

    with config.STATE_CACHE_PATH.open("rb") as f:
        state = pickle.load(f)

    if state.get("config_hash") == _config_hash():
        return state

    old_config = state.get("config", {})
    if sorted(old_config.get("network_types", [])) != sorted(config.NETWORK_TYPES):
        print("Network types changed -- invalidating graph + render caches")
        for p in [config.GRAPH_CACHE_PATH, config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH]:
            if p.exists():
                p.unlink()

    if config.ROUTE_CACHE_PATH.exists():
        config.ROUTE_CACHE_PATH.unlink()
    print("Processing config changed -- full reprocess required")
    return _empty_state()
def _save_state(state: dict[str, Any]) -> None:
    """Persist pipeline state to disk."""
    state["config_hash"] = _config_hash()
    state["config"] = _processing_config()
    with config.STATE_CACHE_PATH.open("wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)
def _lib_versions() -> dict[str, str]:
    """Library versions the pickled graph cache depends on."""
    return {"osmnx": ox.__version__, "networkx": nx.__version__}
def _write_cache_versions() -> None:
    """Stamp the current library versions alongside the graph cache."""
    config.CACHE_VERSIONS_PATH.write_text(json.dumps(_lib_versions()))
def _graph_cache_valid() -> bool:
    """Check the graph cache was written by the current library versions.

    A graph pickled under a different osmnx/networkx can fail to unpickle
    or produce subtly broken objects, so a version mismatch invalidates the
    graph and its derived caches.  A missing stamp (cache predates version
    stamping) adopts the current versions rather than forcing a refetch.
    """
    if not config.CACHE_VERSIONS_PATH.exists():
        _write_cache_versions()
        return True
    try:
        stamped = json.loads(config.CACHE_VERSIONS_PATH.read_text())
    except Exception:
        stamped = None
    current = _lib_versions()
    if stamped == current:
        return True
    print(f"Library versions changed ({stamped} -> {current}) -- refetching graph")
    for p in [config.GRAPH_CACHE_PATH, config.RENDER_CACHE_PATH, config.ROUTE_CACHE_PATH, config.CACHE_VERSIONS_PATH]:
        if p.exists():
            p.unlink()
    return False
def _load_route_cache() -> dict[tuple[int, int], list[tuple[int, int]] | None]:
    """Load cached shortest-path results between node pairs."""
    if not config.ROUTE_CACHE_PATH.exists():
        return {}
    try:
        with config.ROUTE_CACHE_PATH.open("rb") as f:
            return pickle.load(f)
    except Exception:
        return {}
def _save_route_cache(
    route_cache: dict[tuple[int, int], list[tuple[int, int]] | None],
) -> None:
    """Persist route cache to disk."""
    with config.ROUTE_CACHE_PATH.open("wb") as f:
        pickle.dump(route_cache, f, protocol=pickle.HIGHEST_PROTOCOL)
