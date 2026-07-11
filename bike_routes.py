"""Bike Route Frequency Map -- Incremental Pipeline.

Designed for ongoing use: processes only new rides and renders from cached data.

First run:  loads all rides, fetches OSM graph, processes everything (~10-25 min).
After that: processes only new rides added to the rides/ folder (~1-2 min).

Cache files (auto-managed, delete any to force rebuild):
  osm_graph_cache.pkl  -- OSM street graph (bike + drive + walk networks)
  state.pkl            -- processed filenames, edge counts, config snapshot
  render_cache.pkl     -- pre-extracted edge geometries for fast rendering

Dependencies:
    pip install .    # or: pip install osmnx networkx numpy matplotlib scipy
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import pickle
import time
from collections import ChainMap
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo

    _NYC_TZ = ZoneInfo("America/New_York")
except Exception:  # tz database unavailable; timestamps keep their own offset
    _NYC_TZ = None

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import osmnx as ox
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from scipy.spatial import cKDTree

# -- Config: Processing --------------------------------------------------------

RIDES_FOLDER = "rides"
SAMPLE_SIZE = None  # set to e.g. 100 for quick preview, None for all rides
RIDE_FILES = None  # set to e.g. ["2024-06-19_13-35-52_-0400.csv"] to process specific rides
RESAMPLE_SPACING_M = 20
NETWORK_TYPES = ["bike", "drive", "walk"]
SNAP_TOLERANCE_M = 80
MAX_ROUTING_DISTANCE_M = 2500
MAX_GPS_GAP_M = 300  # split ride into segments at raw GPS gaps larger than this
HEADING_PENALTY = 0.15  # metres of snap penalty per degree of edge-heading mismatch
LOOP_WINDOW = 6  # remove short loops (A->...->A) within this many nodes
LOOP_MAX_DETOUR_M = 50  # only remove loops where detour nodes are within this distance of anchor
DENSIFY_M = 150  # add virtual snap points on edges longer than this (metres)
MATCH_PARALLEL_MIN_RIDES = 50  # match on worker processes when this many new rides
MATCH_CHUNK_SIZE = 20  # rides per parallel work unit

# Highway-type snap bias (metres added to edge score; negative = prefer)
HW_PENALTY = {
    "cycleway": -5,
    "path": -2,
    "track": -1,
    "footway": 8,
    "steps": 40,
    "pedestrian": 5,
    "service": 3,
    "motorway": 10,
    "motorway_link": 5,
    "trunk": 2,
    "trunk_link": 2,
}

# Infrastructure classification for the interactive map
INFRA_CLASS: dict[str, str] = {
    "cycleway": "bike",
    "path": "bike",
    "track": "bike",
}
INFRA_DEFAULT = "road"

# Rides with no coordinates inside this bbox are skipped entirely.
NYC_BBOX = (40.49, -74.30, 41.0, -73.60)  # (lat_min, lon_min, lat_max, lon_max)

# -- Config: Rendering ---------------------------------------------------------

FIG_SIZE = (14, 18)
COLORMAP = "plasma"
LINE_WIDTH_MIN = 0.4
LINE_WIDTH_MAX = 6.0
OUTPUT_PATH_UNWEIGHTED = "bike_routes_coverage.png"
OUTPUT_PATH_WEIGHTED = "bike_routes_frequency.png"
GEOJSON_OUTPUT_PATH = Path("docs/rides.geojson.gz")
# The PNGs are gitignored and only useful locally; CI sets this to skip them.
SKIP_PNG_RENDER = os.environ.get("SKIP_PNG_RENDER") == "1"

# -- Config: Cache paths -------------------------------------------------------

GRAPH_CACHE_PATH = Path("osm_graph_cache.pkl")
STATE_CACHE_PATH = Path("state.pkl")
RENDER_CACHE_PATH = Path("render_cache.pkl")
ROUTE_CACHE_PATH = Path("route_cache.pkl")
CACHE_VERSIONS_PATH = Path("cache_versions.json")


# -- Cache management ----------------------------------------------------------


def _processing_config() -> dict[str, Any]:
    """Return params that affect map-matching results (change triggers full reprocess)."""
    return {
        "snap_tolerance_m": SNAP_TOLERANCE_M,
        "max_routing_distance_m": MAX_ROUTING_DISTANCE_M,
        "max_gps_gap_m": MAX_GPS_GAP_M,
        "snap_method": "edge_heading",
        "heading_penalty": HEADING_PENALTY,
        "loop_window": LOOP_WINDOW,
        "loop_max_detour_m": LOOP_MAX_DETOUR_M,
        "hw_penalty": sorted(HW_PENALTY.items()),
        "resample_spacing_m": RESAMPLE_SPACING_M,
        "network_types": sorted(NETWORK_TYPES),
        "edge_key_format": "node_pair",
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
    if not STATE_CACHE_PATH.exists():
        return _empty_state()

    with STATE_CACHE_PATH.open("rb") as f:
        state = pickle.load(f)

    if state.get("config_hash") == _config_hash():
        return state

    old_config = state.get("config", {})
    if sorted(old_config.get("network_types", [])) != sorted(NETWORK_TYPES):
        print("Network types changed -- invalidating graph + render caches")
        for p in [GRAPH_CACHE_PATH, RENDER_CACHE_PATH, ROUTE_CACHE_PATH]:
            if p.exists():
                p.unlink()

    if ROUTE_CACHE_PATH.exists():
        ROUTE_CACHE_PATH.unlink()
    print("Processing config changed -- full reprocess required")
    return _empty_state()


def _save_state(state: dict[str, Any]) -> None:
    """Persist pipeline state to disk."""
    state["config_hash"] = _config_hash()
    state["config"] = _processing_config()
    with STATE_CACHE_PATH.open("wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)


def _lib_versions() -> dict[str, str]:
    """Library versions the pickled graph cache depends on."""
    return {"osmnx": ox.__version__, "networkx": nx.__version__}


def _write_cache_versions() -> None:
    """Stamp the current library versions alongside the graph cache."""
    CACHE_VERSIONS_PATH.write_text(json.dumps(_lib_versions()))


def _graph_cache_valid() -> bool:
    """Check the graph cache was written by the current library versions.

    A graph pickled under a different osmnx/networkx can fail to unpickle
    or produce subtly broken objects, so a version mismatch invalidates the
    graph and its derived caches.  A missing stamp (cache predates version
    stamping) adopts the current versions rather than forcing a refetch.
    """
    if not CACHE_VERSIONS_PATH.exists():
        _write_cache_versions()
        return True
    try:
        stamped = json.loads(CACHE_VERSIONS_PATH.read_text())
    except Exception:
        stamped = None
    current = _lib_versions()
    if stamped == current:
        return True
    print(f"Library versions changed ({stamped} -> {current}) -- refetching graph")
    for p in [GRAPH_CACHE_PATH, RENDER_CACHE_PATH, ROUTE_CACHE_PATH, CACHE_VERSIONS_PATH]:
        if p.exists():
            p.unlink()
    return False


def _load_route_cache() -> dict[tuple[int, int], list[tuple[int, int]] | None]:
    """Load cached shortest-path results between node pairs."""
    if not ROUTE_CACHE_PATH.exists():
        return {}
    try:
        with ROUTE_CACHE_PATH.open("rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def _save_route_cache(
    route_cache: dict[tuple[int, int], list[tuple[int, int]] | None],
) -> None:
    """Persist route cache to disk."""
    with ROUTE_CACHE_PATH.open("wb") as f:
        pickle.dump(route_cache, f, protocol=pickle.HIGHEST_PROTOCOL)


# -- Data processing -----------------------------------------------------------


def haversine_m(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Compute distance in metres between WGS-84 points (scalar or array)."""
    R = 6_371_000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def resample_ride_by_distance(coords: np.ndarray, spacing_m: float) -> np.ndarray:
    """Resample (N,2) [lat,lon] array to ~spacing_m metre intervals."""
    if len(coords) < 2:
        return coords
    seg_dists = haversine_m(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    dists = np.empty(len(coords))
    dists[0] = 0.0
    np.cumsum(seg_dists, out=dists[1:])
    total = dists[-1]
    if total < spacing_m:
        return coords[[0, -1]]
    targets = np.arange(0, total, spacing_m)
    return np.column_stack(
        [
            np.interp(targets, dists, coords[:, 0]),
            np.interp(targets, dists, coords[:, 1]),
        ]
    )


def _split_at_gaps(coords: np.ndarray, max_gap_m: float) -> list[np.ndarray]:
    """Split (N,2) [lat,lon] into sub-arrays wherever consecutive points exceed max_gap_m."""
    if len(coords) < 2:
        return [coords]
    dists = haversine_m(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    gap_idx = np.where(dists > max_gap_m)[0] + 1
    if len(gap_idx) == 0:
        return [coords]
    return np.split(coords, gap_idx)


def _is_nyc_ride(coords: np.ndarray) -> bool:
    """Check if any point in the ride falls within NYC_BBOX."""
    lat_min, lon_min, lat_max, lon_max = NYC_BBOX
    in_bbox = (
        (coords[:, 0] >= lat_min)
        & (coords[:, 0] <= lat_max)
        & (coords[:, 1] >= lon_min)
        & (coords[:, 1] <= lon_max)
    )
    return in_bbox.any()


def _load_and_resample(
    filenames: list[str],
) -> tuple[list[tuple[str, np.ndarray]], int]:
    """Load CSVs, filter to NYC, split at GPS gaps, and resample.

    Returns (nyc_rides, skipped_non_nyc) where nyc_rides is [(filename, coords)].
    A single file may produce multiple entries if it has GPS gaps.
    """
    rides = []
    non_nyc = 0
    for f in filenames:
        path = Path(RIDES_FOLDER) / f
        try:
            data = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(0, 1))
        except Exception as exc:
            print(f"  Skipping {f}: {exc}")
            continue
        if data.ndim == 1:
            data = data.reshape(1, 2)
        coords = data[:, ::-1]  # (lon, lat) -> (lat, lon)
        if not _is_nyc_ride(coords):
            non_nyc += 1
            continue
        segments = _split_at_gaps(coords, MAX_GPS_GAP_M)
        for seg in segments:
            rs = resample_ride_by_distance(seg, RESAMPLE_SPACING_M)
            if len(rs) >= 2:
                rides.append((f, rs))
    return rides, non_nyc


# -- Ride stats ------------------------------------------------------------------
#
# Per-ride time/distance stats are derived from the CSV timestamps that
# map-matching otherwise discards.  They live in state["ride_stats"] keyed by
# filename, are backfilled for rides processed before this feature existed,
# and do not participate in the config hash (no reprocess on change).


def _parse_ride_timestamp(ts: str) -> datetime | None:
    """Parse a ride CSV timestamp.

    Accepts the exporter format ("2021-09-04 18:53:31 -0400") and ISO 8601
    as written by GPX (e.g. "2024-06-19T17:35:52Z").  Returns None if the
    value is unparseable or has no timezone.
    """
    ts = ts.strip()
    for parse in (
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z"),
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
    ):
        try:
            dt = parse(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            return None
        return dt
    return None


def _ride_stats_for_file(path: Path) -> dict[str, Any] | None:
    """Compute {start, duration_s, dist_m} for one ride CSV.

    start is ISO 8601 in NYC local time (or the timestamp's own offset if
    the tz database is unavailable -- equivalent for the exporter format,
    which already carries local offsets).  Distance is the raw GPS track
    length; duration is elapsed wall-clock time between first and last fix.
    """
    first_ts: str | None = None
    last_ts: str | None = None
    lats: list[float] = []
    lons: list[float] = []
    try:
        with path.open() as f:
            next(f)  # header
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) < 3:
                    continue
                lons.append(float(parts[0]))
                lats.append(float(parts[1]))
                if first_ts is None:
                    first_ts = parts[2]
                last_ts = parts[2]
    except Exception:
        return None
    if len(lats) < 2 or first_ts is None or last_ts is None:
        return None

    la = np.array(lats)
    lo = np.array(lons)
    dist_m = float(haversine_m(la[:-1], lo[:-1], la[1:], lo[1:]).sum())

    t0 = _parse_ride_timestamp(first_ts)
    t1 = _parse_ride_timestamp(last_ts)
    if t0 is None:
        return None
    start = t0.astimezone(_NYC_TZ) if _NYC_TZ is not None else t0
    duration_s = (t1 - t0).total_seconds() if t1 is not None else None
    return {
        "start": start.isoformat(),
        "duration_s": duration_s,
        "dist_m": round(dist_m, 1),
    }


def _backfill_ride_stats(state: dict[str, Any]) -> int:
    """Compute stats for processed rides that don't have them yet.

    Unparseable rides are stored as None so they aren't retried every run;
    rides whose CSV is missing on disk are skipped and retried next run.
    """
    stats = state.setdefault("ride_stats", {})
    missing = sorted(state["processed_files"] - stats.keys())
    n = 0
    for fname in missing:
        path = Path(RIDES_FOLDER) / fname
        if not path.exists():
            continue
        stats[fname] = _ride_stats_for_file(path)
        n += 1
    return n


def _riding_summary(ride_stats: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    """Aggregate per-ride stats into the map's riding-summary property."""
    by_hour = [0] * 24
    by_weekday = [0] * 7
    km_by_year: dict[str, float] = {}
    total_km = 0.0
    total_s = 0.0
    longest_km = 0.0
    n = 0
    for rs in ride_stats.values():
        if not rs or not rs.get("start"):
            continue
        dt = datetime.fromisoformat(rs["start"])
        km = rs["dist_m"] / 1000
        by_hour[dt.hour] += 1
        by_weekday[dt.weekday()] += 1
        year = str(dt.year)
        km_by_year[year] = km_by_year.get(year, 0.0) + km
        total_km += km
        longest_km = max(longest_km, km)
        if rs.get("duration_s"):
            total_s += rs["duration_s"]
        n += 1
    if n == 0:
        return None
    return {
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "km_by_year": {y: round(v, 1) for y, v in sorted(km_by_year.items())},
        "total_km": round(total_km, 1),
        "total_h": round(total_s / 3600, 1),
        "avg_kmh": round(total_km / (total_s / 3600), 1) if total_s else None,
        "longest_km": round(longest_km, 1),
    }


# -- Graph management ----------------------------------------------------------


def _compute_bbox(all_pts: np.ndarray) -> tuple[float, float, float, float]:
    """Compute bounding box from ride coordinates, clamped to NYC_BBOX."""
    lats, lons = all_pts[:, 0], all_pts[:, 1]
    bbox_lat_min, bbox_lon_min, bbox_lat_max, bbox_lon_max = NYC_BBOX

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
    for nt in NETWORK_TYPES:
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

    with GRAPH_CACHE_PATH.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    _write_cache_versions()
    print(f"  Cached to {GRAPH_CACHE_PATH}")
    return G


def _load_graph(new_rides: list[tuple[str, np.ndarray]], state: dict[str, Any]) -> nx.MultiDiGraph:
    """Load graph from cache or fetch from OSM."""
    if GRAPH_CACHE_PATH.exists() and _graph_cache_valid():
        print(f"Loading graph from {GRAPH_CACHE_PATH}...")
        try:
            with GRAPH_CACHE_PATH.open("rb") as f:
                G = pickle.load(f)
        except Exception as exc:
            print(f"  Graph cache unreadable ({exc!r}) -- refetching from OSM")
            GRAPH_CACHE_PATH.unlink()
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

    for p in [RENDER_CACHE_PATH, ROUTE_CACHE_PATH]:
        if p.exists():
            p.unlink()

    return _fetch_graph(bbox)


# -- Map-matching --------------------------------------------------------------


def _path_to_edges(
    G: nx.MultiDiGraph, path_nodes: list[int], max_dist: float
) -> list[tuple[int, int]] | None:
    """Convert path nodes to canonical edge pairs in a single pass.

    Returns edge list or None if cumulative length exceeds max_dist.
    """
    length = 0.0
    result = []
    for a, b in zip(path_nodes[:-1], path_nodes[1:]):
        canon = (min(a, b), max(a, b))
        edge_data = G[canon[0]][canon[1]] if G.has_edge(canon[0], canon[1]) else G[a][b]
        key = min(edge_data, key=lambda k: edge_data[k].get("length", 0))
        length += edge_data[key].get("length", 0)
        if length > max_dist:
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
        p = min(HW_PENALTY.get(h, 0) for h in hw) if isinstance(hw, list) else HW_PENALTY.get(hw, 0)
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
        if length < DENSIFY_M:
            continue
        n_pts = int(length / DENSIFY_M)
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
    hp = HEADING_PENALTY
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
    lw = LOOP_WINDOW
    detour_sq = LOOP_MAX_DETOUR_M * LOOP_MAX_DETOUR_M
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
            if straight > MAX_ROUTING_DISTANCE_M:
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
            result = None if path is None else _path_to_edges(G, path, MAX_ROUTING_DISTANCE_M)
            route_cache[canon] = result
            pairs[idx] = result

    # Phase 3: assemble
    edges: list[tuple[int, int]] = []
    skipped = 0
    for r in pairs:
        if r is None:
            skipped += 1
        elif r is not _PENDING:
            edges.extend(r)
    return edges, skipped


# -- Parallel matching ---------------------------------------------------------
#
# Rides are independent given the graph, so cold rebuilds (1,000+ rides,
# ~45 min sequential) are matched on worker processes.  Each worker loads
# the cached graph and snap structures once via the pool initializer and
# keeps a private route cache that grows across its chunks; entries new to
# each chunk are shipped back and merged into the main cache.

_worker_graph_ctx: tuple[Any, ...] | None = None
_worker_route_cache: dict[tuple[int, int], list[tuple[int, int]] | None] | None = None


def _match_worker_init() -> None:
    """Load the graph and build snap structures once per worker process."""
    global _worker_graph_ctx, _worker_route_cache  # noqa: PLW0603 -- pool initializer pattern
    with GRAPH_CACHE_PATH.open("rb") as f:
        G = pickle.load(f)
    _worker_graph_ctx = (G, _build_snap_tree(G))
    _worker_route_cache = _load_route_cache()


def _match_chunk(
    chunk: list[tuple[str, np.ndarray]],
) -> tuple[
    list[tuple[str, list[tuple[int, int]], int]],
    dict[tuple[int, int], list[tuple[int, int]] | None],
]:
    """Match one chunk of rides in a worker.

    Returns per-ride results and the route-cache entries first computed
    during this chunk.
    """
    assert _worker_graph_ctx is not None
    assert _worker_route_cache is not None
    G, snap_data = _worker_graph_ctx
    new_entries: dict[tuple[int, int], list[tuple[int, int]] | None] = {}
    cache = ChainMap(new_entries, _worker_route_cache)
    results = []
    for fname, coords in chunk:
        edges, skipped = _map_match_ride(G, coords, SNAP_TOLERANCE_M, cache, *snap_data)
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
    if n_rides < MATCH_PARALLEL_MIN_RIDES or not GRAPH_CACHE_PATH.exists():
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
        new_rides[i : i + MATCH_CHUNK_SIZE]
        for i in range(0, len(new_rides), MATCH_CHUNK_SIZE)
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


# -- Render data ---------------------------------------------------------------


def _classify_hw(hw: str | list[str]) -> str:
    """Map an OSM highway tag value to an infrastructure class."""
    if isinstance(hw, list):
        classes = [INFRA_CLASS.get(h, INFRA_DEFAULT) for h in hw]
        return "bike" if "bike" in classes else INFRA_DEFAULT
    return INFRA_CLASS.get(hw, INFRA_DEFAULT)


def _build_render_cache(
    G: nx.MultiDiGraph,
) -> tuple[dict[tuple[int, int], list[tuple[float, float]]], dict[tuple[int, int], str]]:
    """Extract one canonical geometry and highway class per node pair.

    When multiple parallel edges exist between the same nodes (from
    composing bike/drive/walk networks), keeps only the shortest edge's
    geometry to avoid overlapping rendered lines.
    """
    print("Building render cache...")
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]] = {}
    edge_hw: dict[tuple[int, int], str] = {}
    edge_len: dict[tuple[int, int], float] = {}
    for u, v, _key, data in G.edges(data=True, keys=True):
        canon = (min(u, v), max(u, v))
        length = data.get("length", float("inf"))
        cls = _classify_hw(data.get("highway", ""))
        if canon in edge_geom:
            if cls == "bike" and edge_hw[canon] != "bike":
                edge_hw[canon] = "bike"
            if edge_len[canon] <= length:
                continue
        if "geometry" in data:
            xs, ys = data["geometry"].xy
            edge_geom[canon] = list(zip(xs, ys))
        else:
            edge_geom[canon] = [
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"]),
            ]
        edge_len[canon] = length
        if canon not in edge_hw:
            edge_hw[canon] = cls

    with RENDER_CACHE_PATH.open("wb") as f:
        pickle.dump((edge_geom, edge_hw), f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"  Cached {len(edge_geom):,} edge geometries")
    return edge_geom, edge_hw


