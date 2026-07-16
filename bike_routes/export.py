"""GeoJSON export and street-coverage stats."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from typing import Any

from . import config
from .merge import _audit_merge, _geom_len_m, _merge_parallel_features
from .ride_stats import _riding_summary
from .weather import _weather_summary


def _coverage_summary(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_hw: dict[tuple[int, int], str],
    state: dict[str, Any],
) -> dict[str, Any] | None:
    """Fraction of the mapped rideable street network that has been ridden.

    The denominator is every graph edge whose highway tag is plausibly
    rideable (config.COVERAGE_EXCLUDE filters footways, steps, motorways, service
    ways, ...); the numerator is the ridden subset.  new_km_by_year
    attributes each ridden edge to the year of its first traversal.
    """
    if not edge_hw:
        return None
    edge_counts = state["edge_counts"]
    edge_rides: dict[tuple[int, int], list[str]] = state.get("edge_rides", {})
    network_m = 0.0
    ridden_m = 0.0
    new_by_year: dict[str, float] = {}
    for key, coords in edge_geom.items():
        if edge_hw.get(key, "") in config.COVERAGE_EXCLUDE:
            continue
        length = _geom_len_m(coords)
        network_m += length
        if key in edge_counts:
            ridden_m += length
            rides = edge_rides.get(key)
            if rides:
                year = min(rides)[:4]
                new_by_year[year] = new_by_year.get(year, 0.0) + length
    if network_m == 0:
        return None
    return {
        "pct": round(100 * ridden_m / network_m, 1),
        "ridden_km": round(ridden_m / 1000, 1),
        "network_km": round(network_m / 1000),
        "new_km_by_year": {y: round(v / 1000, 1) for y, v in sorted(new_by_year.items())},
    }
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

    # Global ride index: one entry per processed ride, chronological by
    # filename, as [date_index, "HH:MM", distance_km].  Features reference
    # rides by index (repeated filename/date strings would dominate the
    # payload), a single ride's full route is reconstructable client-side,
    # and per-ride distances power the yearly recap.
    # ride_count is dropped from features since it equals len(rides).
    all_fnames = sorted(state["processed_files"])
    ride_id = {fname: i for i, fname in enumerate(all_fnames)}
    all_dates = sorted({fname[:10] for fname in all_fnames})
    date_idx = {d: i for i, d in enumerate(all_dates)}
    ride_stats = state.get("ride_stats", {})
    rides_meta = []
    for fname in all_fnames:
        rs = ride_stats.get(fname) or {}
        dist = round(rs["dist_m"] / 1000, 1) if rs.get("dist_m") else None
        rides_meta.append(
            [
                date_idx[fname[:10]],
                f"{fname[11:13]}:{fname[14:16]}" if len(fname) >= 16 else "",
                dist,
            ]
        )
    for f in features:
        props = f["properties"]
        props["rides"] = [ride_id[r] for r in props["rides"] if r in ride_id]
        del props["ride_count"]

    total_km = (
        sum(_geom_len_m(f["geometry"]["coordinates"]) for f in features) / 1000
    )

    rides_per_year: dict[str, int] = {}
    for fname in all_fnames:
        rides_per_year[fname[:4]] = rides_per_year.get(fname[:4], 0) + 1

    geojson = {
        "type": "FeatureCollection",
        "properties": {
            "total_rides": len(state["processed_files"]),
            "total_edges": len(features),
            "max_count": max_count,
            "total_km": round(total_km, 1),
            "rides_per_year": rides_per_year,
            "riding": _riding_summary(state.get("ride_stats", {})),
            "coverage": _coverage_summary(edge_geom, edge_hw or {}, state),
            "weather": _weather_summary(state.get("ride_stats", {})),
            "dates": all_dates,
            "rides": rides_meta,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        },
        "features": features,
    }

    config.GEOJSON_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(geojson, separators=(",", ":")).encode()
    with gzip.open(config.GEOJSON_OUTPUT_PATH, "wb", compresslevel=9) as f:
        f.write(raw)

    raw_mb = len(raw) / 1_048_576
    gz_mb = config.GEOJSON_OUTPUT_PATH.stat().st_size / 1_048_576
    print(
        f"  Exported {len(features):,} edges to {config.GEOJSON_OUTPUT_PATH} ({raw_mb:.1f} MB -> {gz_mb:.1f} MB gzipped)"
    )
