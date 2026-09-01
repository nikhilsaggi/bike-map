"""Pipeline configuration: processing, rendering, merge, and cache settings."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

RIDES_FOLDER = "rides"
SAMPLE_SIZE = None  # set to e.g. 100 for quick preview, None for all rides
RIDE_FILES = None  # set to e.g. ["2024-06-19_13-35-52_-0400.csv"] to process specific rides
RESAMPLE_SPACING_M = 20
NETWORK_TYPES = ["bike", "drive", "walk"]
SNAP_TOLERANCE_M = 80
MAX_ROUTING_DISTANCE_M = 2500
MAX_ROUTE_DETOUR = 3.0  # reject routes longer than this multiple of straight-line distance
MAX_GPS_GAP_M = 300  # split ride into segments at raw GPS gaps larger than this
HEADING_PENALTY = 0.15  # metres of snap penalty per degree of edge-heading mismatch
LOOP_WINDOW = 6  # remove short loops (A->...->A) within this many nodes
LOOP_MAX_DETOUR_M = 50  # only remove loops where detour nodes are within this distance of anchor
DENSIFY_M = 150  # add virtual snap points on edges longer than this (metres)
MATCH_PARALLEL_MIN_RIDES = 20  # match on worker processes when this many new rides
MATCH_CHUNK_SIZE = 10  # rides per parallel work unit
CHECKPOINT_EVERY_RIDES = 100  # save state mid-match after this many rides land

# Matcher selection: "hmm" (leuvenmapmatching Viterbi; issue #11) or the
# original "heuristic" edge-snapping matcher (kept for comparison/fallback).
MATCHER = "hmm"
HMM_MAX_DIST = 80  # max GPS-to-edge distance considered (metres)
HMM_OBS_NOISE = 15  # expected GPS noise for emitting states (metres)
HMM_OBS_NOISE_NE = 30  # noise for non-emitting (interpolated) states
HMM_MIN_PROB_NORM = 0.001  # prune lattice states below this normalized prob
HMM_LATTICE_WIDTH = 8  # Viterbi beam width on the first attempt
HMM_LATTICE_WIDTH_RETRY = 24  # widened beam when a match stops early
HMM_FAIL_SKIP_POINTS = 5  # points (~100m) skipped past a dead-end before rematching

HW_PENALTY = {
    "cycleway": -5,
    "path": -2,
    "track": -1,
    "footway": 25,
    "steps": 40,
    "pedestrian": 5,
    "service": 3,
    "motorway": 10,
    "motorway_link": 5,
    "trunk": 2,
    "trunk_link": 2,
}
INFRA_CLASS: dict[str, str] = {
    "cycleway": "bike",
    "path": "bike",
    "track": "bike",
}
INFRA_DEFAULT = "road"
COVERAGE_EXCLUDE = {
    "footway",
    "steps",
    "pedestrian",
    "corridor",
    "elevator",
    "escalator",
    "motorway",
    "motorway_link",
    "service",
    "construction",
    "proposed",
}
NYC_BBOX = (40.49, -74.30, 41.0, -73.60)  # (lat_min, lon_min, lat_max, lon_max)
FIG_SIZE = (14, 18)
COLORMAP = "plasma"
LINE_WIDTH_MIN = 0.4
LINE_WIDTH_MAX = 6.0
OUTPUT_PATH_UNWEIGHTED = "bike_routes_coverage.png"
OUTPUT_PATH_WEIGHTED = "bike_routes_frequency.png"
GEOJSON_OUTPUT_PATH = Path("docs/rides.geojson.gz")
SKIP_PNG_RENDER = os.environ.get("SKIP_PNG_RENDER") == "1"

# -- Generated caches -------------------------------------------------------
# All auto-managed; delete any (or the whole directory) to force a rebuild.
# Paths are relative, so they resolve against the working directory the
# pipeline is run from -- the repo root in normal use, tmp_path under test.
CACHE_DIR = Path("cache")
GRAPH_CACHE_PATH = CACHE_DIR / "osm_graph_cache.pkl"
HMM_MAP_CACHE_PATH = CACHE_DIR / "hmm_map_cache.pkl"
STATE_CACHE_PATH = CACHE_DIR / "state.pkl"
RENDER_CACHE_PATH = CACHE_DIR / "render_cache.pkl"
ROUTE_CACHE_PATH = CACHE_DIR / "route_cache.pkl"
CACHE_VERSIONS_PATH = CACHE_DIR / "cache_versions.json"
WEATHER_CACHE_PATH = CACHE_DIR / "weather_cache.json"


RENDER_CACHE_FORMAT = "hw-name-v1"  # bump invalidates render_cache.pkl (rebuilt from the graph)
MERGE_TOL_M = 20.0  # parallel features within this lateral distance may merge
MERGE_SAMPLE_M = 8.0  # geometry sampling interval for coverage tests
MERGE_HEADING_DEG = 30.0  # max heading difference (mod 180) for samples to match
MERGE_MUTUAL_COV = 0.75  # mutual coverage required to cluster two features
MERGE_ABSORB_COV = 0.95  # union coverage required to absorb a redundant span
MERGE_TRANSFER_COV = 0.80  # coverage needed to receive an absorbed feature's rides
MERGE_KEEP_COV = 0.97  # cluster extent that kept geometries must cover
MERGE_SNAP_M = 40.0  # reconnect feature endpoints to neighbours within this
MERGE_CONNECT_M = 6.0  # endpoints this close to another feature stay put
MERGE_MOVE_M = 15.0  # endpoint gaps up to this close by moving the endpoint
RING_MAX_GAP_M = 15.0  # endpoints closer than this make a feature a closed ring
RING_MIN_LEN_M = 30.0  # shorter features are corridor pieces, not rings
RING_MAX_LEN_M = 300.0  # rings up to this perimeter may be dropped as redundant
RING_NEAR_M = 40.0  # rings must hug covering features within this distance

# -- Direction-split edge speed and traversal counts ------------------------
# Backfilled from the ride CSVs; NOT part of _processing_config.  Bumping
# SPEED_VERSION discards edge_speed/edge_traversals/speed_rides and recomputes
# both; it is the invalidation lever for this data, deliberately separate from
# the config hash so an algorithm change here never triggers a full rematch.
SPEED_VERSION = 3  # 3: per-ride traversal counts (2: per-chunk speed records)
SPEED_SAMPLE_M = 5.0  # edge polyline densification for the projection index
SPEED_SNAP_M = 25.0  # max GPS-to-edge distance for a fix to count as on-edge
SPEED_HYSTERESIS = 1.5  # stay on the previous edge within this factor of the best
SPEED_MAX_FIX_GAP_S = 30.0  # split a pass at a recording gap (auto-pause, signal loss)
SPEED_MIN_PASS_M = 25.0  # a pass must cover this, or most of a shorter edge
SPEED_REVERSAL_M = 15.0  # along-line backtrack that counts as turning around
# rather than GPS wobble; splits an out-and-back into one pass per direction
SPEED_MAX_KMH = 60.0  # reject implausible passes (GPS jump / misassignment)
SPEED_MOVING_KMH = 2.4  # below this a fix counts as stopped (~1.5 mph)
SPEED_CHUNK_M = 150.0  # long edges are measured in chunks this size (a bridge
# averages its own climb against its descent when treated as one segment)
SPEED_MAX_CHUNKS = 24  # cap chunks per edge so one long way cannot dominate the scan
SPEED_MIN_DIST_M = 50.0  # per-direction distance needed before a speed is usable
# Comparing the two directions against each other is a strong claim, so it
# needs more evidence than a single noisy pass would give.
SPEED_SPLIT_PASSES = 3  # passes needed in EACH direction before a chunk is ranked
SPEED_CORRIDOR_MIN_M = 250.0  # a shorter same-sign run is an anecdote, not a corridor
SPEED_CORRIDOR_N = 10  # corridors listed in the stats panel
# Traversal counting reuses the speed pass detector, but must not inherit its
# split at recording gaps: a rider who stops mid-block for five minutes rode
# that block once.  Two same-direction passes on one edge are the same
# traversal when the second resumes within this distance of where the first
# stopped; a second lap re-enters from the far end (median edge is 63 m).
TRAVERSAL_RESUME_M = 30.0
M_PER_LON = 111_320 * np.cos(np.radians(40.73))
M_PER_LAT = 110_540