def _get_render_data(
    G: nx.MultiDiGraph | None = None,
) -> tuple[
    dict[tuple[int, int], list[tuple[float, float]]],
    dict[tuple[int, int], str],
] | None:
    """Load render cache, or build it from graph if missing.

    A legacy cache (geometry only, no highway classes) is treated as
    missing so it rebuilds with classes rather than silently dropping
    the infrastructure data from the export.
    """
    if RENDER_CACHE_PATH.exists():
        with RENDER_CACHE_PATH.open("rb") as f:
            cached = pickle.load(f)
        if isinstance(cached, tuple):
            edge_geom, edge_hw = cached
            print(f"Loaded render cache ({len(edge_geom):,} edges)")
            return edge_geom, edge_hw
        print("Render cache in legacy format -- rebuilding")
    if G is None:
        return None
    return _build_render_cache(G)


# -- Rendering -----------------------------------------------------------------


def _make_fig(
    skeleton_lines: list[list[tuple[float, float]]],
) -> tuple[plt.Figure, plt.Axes]:
    """Create figure with OSM skeleton background."""
    fig, ax = plt.subplots(figsize=FIG_SIZE, facecolor="#0d0d0d")
    ax.set_facecolor("#0d0d0d")
    ax.set_aspect("equal")
    ax.axis("off")
    lc = LineCollection(skeleton_lines, colors="#2a2a2a", linewidths=0.3, zorder=1)
    ax.add_collection(lc)
    ax.autoscale_view()
    return fig, ax


