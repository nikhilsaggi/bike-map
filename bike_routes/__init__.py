"""Bike Route Frequency Map -- incremental map-matching pipeline.

Designed for ongoing use: processes only new rides and renders from cached data.

First run:  loads all rides, fetches OSM graph, processes everything (~10-25 min).
After that: processes only new rides added to the rides/ folder (~1-2 min).

One stage per module -- import the stage you need rather than the package:

    gps         load ride CSVs, filter to NYC, resample to fixed spacing
    graph       fetch/merge the OSM networks and cache them
    hmm         leuvenmapmatching Viterbi matcher (the default)
    matching    heuristic snap+route matcher, parallel worker pool
    cache       pipeline state and config-hash invalidation
    merge       collapse parallel edge geometries into corridors
    edge_speed  direction-split per-edge speed, backfilled from timestamps
    ride_stats  per-ride distance/duration summaries
    render      PNG output and the render cache
    export      docs/rides.geojson.gz
    weather     Open-Meteo ride-weather stats
    cli         argument parsing and stage orchestration
    ingest      Garmin download and GPX -> CSV conversion

Run the pipeline with ``python -m bike_routes``; see config.py for settings and
CACHE_DIR for the generated caches (delete any to force a rebuild).

Dependencies:
    pip install .    # or: pip install osmnx networkx numpy matplotlib scipy
"""

from __future__ import annotations

__all__: list[str] = []
