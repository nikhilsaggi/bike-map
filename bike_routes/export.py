"""GeoJSON export and street-coverage stats."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from typing import Any

from . import config
from .edge_speed import (
    _FWD,
    _REV,
    _chunk_slices,
    _chunk_speed_kmh,
    _oriented_chunks,
    _speed_summary,
)
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


def _speed_payload(rec: list[float] | None) -> list[int] | None:
    """Serialize a merged speed record as [fwd_dkmh, fwd_n, rev_dkmh, rev_n].

    Speeds are tenths of km/h as integers; a direction that has not cleared
    both thresholds is 0, which is never a real speed and so doubles as
    "no data" for a byte instead of four.  Returns None when neither
    direction qualifies, so the feature omits the key entirely.

    Buckets are relative to the exported coordinate array, so the client
    derives compass direction from the geometry and no bearing is shipped.
    """
    if not rec:
        return None
    out: list[int] = []
    for base in (_FWD, _REV):
        kmh = _chunk_speed_kmh(rec, base)
        out.extend([round(10 * kmh) if kmh is not None else 0, int(rec[base + 3])])
    return out if (out[0] or out[2]) else None


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
    edge_speed: dict[tuple[int, int], list[float]] = state.get("edge_speed", {})

    features = []
    for edge_key in edge_counts:
        if edge_key not in edge_geom:
            continue
        coords = [(round(lon, 6), round(lat, 6)) for lon, lat in edge_geom[edge_key]]
        rides = set(edge_rides.get(edge_key, ()))
        chunks = _oriented_chunks(edge_speed.get(edge_key), coords)
        # A long way is exported as one feature per speed chunk: measured whole,
        # a bridge averages its climb against its descent and shows nothing.
        # The slices share boundary vertices, so the drawn line is unchanged.
        slices = _chunk_slices(coords, len(chunks)) if chunks else [coords]
        if chunks and len(slices) != len(chunks):
            chunks = None  # slicing declined to split; fall back to one feature
            slices = [coords]
        for i, piece in enumerate(slices):
            props: dict[str, Any] = {"_rides": set(rides)}
            if chunks:
                props["_speed"] = chunks[i]
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": piece},
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
        sp = _speed_payload(props.pop("_speed", None))
        if sp is not None:
            props["sp"] = sp

    total_km = sum(_geom_len_m(f["geometry"]["coordinates"]) for f in features) / 1000

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
            "speed": _speed_summary(state.get("edge_speed", {})),
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