def _save_fig(fig: plt.Figure, path: str) -> None:
    """Save figure to disk and close it."""
    fig.subplots_adjust(bottom=0.10)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"  Saved -> {path}")
    plt.close(fig)


def _render(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    state: dict[str, Any],
    *,
    skip_png: bool = SKIP_PNG_RENDER,
) -> None:
    """Render both coverage and frequency maps."""
    if skip_png:
        print("Skipping PNG render")
        return

    edge_counts = state["edge_counts"]

    if not edge_counts:
        print("No edges to render")
        return

    skeleton = list(edge_geom.values())
    cmap = plt.colormaps[COLORMAP]

    # -- Coverage map --
    print("Rendering coverage map...")
    fig, ax = _make_fig(skeleton)

    lines = [edge_geom[k] for k in edge_counts if k in edge_geom]
    lc = LineCollection(
        lines, colors=[cmap(1.0)], linewidths=LINE_WIDTH_MIN * 2, alpha=0.85, zorder=2
    )
    ax.add_collection(lc)
    _save_fig(fig, OUTPUT_PATH_UNWEIGHTED)

    # -- Frequency map --
    print("Rendering frequency map...")
    counts_arr = np.array(list(edge_counts.values()))
    max_count = counts_arr.max()
    print(f"  Max edge count: {max_count}")

    def scale(c: float) -> np.floating:
        return np.sqrt(c) / np.sqrt(max_count)

    fig, ax = _make_fig(skeleton)

    # Pass 1: faint underlay
    lines_u, colors_u = [], []
    for edge_key, count in edge_counts.items():
        if edge_key not in edge_geom:
            continue
        lines_u.append(edge_geom[edge_key])
        rgba = cmap(scale(count))
        colors_u.append((rgba[0], rgba[1], rgba[2], 0.25))
    ax.add_collection(LineCollection(lines_u, colors=colors_u, linewidths=LINE_WIDTH_MIN, zorder=2))

    # Pass 2: overlay, rare -> frequent
    sorted_items = sorted(
        ((k, v) for k, v in edge_counts.items() if k in edge_geom),
        key=lambda x: x[1],
    )
    lines_o, colors_o = [], []
    for edge_key, count in sorted_items:
        s = scale(count)
        lines_o.append(edge_geom[edge_key])
        rgba = cmap(s)
        colors_o.append((rgba[0], rgba[1], rgba[2], 0.9))
    ax.add_collection(
        LineCollection(lines_o, colors=colors_o, linewidths=LINE_WIDTH_MIN * 2, zorder=3)
    )

    # Colorbar
    sm = ScalarMappable(cmap=cmap, norm=mcolors.PowerNorm(gamma=0.5, vmin=0, vmax=max_count))
    sm.set_array([])
    cbar_ax = fig.add_axes([0.15, 0.06, 0.70, 0.015])
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_label("Number of rides", color="white", fontsize=11, labelpad=8)
    cbar.ax.xaxis.set_tick_params(color="white")
    plt.setp(cbar.ax.xaxis.get_ticklabels(), color="white", fontsize=9)
    cbar.outline.set_edgecolor("white")

    legend_elements = [
        Line2D([0], [0], color=cmap(scale(1)), linewidth=2, label="1 ride"),
        Line2D(
            [0],
            [0],
            color=cmap(scale(max(max_count // 4, 1))),
            linewidth=2,
            label=f"~{max_count // 4} rides",
        ),
        Line2D(
            [0],
            [0],
            color=cmap(scale(max_count // 2)),
            linewidth=2,
            label=f"~{max_count // 2} rides",
        ),
        Line2D([0], [0], color=cmap(1.0), linewidth=2, label=f"{max_count} rides"),
    ]
    legend = ax.legend(
        handles=legend_elements,
        loc="lower right",
        framealpha=0.15,
        facecolor="#0d0d0d",
        edgecolor="#555",
        labelcolor="white",
        fontsize=9,
        title="Ride frequency",
        title_fontsize=9,
    )
    legend.get_title().set_color("white")

    _save_fig(fig, OUTPUT_PATH_WEIGHTED)


# -- GeoJSON export ------------------------------------------------------------

MERGE_TOL_M = 20.0  # parallel features within this lateral distance may merge
MERGE_SAMPLE_M = 8.0  # geometry sampling interval for coverage tests
MERGE_HEADING_DEG = 30.0  # max heading difference (mod 180) for samples to match
MERGE_MUTUAL_COV = 0.75  # mutual coverage required to cluster two features
MERGE_ABSORB_COV = 0.95  # union coverage required to absorb a redundant span
MERGE_TRANSFER_COV = 0.80  # coverage needed to receive an absorbed feature's rides
MERGE_KEEP_COV = 0.97  # cluster extent that kept geometries must cover
MERGE_SNAP_M = 40.0  # reconnect feature endpoints to neighbours within this
MERGE_CONNECT_M = 6.0  # endpoints this close to another feature stay put

_M_PER_LON = 111_320 * np.cos(np.radians(40.73))
_M_PER_LAT = 110_540


def _sample_line(coords: list) -> list[tuple[float, float, float]]:
    """Sample (x_m, y_m, heading_deg mod 180) every MERGE_SAMPLE_M along a line."""
    pts = [(c[0] * _M_PER_LON, c[1] * _M_PER_LAT) for c in coords]

    def head(x0: float, y0: float, x1: float, y1: float) -> float:
        return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0

    out = [(pts[0][0], pts[0][1], head(*pts[0], *pts[1]))]
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        h = head(x0, y0, x1, y1)
        d = MERGE_SAMPLE_M - carry
        while d <= seg:
            t = d / seg
            out.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0), h))
            d += MERGE_SAMPLE_M
        carry = seg - (d - MERGE_SAMPLE_M)
    out.append((pts[-1][0], pts[-1][1], head(*pts[-2], *pts[-1])))
    return out


def _heading_diff(a: float, b: float) -> float:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def _sample_hits(
    samples: list[list[tuple[float, float, float]]],
) -> list[list[set[int]]]:
    """Find aligned neighbours for every sample of every feature.

    For each feature, for each of its samples, the set of OTHER features
    with an aligned sample within MERGE_TOL_M.
    """
    cell = MERGE_TOL_M
    grid: dict[tuple[int, int], list[tuple[int, float, float, float]]] = {}
    for i, pts in enumerate(samples):
        for (x, y, h) in pts:
            grid.setdefault((int(x // cell), int(y // cell)), []).append((i, x, y, h))
    tol_sq = MERGE_TOL_M * MERGE_TOL_M
    hits: list[list[set[int]]] = []
    for i, pts in enumerate(samples):
        rows = []
        for (x, y, h) in pts:
            gx, gy = int(x // cell), int(y // cell)
            hit: set[int] = set()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (j, jx, jy, jh) in grid.get((gx + dx, gy + dy), ()):
                        if j == i or j in hit:
                            continue
                        ddx, ddy = jx - x, jy - y
                        if (
                            ddx * ddx + ddy * ddy <= tol_sq
                            and _heading_diff(h, jh) <= MERGE_HEADING_DEG
                        ):
                            hit.add(j)
            rows.append(hit)
        hits.append(rows)
    return hits


def _dense_point_grid(
    features: list[dict[str, Any]], cell: float, step: float = 4.0
) -> dict[tuple[int, int], list[tuple[int, float, float, float, float]]]:
    """Build a spatial grid over all feature geometries.

    Cells hold (feature_idx, x_m, y_m, lon, lat) points sampled every
    `step` m along every feature's geometry.
    """
    grid: dict[tuple[int, int], list[tuple[int, float, float, float, float]]] = {}
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        pts = [(c[0] * _M_PER_LON, c[1] * _M_PER_LAT) for c in coords]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            n = max(1, int(seg // step))
            for k in range(n):
                t = k / n
                x, y = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
                grid.setdefault((int(x // cell), int(y // cell)), []).append(
                    (i, x, y, x / _M_PER_LON, y / _M_PER_LAT)
                )
        x, y = pts[-1]
        grid.setdefault((int(x // cell), int(y // cell)), []).append(
            (i, x, y, coords[-1][0], coords[-1][1])
        )
    return grid


def _snap_endpoints(features: list[dict[str, Any]]) -> None:
    """Reconnect merged features at junctions (in place).

    Merging keeps one geometry per parallel cluster, so adjacent features
    along a corridor can come from different parallel ways offset 10-20m
    laterally, leaving gaps and jogs at junctions.  Any endpoint that is
    not already touching another feature gets a short connector segment
    appended, bridging to the nearest point on a neighbouring feature
    within MERGE_SNAP_M.  The original endpoint is kept (extending rather
    than moving it) so the line's true geometry is not distorted.
    """
    cell = MERGE_SNAP_M
    grid = _dense_point_grid(features, cell)

    snap_sq = MERGE_SNAP_M * MERGE_SNAP_M
    connect_sq = MERGE_CONNECT_M * MERGE_CONNECT_M
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        for end in (0, -1):
            lon, lat = coords[end][0], coords[end][1]
            x, y = lon * _M_PER_LON, lat * _M_PER_LAT
            gx, gy = int(x // cell), int(y // cell)
            best = None
            best_d = snap_sq
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (j, jx, jy, jlon, jlat) in grid.get((gx + dx, gy + dy), ()):
                        if j == i:
                            continue
                        d = (jx - x) ** 2 + (jy - y) ** 2
                        if d < best_d:
                            best_d = d
                            best = (jlon, jlat)
            if best is not None and best_d > connect_sq:
                new_pt = (round(best[0], 6), round(best[1], 6))
                if end == 0:
                    coords.insert(0, new_pt)
                else:
                    coords.append(new_pt)


def _merge_parallel_features(
    features: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge GeoJSON features representing the same physical road corridor.

    Parallel OSM ways (divided carriageways, protected bike lanes, footways
    alongside roads) get map-matched separately, drawing 2+ lines 10-20m
    apart for the same street.  Merging is driven by heading-aligned sample
    coverage: fraction of one feature's points lying within MERGE_TOL_M of
    the other's.  Adjacent segments along a street have near-zero mutual
    coverage, so transitive chains cannot form the way endpoint matching
    allowed.

    Phase 1 clusters features that mutually cover each other and keeps a
    minimal set of member geometries covering the cluster extent.  Phase 2
    drops features almost entirely covered by the union of the others
    (e.g. long ways spanning several already-covered blocks).

    Each input feature must carry properties["_rides"]: the set of ride ids
    using that edge.  Merged counts are unions of these sets, so a ride is
    never double-counted no matter how features combine.  Returns finalized
    features with ride_count/ride_dates and no _rides.
    """
    tol_sq = MERGE_TOL_M * MERGE_TOL_M
    samples = [_sample_line(f["geometry"]["coordinates"]) for f in features]
    hits = _sample_hits(samples)

    # covered[i][j] = number of i's samples matched by feature j
    covered: list[dict[int, int]] = []
    for rows in hits:
        row_counts: dict[int, int] = {}
        for hit in rows:
            for j in hit:
                row_counts[j] = row_counts.get(j, 0) + 1
        covered.append(row_counts)

    def cov(i: int, j: int) -> float:
        return covered[i].get(j, 0) / len(samples[i])

    # -- Phase 1: cluster mutually-covering features (Union-Find) --
    parent = list(range(len(features)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    n_pairs = 0
    for i, row_counts in enumerate(covered):
        for j in row_counts:
            if j > i and cov(i, j) >= MERGE_MUTUAL_COV and cov(j, i) >= MERGE_MUTUAL_COV:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
                n_pairs += 1

    clusters: dict[int, list[int]] = {}
    for i in range(len(features)):
        clusters.setdefault(find(i), []).append(i)

    def line_len(i: int) -> float:
        pts = samples[i]
        return math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])

    merged: list[dict[str, Any]] = []
    for members in clusters.values():
        rides: set[str] = set()
        hw = INFRA_DEFAULT
        for i in members:
            rides |= features[i]["properties"]["_rides"]
            if features[i]["properties"].get("_hw") == "bike":
                hw = "bike"
        if len(members) == 1:
            keep = members
        else:
            # Greedy set-cover: keep member geometries until MERGE_KEEP_COV of
            # the cluster's sampled extent lies within MERGE_TOL_M of a kept
            # one.  Tight clusters keep a single line; staggered fragment
            # chains at junctions keep 2-3 instead of losing extent.
            all_pts = [(x, y) for i in members for (x, y, _h) in samples[i]]
            cov_flags = [False] * len(all_pts)
            keep = []
            # Prefer the most-ridden member as the kept geometry: rides stay
            # on the same physical way through consecutive blocks, so this
            # picks laterally-consistent representatives along a corridor
            # (keeping the longest instead makes adjacent blocks alternate
            # between parallel ways, fragmenting the drawn line).
            remaining = sorted(
                members,
                key=lambda m: (len(features[m]["properties"]["_rides"]), line_len(m)),
                reverse=True,
            )

            def mark(m: int, all_pts: list, cov_flags: list) -> int:
                gained = 0
                for k, (x, y) in enumerate(all_pts):
                    if cov_flags[k]:
                        continue
                    for (jx, jy, _jh) in samples[m]:
                        if (jx - x) ** 2 + (jy - y) ** 2 <= tol_sq:
                            cov_flags[k] = True
                            gained += 1
                            break
                return gained

            while remaining and (not keep or sum(cov_flags) / len(cov_flags) < MERGE_KEEP_COV):
                m = remaining.pop(0)
                if mark(m, all_pts, cov_flags) == 0 and keep:
                    continue
                keep.append(m)
        merged.extend(
            {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": features[m]["geometry"]["coordinates"],
                },
                "properties": {"_rides": set(rides), "_hw": hw},
            }
            for m in keep
        )

    # -- Phase 2: absorb spans redundant with the union of other features --
    m_samples = [_sample_line(f["geometry"]["coordinates"]) for f in merged]
    m_hits = _sample_hits(m_samples)
    m_covered: list[dict[int, int]] = []
    for rows in m_hits:
        row_counts = {}
        for hit in rows:
            for j in hit:
                row_counts[j] = row_counts.get(j, 0) + 1
        m_covered.append(row_counts)

    active = [True] * len(merged)
    n_absorbed = 0
    # Absorb low-ridership features first: a busy corridor must never be
    # dropped in favour of a near-empty parallel path that happens to cover
    # it -- by the time a busy feature is considered, its low-count cover
    # has already been absorbed, so the busy feature survives.
    order = sorted(
        range(len(merged)),
        key=lambda i: (len(merged[i]["properties"]["_rides"]), -len(m_samples[i])),
    )
    def union_cov(i: int) -> float:
        """Fraction of i's samples covered by comparably-ridden active features.

        Near-empty parallel paths must not count as cover for a busy
        corridor -- absorbing the corridor would leave its street drawn
        only by an almost-invisible low-count line.
        """
        min_rides = len(merged[i]["properties"]["_rides"]) / 2
        rows = m_hits[i]
        n = sum(
            1
            for hit in rows
            if any(
                active[j] and len(merged[j]["properties"]["_rides"]) >= min_rides
                for j in hit
            )
        )
        return n / len(rows)

    for i in order:
        if union_cov(i) < MERGE_ABSORB_COV:
            continue
        receivers = [
            j
            for j in {j for hit in m_hits[i] for j in hit if active[j]}
            if m_covered[j].get(i, 0) / len(m_samples[j]) >= MERGE_TRANSFER_COV
        ]
        if not receivers:
            continue
        active[i] = False
        n_absorbed += 1
        for j in receivers:
            merged[j]["properties"]["_rides"] |= merged[i]["properties"]["_rides"]

    # Restore pass: absorbing feature B can erode the cover that justified
    # absorbing feature A earlier, leaving a hole in the drawn corridor.
    # Reactivate any absorbed feature no longer covered by the final set.
    while True:
        restored = 0
        for i in order:
            if active[i]:
                continue
            if union_cov(i) < MERGE_ABSORB_COV:
                active[i] = True
                n_absorbed -= 1
                restored += 1
        if not restored:
            break

    survivors = [f for i, f in enumerate(merged) if active[i]]
    _snap_endpoints(survivors)

    out = []
    for f in survivors:
        rides = f["properties"].pop("_rides")
        f["properties"]["ride_count"] = len(rides)
        f["properties"]["ride_dates"] = sorted(r[:10] for r in rides)
        hw = f["properties"].pop("_hw", None)
        if hw is not None:
            f["properties"]["hw"] = hw
        out.append(f)

    print(
        f"  Merged parallel features: {len(features):,} -> {len(out):,} "
        f"({n_pairs:,} mutual pairs, {n_absorbed:,} redundant spans absorbed)"
    )
    return out


def _audit_merge(features: list[dict[str, Any]]) -> None:
    """Print post-merge regression metrics.

    Residual duplicate pairs: features >= 30m that still mutually cover each
    other (should have been merged).  Dangling endpoints: endpoints near
    another feature (within MERGE_SNAP_M) but not touching it (beyond
    MERGE_CONNECT_M) -- broken corridor joins.  Healthy baseline as of
    2026-07: ~34 duplicate pairs, ~0.4% dangling.
    """
    samples = [_sample_line(f["geometry"]["coordinates"]) for f in features]
    hits = _sample_hits(samples)
    covered: list[dict[int, int]] = []
    for rows in hits:
        row_counts: dict[int, int] = {}
        for hit in rows:
            for j in hit:
                row_counts[j] = row_counts.get(j, 0) + 1
        covered.append(row_counts)

    def length_m(i: int) -> float:
        pts = [
            (c[0] * _M_PER_LON, c[1] * _M_PER_LAT)
            for c in features[i]["geometry"]["coordinates"]
        ]
        return sum(
            math.hypot(x1 - x0, y1 - y0) for (x0, y0), (x1, y1) in zip(pts, pts[1:])
        )

    lens = [length_m(i) for i in range(len(features))]
    dup_pairs = 0
    dup_km = 0.0
    for i, row_counts in enumerate(covered):
        for j, n in row_counts.items():
            if j <= i or min(lens[i], lens[j]) < 30.0:
                continue
            ci = n / len(samples[i])
            cj = covered[j].get(i, 0) / len(samples[j])
            if ci >= MERGE_MUTUAL_COV and cj >= MERGE_MUTUAL_COV:
                dup_pairs += 1
                dup_km += min(lens[i], lens[j]) / 1000

    grid = _dense_point_grid(features, MERGE_SNAP_M)
    snap_sq = MERGE_SNAP_M * MERGE_SNAP_M
    connect_sq = MERGE_CONNECT_M * MERGE_CONNECT_M
    dangling = 0
    for i, f in enumerate(features):
        coords = f["geometry"]["coordinates"]
        for end in (0, -1):
            x = coords[end][0] * _M_PER_LON
            y = coords[end][1] * _M_PER_LAT
            gx, gy = int(x // MERGE_SNAP_M), int(y // MERGE_SNAP_M)
            best = snap_sq
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for (j, jx, jy, _jlon, _jlat) in grid.get((gx + dx, gy + dy), ()):
                        if j == i:
                            continue
                        d = (jx - x) ** 2 + (jy - y) ** 2
                        best = min(best, d)
            if connect_sq < best < snap_sq:
                dangling += 1

    total_ends = 2 * len(features)
    pct = 100 * dangling / total_ends if total_ends else 0.0
    print(
        f"  Audit: {dup_pairs:,} residual duplicate pairs ({dup_km:.1f} km), "
        f"{dangling:,}/{total_ends:,} dangling endpoints ({pct:.1f}%)"
    )
    if dup_pairs > 100 or pct > 2.0:
        print("  WARNING: merge regression suspected -- metrics well above baseline")


def _export_geojson(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    state: dict[str, Any],
    edge_hw: dict[tuple[int, int], str] | None = None,
) -> None:
    """Export ridden edges as GeoJSON for the interactive Leaflet map."""
    edge_counts = state["edge_counts"]
    if not edge_counts:
        return

    edge_rides: dict[tuple[int, int], list[str]] = state.get("edge_rides", {})

    features = []
    for edge_key in edge_counts:
        if edge_key not in edge_geom:
            continue
        coords = [(round(lon, 6), round(lat, 6)) for lon, lat in edge_geom[edge_key]]
        props: dict[str, Any] = {"_rides": set(edge_rides.get(edge_key, ()))}
        if edge_hw:
            props["_hw"] = edge_hw.get(edge_key, INFRA_DEFAULT)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coords},
                "properties": props,
            }
        )

    features = _merge_parallel_features(features)
    _audit_merge(features)
    features.sort(key=lambda f: f["properties"]["ride_count"])

    max_count = max((f["properties"]["ride_count"] for f in features), default=0)

    # Encode per-ride dates as indices into one global date list: repeated
    # "YYYY-MM-DD" strings dominate the uncompressed payload otherwise.
    # ride_count is dropped from features since it equals len(ride_dates).
    all_dates = sorted({d for f in features for d in f["properties"]["ride_dates"]})
    date_idx = {d: i for i, d in enumerate(all_dates)}
    for f in features:
        props = f["properties"]
        props["ride_dates"] = [date_idx[d] for d in props["ride_dates"]]
        del props["ride_count"]
        if props.get("hw") == INFRA_DEFAULT:
            del props["hw"]

    km_by_class: dict[str, float] = {}
    total_km = 0.0
    for f in features:
        length = sum(
            math.hypot((lon1 - lon0) * _M_PER_LON, (lat1 - lat0) * _M_PER_LAT)
            for (lon0, lat0), (lon1, lat1) in zip(
                f["geometry"]["coordinates"], f["geometry"]["coordinates"][1:]
            )
        )
        total_km += length
        cls = f["properties"].get("hw", INFRA_DEFAULT)
        km_by_class[cls] = km_by_class.get(cls, 0) + length
    total_km /= 1000
    km_by_class = {k: round(v / 1000, 1) for k, v in km_by_class.items()}

    rides_per_year: dict[str, int] = {}
    for fname in sorted(state["processed_files"]):
        rides_per_year[fname[:4]] = rides_per_year.get(fname[:4], 0) + 1

    geojson = {
        "type": "FeatureCollection",
        "properties": {
            "total_rides": len(state["processed_files"]),
            "total_edges": len(features),
            "max_count": max_count,
            "total_km": round(total_km, 1),
            "km_by_class": km_by_class,
            "rides_per_year": rides_per_year,
            "riding": _riding_summary(state.get("ride_stats", {})),
            "dates": all_dates,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "features": features,
    }

    GEOJSON_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(geojson, separators=(",", ":")).encode()
    with gzip.open(GEOJSON_OUTPUT_PATH, "wb", compresslevel=9) as f:
        f.write(raw)

    raw_mb = len(raw) / 1_048_576
    gz_mb = GEOJSON_OUTPUT_PATH.stat().st_size / 1_048_576
    print(
        f"  Exported {len(features):,} edges to {GEOJSON_OUTPUT_PATH} ({raw_mb:.1f} MB -> {gz_mb:.1f} MB gzipped)"
    )


# -- Main pipeline -------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line overrides for the module-level config."""
    parser = argparse.ArgumentParser(
        description="Process bike ride GPS logs into an interactive frequency map."
    )
    parser.add_argument(
        "--sample", type=int, metavar="N", help="process only the first N ride files"
    )
    parser.add_argument(
        "--rides", nargs="+", metavar="FILE", help="process only these ride CSV filenames"
    )
    parser.add_argument("--no-png", action="store_true", help="skip rendering the static PNG maps")
    parser.add_argument(
        "--workers",
        type=int,
        metavar="N",
        help="worker processes for map matching (1 = sequential)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the full bike route pipeline."""
    t0 = time.time()

    args = _parse_args(argv)
    sample_size = args.sample if args.sample is not None else SAMPLE_SIZE
    ride_files = args.rides if args.rides is not None else RIDE_FILES
    skip_png = SKIP_PNG_RENDER or args.no_png
    if args.workers is not None:
        # _match_worker_count reads this; the env var also reaches workers
        os.environ["MATCH_WORKERS"] = str(args.workers)

    # 1. Load state
    state = _load_state()

    # 2. Find new rides (exclude already-processed and already-skipped files)
    all_files = sorted(f.name for f in Path(RIDES_FOLDER).iterdir() if f.suffix == ".csv")
    if ride_files is not None:
        ride_set = set(ride_files)
        all_files = [f for f in all_files if f in ride_set]
    elif sample_size is not None:
        all_files = all_files[:sample_size]
    known = state["processed_files"] | state.get("skipped_files", set())
    new_files = [f for f in all_files if f not in known]

    n_total = len(all_files)
    n_processed = len(state["processed_files"])
    n_skipped = len(state.get("skipped_files", set()))
    n_new = len(new_files)

    print(f"Rides: {n_total} total, {n_processed} NYC, {n_skipped} non-NYC, {n_new} new")

    if n_new <= 10 and n_new > 0:
        for f in new_files:
            print(f"  + {f}")

    # Backfill time/distance stats for rides processed before stats existed
    n_backfilled = _backfill_ride_stats(state)
    if n_backfilled:
        print(f"Backfilled ride stats for {n_backfilled} ride(s)")
        _save_state(state)

    # 3. No new rides -- re-render from cache
    if n_new == 0:
        if not state["edge_counts"]:
            print("No rides found")
            return

        render_data = _get_render_data()
        if render_data is None:
            print("Render cache missing -- loading graph to rebuild...")
            G = _load_graph([], state)
            render_data = _build_render_cache(G)
        edge_geom, edge_hw = render_data
        _render(edge_geom, state, skip_png=skip_png)
        _export_geojson(edge_geom, state, edge_hw)

        print(f"\nDone in {time.time() - t0:.1f}s (no new rides)")
        return

    # 4. Load, filter to NYC, and resample new rides
    new_rides, n_non_nyc = _load_and_resample(new_files)
    if n_non_nyc:
        # Track non-NYC files so they aren't re-checked next run
        skipped_files = state.get("skipped_files", set())
        nyc_fnames = {f for f, _ in new_rides}
        skipped_files |= {f for f in new_files if f not in nyc_fnames}
        state["skipped_files"] = skipped_files
        print(f"  Filtered out {n_non_nyc} non-NYC ride(s)")

    if not new_rides:
        print("No new NYC rides to process")
        _save_state(state)
        if state["edge_counts"]:
            render_data = _get_render_data()
            if render_data is None:
                print("Render cache missing -- loading graph to rebuild...")
                G = _load_graph([], state)
                render_data = _build_render_cache(G)
            edge_geom, edge_hw = render_data
            _render(edge_geom, state, skip_png=skip_png)
            _export_geojson(edge_geom, state, edge_hw)
        print(f"\nDone in {time.time() - t0:.1f}s")
        return

    total_pts = sum(len(c) for _, c in new_rides)
    print(f"Resampled {len(new_rides)} NYC rides ({total_pts:,} points)")

    # 5. Load or fetch graph
    G = _load_graph(new_rides, state)

    # 6. Build spatial index once for all rides
    snap_tree, tree_node_ids, mean_lat_rad, node_xs, node_ys, node_idx_map, adj, edge_hw = (
        _build_snap_tree(G)
    )

    # 7. Map-match new rides
    print("Map-matching...")
    route_cache = _load_route_cache()
    print(f"  Route cache: {len(route_cache):,} entries loaded")

    results: list[tuple[str, list[tuple[int, int]], int]] | None = None
    n_workers = _match_worker_count(len(new_rides))
    if n_workers > 1:
        print(f"  Matching on {n_workers} worker processes")
        try:
            results = _match_rides_parallel(new_rides, route_cache, n_workers)
        except Exception as e:
            print(f"  Parallel matching failed ({e!r}) -- falling back to sequential")
            results = None

    if results is None:
        results = []
        for i, (fname, coords) in enumerate(new_rides, 1):
            edges, skipped = _map_match_ride(
                G,
                coords,
                SNAP_TOLERANCE_M,
                route_cache,
                snap_tree,
                tree_node_ids,
                mean_lat_rad,
                node_xs,
                node_ys,
                node_idx_map,
                adj,
                edge_hw,
            )
            results.append((fname, edges, skipped))
            if i % 20 == 0 or i == len(new_rides):
                print(f"  {i}/{len(new_rides)} rides (route cache: {len(route_cache):,} entries)")

    # Apply results in filename order so state is identical regardless of
    # how matching was scheduled.
    total_skipped = 0
    for fname, edges, skipped in sorted(results):
        total_skipped += skipped
        edge_rides = state.setdefault("edge_rides", {})
        for edge in set(edges):
            state["edge_counts"][edge] = state["edge_counts"].get(edge, 0) + 1
            edge_rides.setdefault(edge, []).append(fname)
        state["processed_files"].add(fname)

    if total_skipped:
        print(f"  Skipped {total_skipped:,} segments > {MAX_ROUTING_DISTANCE_M}m")

    _save_route_cache(route_cache)
    print(f"  Route cache: {len(route_cache):,} entries saved")

    # 8. Compute stats for the newly processed rides, save state
    _backfill_ride_stats(state)
    _save_state(state)

    # 9. Render
    render_data = _get_render_data(G)
    assert render_data is not None
    edge_geom, edge_hw = render_data
    _render(edge_geom, state, skip_png=skip_png)
    _export_geojson(edge_geom, state, edge_hw)

    # Summary
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  {len(state['processed_files'])} NYC rides")
    print(f"  {len(state.get('skipped_files', set()))} non-NYC rides skipped")
    print(f"  {len(state['edge_counts']):,} unique edges")


if __name__ == "__main__":
    main()
