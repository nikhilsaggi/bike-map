"""
Bike Route Frequency Map -- Incremental Pipeline
=================================================
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

import hashlib
import json
import os
import pickle
import time

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

# Rides whose median coordinate falls outside this bbox are skipped entirely.
NYC_BBOX = (40.49, -74.30, 41.0, -73.60)  # (lat_min, lon_min, lat_max, lon_max)

# -- Config: Rendering ---------------------------------------------------------

FIG_SIZE = (14, 18)
COLORMAP = "plasma"
LINE_WIDTH_MIN = 0.4
LINE_WIDTH_MAX = 6.0
OUTPUT_PATH_UNWEIGHTED = "bike_routes_coverage.png"
OUTPUT_PATH_WEIGHTED = "bike_routes_frequency.png"

# -- Config: Cache paths -------------------------------------------------------

GRAPH_CACHE_PATH = "osm_graph_cache.pkl"
STATE_CACHE_PATH = "state.pkl"
RENDER_CACHE_PATH = "render_cache.pkl"
ROUTE_CACHE_PATH = "route_cache.pkl"


# -- Cache management ----------------------------------------------------------


def _processing_config():
    """Params that affect map-matching results (change triggers full reprocess)."""
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
    }


def _config_hash():
    return hashlib.sha1(json.dumps(_processing_config(), sort_keys=True).encode()).hexdigest()


def _empty_state():
    return {
        "config_hash": _config_hash(),
        "config": _processing_config(),
        "processed_files": set(),
        "skipped_files": set(),
        "edge_counts": {},
        "graph_bbox": None,
    }


def _load_state():
    """Load cached state, invalidating if processing config changed."""
    if not os.path.exists(STATE_CACHE_PATH):
        return _empty_state()

    with open(STATE_CACHE_PATH, "rb") as f:
        state = pickle.load(f)

    if state.get("config_hash") == _config_hash():
        return state

    old_config = state.get("config", {})
    if sorted(old_config.get("network_types", [])) != sorted(NETWORK_TYPES):
        print("Network types changed -- invalidating graph + render caches")
        for p in [GRAPH_CACHE_PATH, RENDER_CACHE_PATH, ROUTE_CACHE_PATH]:
            if os.path.exists(p):
                os.remove(p)

    if os.path.exists(ROUTE_CACHE_PATH):
        os.remove(ROUTE_CACHE_PATH)
    print("Processing config changed -- full reprocess required")
    return _empty_state()


def _save_state(state):
    state["config_hash"] = _config_hash()
    state["config"] = _processing_config()
    with open(STATE_CACHE_PATH, "wb") as f:
        pickle.dump(state, f, protocol=pickle.HIGHEST_PROTOCOL)


def _load_route_cache():
    if not os.path.exists(ROUTE_CACHE_PATH):
        return {}
    try:
        with open(ROUTE_CACHE_PATH, "rb") as f:
            return pickle.load(f)
    except Exception:
        return {}


def _save_route_cache(route_cache):
    with open(ROUTE_CACHE_PATH, "wb") as f:
        pickle.dump(route_cache, f, protocol=pickle.HIGHEST_PROTOCOL)


# -- Data processing -----------------------------------------------------------


def haversine_m(lat1, lon1, lat2, lon2):
    """Distance in metres between WGS-84 points (scalar or array)."""
    R = 6_371_000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))


def resample_ride_by_distance(coords, spacing_m):
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


def _split_at_gaps(coords, max_gap_m):
    """Split (N,2) [lat,lon] into sub-arrays wherever consecutive points exceed max_gap_m."""
    if len(coords) < 2:
        return [coords]
    dists = haversine_m(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    gap_idx = np.where(dists > max_gap_m)[0] + 1
    if len(gap_idx) == 0:
        return [coords]
    return np.split(coords, gap_idx)


def _is_nyc_ride(coords):
    """True if any point in the ride falls within NYC_BBOX."""
    lat_min, lon_min, lat_max, lon_max = NYC_BBOX
    in_bbox = (
        (coords[:, 0] >= lat_min)
        & (coords[:, 0] <= lat_max)
        & (coords[:, 1] >= lon_min)
        & (coords[:, 1] <= lon_max)
    )
    return in_bbox.any()


def _load_and_resample(filenames):
    """Load CSVs, filter to NYC, split at GPS gaps, and resample.
    Returns (nyc_rides, skipped_non_nyc) where nyc_rides is [(filename, coords)].
    A single file may produce multiple entries if it has GPS gaps."""
    rides = []
    non_nyc = 0
    for f in filenames:
        path = os.path.join(RIDES_FOLDER, f)
        data = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(0, 1))
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


# -- Graph management ----------------------------------------------------------


def _compute_bbox(all_pts):
    """Bounding box from ride coordinates with IQR outlier removal + NYC clamp."""
    lats, lons = all_pts[:, 0], all_pts[:, 1]

    def iqr_bounds(arr, k=3.0):
        q1, q3 = np.percentile(arr, 25), np.percentile(arr, 75)
        iqr = q3 - q1
        return q1 - k * iqr, q3 + k * iqr

    lat_lo, lat_hi = iqr_bounds(lats)
    lon_lo, lon_hi = iqr_bounds(lons)
    mask = (lats >= lat_lo) & (lats <= lat_hi) & (lons >= lon_lo) & (lons <= lon_hi)
    n_dropped = int((~mask).sum())
    if n_dropped:
        print(f"  Dropped {n_dropped:,} outlier points before bbox calculation")
    clean = all_pts[mask]

    lat_min = max(clean[:, 0].min(), 40.538128)
    lat_max = min(clean[:, 0].max(), 40.95)
    lon_min = max(clean[:, 1].min(), -74.050255)
    lon_max = min(clean[:, 1].max(), -73.65)

    buf = 0.005
    return (lon_min - buf, lat_min - buf, lon_max + buf, lat_max + buf)


def _fetch_graph(bbox):
    """Fetch OSM networks for bbox, merge, and cache."""
    graphs = []
    for nt in NETWORK_TYPES:
        print(f"  Fetching OSM '{nt}' network...")
        g = ox.graph_from_bbox(bbox, network_type=nt, simplify=True)
        graphs.append(g)
        print(f"    {g.number_of_nodes():,} nodes, {g.number_of_edges():,} edges")

    G = nx.compose_all(graphs)
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    print(f"  Merged: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    with open(GRAPH_CACHE_PATH, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"  Cached to {GRAPH_CACHE_PATH}")
    return G


def _load_graph(new_rides, state):
    """Load graph from cache or fetch from OSM."""
    if os.path.exists(GRAPH_CACHE_PATH):
        print(f"Loading graph from {GRAPH_CACHE_PATH}...")
        with open(GRAPH_CACHE_PATH, "rb") as f:
            G = pickle.load(f)
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
        if os.path.exists(p):
            os.remove(p)

    return _fetch_graph(bbox)


# -- Map-matching --------------------------------------------------------------


def _path_to_edges(G, path_nodes, max_dist):
    """Convert path nodes to canonical edge tuples in a single pass.
    Returns edge list or None if cumulative length exceeds max_dist."""
    length = 0.0
    result = []
    for a, b in zip(path_nodes[:-1], path_nodes[1:]):
        edge_data = G[a][b]
        key = min(edge_data, key=lambda k: edge_data[k].get("length", 0))
        length += edge_data[key].get("length", 0)
        if length > max_dist:
            return None
        result.append((min(a, b), max(a, b), key))
    return result


def _build_snap_tree(G):
    """Build a cKDTree and adjacency data for edge-based snapping.
    Long edges (>150m) get virtual intermediate points so bridge/highway
    GPS points can find the edge even when endpoints are far away."""
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

    DENSIFY_M = 150
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
    G,
    coords,
    snap_tol,
    route_cache,
    snap_tree,
    tree_node_ids,
    mean_lat_rad,
    node_xs,
    node_ys,
    node_idx_map,
    adj,
    edge_hw,
):
    """Snap points to nearest OSM edges, route between them. Returns (edges, skipped_count)."""
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
    pairs = []
    pending_idx = []
    origs, dests = [], []

    for i in range(len(deduped) - 1):
        u, v = deduped[i], deduped[i + 1]
        canon = (min(u, v), max(u, v))

        if G.has_edge(u, v):
            ed = G[u][v]
            key = min(ed, key=lambda k: ed[k].get("length", 0))
            pairs.append([(canon[0], canon[1], key)])
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
                pairs.append("PENDING")
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
    edges = []
    skipped = 0
    for r in pairs:
        if r is not None:
            edges.extend(r)
        else:
            skipped += 1
    return edges, skipped


# -- Render data ---------------------------------------------------------------


def _build_render_cache(G):
    """Extract canonical edge geometries from graph for rendering."""
    print("Building render cache...")
    edge_geom = {}
    for u, v, key, data in G.edges(data=True, keys=True):
        canon = (min(u, v), max(u, v), key)
        if canon in edge_geom:
            continue
        if "geometry" in data:
            xs, ys = data["geometry"].xy
            edge_geom[canon] = list(zip(xs, ys))
        else:
            edge_geom[canon] = [
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"]),
            ]

    with open(RENDER_CACHE_PATH, "wb") as f:
        pickle.dump(edge_geom, f, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"  Cached {len(edge_geom):,} edge geometries")
    return edge_geom


def _get_render_data(G=None):
    """Load render cache, or build it from graph if missing."""
    if os.path.exists(RENDER_CACHE_PATH):
        with open(RENDER_CACHE_PATH, "rb") as f:
            edge_geom = pickle.load(f)
        print(f"Loaded render cache ({len(edge_geom):,} edges)")
        return edge_geom
    if G is None:
        return None
    return _build_render_cache(G)


# -- Rendering -----------------------------------------------------------------


def _make_fig(skeleton_lines):
    """Create figure with OSM skeleton background."""
    fig, ax = plt.subplots(figsize=FIG_SIZE, facecolor="#0d0d0d")
    ax.set_facecolor("#0d0d0d")
    ax.set_aspect("equal")
    ax.axis("off")
    lc = LineCollection(skeleton_lines, colors="#2a2a2a", linewidths=0.3, zorder=1)
    ax.add_collection(lc)
    ax.autoscale_view()
    return fig, ax


def _save_fig(fig, path):
    fig.subplots_adjust(bottom=0.10)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"  Saved -> {path}")
    plt.close(fig)



def _render(edge_geom, state):
    """Render both coverage and frequency maps."""
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

    def scale(c):
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


# -- Main pipeline -------------------------------------------------------------


def main():
    t0 = time.time()

    # 1. Load state
    state = _load_state()

    # 2. Find new rides (exclude already-processed and already-skipped files)
    all_files = sorted(f for f in os.listdir(RIDES_FOLDER) if f.endswith(".csv"))
    if RIDE_FILES is not None:
        ride_set = set(RIDE_FILES)
        all_files = [f for f in all_files if f in ride_set]
    elif SAMPLE_SIZE is not None:
        all_files = all_files[:SAMPLE_SIZE]
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

    # 3. No new rides -- re-render from cache
    if n_new == 0:
        if not state["edge_counts"]:
            print("No rides found")
            return

        edge_geom = _get_render_data()
        if edge_geom is None:
            print("Render cache missing -- loading graph to rebuild...")
            G = _load_graph([], state)
            edge_geom = _build_render_cache(G)
        _render(edge_geom, state)

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
            edge_geom = _get_render_data()
            if edge_geom is None:
                print("Render cache missing -- loading graph to rebuild...")
                G = _load_graph([], state)
                edge_geom = _build_render_cache(G)
            _render(edge_geom, state)
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
    total_skipped = 0
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
        total_skipped += skipped
        for edge in set(edges):
            state["edge_counts"][edge] = state["edge_counts"].get(edge, 0) + 1
        state["processed_files"].add(fname)
        if i % 20 == 0 or i == len(new_rides):
            print(f"  {i}/{len(new_rides)} rides (route cache: {len(route_cache):,} entries)")

    if total_skipped:
        print(f"  Skipped {total_skipped:,} segments > {MAX_ROUTING_DISTANCE_M}m")

    _save_route_cache(route_cache)
    print(f"  Route cache: {len(route_cache):,} entries saved")

    # 8. Save state
    _save_state(state)

    # 9. Render
    edge_geom = _get_render_data(G)
    _render(edge_geom, state)

    # Summary
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  {len(state['processed_files'])} NYC rides")
    print(f"  {len(state.get('skipped_files', set()))} non-NYC rides skipped")
    print(f"  {len(state['edge_counts']):,} unique edges")


if __name__ == "__main__":
    main()
