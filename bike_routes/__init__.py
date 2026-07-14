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

from . import cache, cli, config, export, gps, graph, hmm, matching, merge, render, ride_stats
from .cache import (
    _config_hash,
    _empty_state,
    _graph_cache_valid,
    _lib_versions,
    _load_route_cache,
    _load_state,
    _processing_config,
    _save_route_cache,
    _save_state,
    _write_cache_versions,
)
from .cli import (
    _parse_args,
    main,
)
from .config import (
    CACHE_VERSIONS_PATH,
    COLORMAP,
    COVERAGE_EXCLUDE,
    DENSIFY_M,
    FIG_SIZE,
    GEOJSON_OUTPUT_PATH,
    GRAPH_CACHE_PATH,
    HEADING_PENALTY,
    HW_PENALTY,
    INFRA_CLASS,
    INFRA_DEFAULT,
    LINE_WIDTH_MAX,
    LINE_WIDTH_MIN,
    LOOP_MAX_DETOUR_M,
    LOOP_WINDOW,
    M_PER_LAT,
    M_PER_LON,
    MATCH_CHUNK_SIZE,
    MATCH_PARALLEL_MIN_RIDES,
    MAX_GPS_GAP_M,
    MAX_ROUTING_DISTANCE_M,
    MERGE_ABSORB_COV,
    MERGE_CONNECT_M,
    MERGE_HEADING_DEG,
    MERGE_KEEP_COV,
    MERGE_MOVE_M,
    MERGE_MUTUAL_COV,
    MERGE_SAMPLE_M,
    MERGE_SNAP_M,
    MERGE_TOL_M,
    MERGE_TRANSFER_COV,
    NETWORK_TYPES,
    NYC_BBOX,
    OUTPUT_PATH_UNWEIGHTED,
    OUTPUT_PATH_WEIGHTED,
    RENDER_CACHE_FORMAT,
    RENDER_CACHE_PATH,
    RESAMPLE_SPACING_M,
    RIDE_FILES,
    RIDES_FOLDER,
    RING_MAX_GAP_M,
    RING_MAX_LEN_M,
    RING_MIN_LEN_M,
    RING_NEAR_M,
    ROUTE_CACHE_PATH,
    SAMPLE_SIZE,
    SKIP_PNG_RENDER,
    SNAP_TOLERANCE_M,
    STATE_CACHE_PATH,
)
from .export import (
    _coverage_summary,
    _export_geojson,
    _geom_len_m,
)
from .gps import (
    _is_nyc_ride,
    _load_and_resample,
    _split_at_gaps,
    haversine_m,
    resample_ride_by_distance,
)
from .graph import (
    _compute_bbox,
    _fetch_graph,
    _load_graph,
    _remove_subsumed_edges,
)
from .hmm import (
    _build_inmem_map,
    _build_matcher_context,
    _map_match_ride_hmm,
    _match_one,
)
from .matching import (
    _build_snap_tree,
    _map_match_ride,
    _match_chunk,
    _match_rides_parallel,
    _match_worker_count,
    _match_worker_init,
    _path_to_edges,
    _worker_graph_ctx,
    _worker_route_cache,
)
from .merge import (
    _audit_merge,
    _average_parallel_geometry,
    _dense_point_grid,
    _drop_redundant_rings,
    _harmonize_representatives,
    _heading_diff,
    _merge_parallel_features,
    _sample_hits,
    _sample_line,
    _snap_endpoints,
)
from .render import (
    _build_render_cache,
    _classify_hw,
    _get_render_data,
    _make_fig,
    _primary_hw_tag,
    _render,
    _save_fig,
)
from .ride_stats import (
    _backfill_ride_stats,
    _parse_ride_timestamp,
    _ride_stats_for_file,
    _riding_summary,
)
