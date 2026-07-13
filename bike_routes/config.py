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
MAX_GPS_GAP_M = 300  # split ride into segments at raw GPS gaps larger than this
HEADING_PENALTY = 0.15  # metres of snap penalty per degree of edge-heading mismatch
LOOP_WINDOW = 6  # remove short loops (A->...->A) within this many nodes
LOOP_MAX_DETOUR_M = 50  # only remove loops where detour nodes are within this distance of anchor
DENSIFY_M = 150  # add virtual snap points on edges longer than this (metres)
MATCH_PARALLEL_MIN_RIDES = 50  # match on worker processes when this many new rides
MATCH_CHUNK_SIZE = 20  # rides per parallel work unit
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
GRAPH_CACHE_PATH = Path("osm_graph_cache.pkl")
STATE_CACHE_PATH = Path("state.pkl")
RENDER_CACHE_PATH = Path("render_cache.pkl")
ROUTE_CACHE_PATH = Path("route_cache.pkl")
CACHE_VERSIONS_PATH = Path("cache_versions.json")
RENDER_CACHE_FORMAT = "hw-raw-v1"
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
M_PER_LON = 111_320 * np.cos(np.radians(40.73))
M_PER_LAT = 110_540
