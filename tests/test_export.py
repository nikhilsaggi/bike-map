"""Integration test for the GeoJSON export (merge, date indexing, stats)."""

from __future__ import annotations

import gzip
import json

from conftest import lonlat

import bike_routes as br

R1 = "2024-01-05_08-00-00_-0500.csv"
R2 = "2025-06-01_12-00-00_-0400.csv"


def test_export_geojson(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(br.config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

    edge_geom = {
        (1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)],
        (3, 4): [lonlat(0.0, 1000.0), lonlat(300.0, 1000.0)],
    }
    state = {
        "processed_files": {R1, R2},
        "edge_counts": {(1, 2): 2, (3, 4): 1},
        "edge_rides": {(1, 2): [R1, R2], (3, 4): [R1]},
        "ride_stats": {R1: {"dist_m": 12345.6, "duration_s": 3600, "start": None}},
    }

    br._export_geojson(edge_geom, state)

    out_path = tmp_path / "docs" / "rides.geojson.gz"
    assert out_path.exists()
    with gzip.open(out_path) as f:
        data = json.load(f)

    props = data["properties"]
    assert props["total_rides"] == 2
    assert props["total_edges"] == 2
    assert props["max_count"] == 2
    assert props["dates"] == ["2024-01-05", "2025-06-01"]
    # Global ride index: [date_index, "HH:MM", dist_km] per ride,
    # chronological; distance is None when ride_stats are absent
    assert props["rides"] == [[0, "08:00", 12.3], [1, "12:00", None]]
    assert props["rides_per_year"] == {"2024": 1, "2025": 1}
    assert abs(props["total_km"] - 0.6) < 0.05
    assert props["updated"] >= "2026-01-01"

    features = sorted(data["features"], key=lambda f: len(f["properties"]["rides"]))
    # Feature rides are indices into the global ride list; ride_count is dropped
    assert features[0]["properties"]["rides"] == [0]
    assert features[1]["properties"]["rides"] == [0, 1]
    assert all("ride_count" not in f["properties"] for f in features)


def test_export_geojson_no_edges_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(br.config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    br._export_geojson({}, {"edge_counts": {}, "processed_files": set()})
    assert not (tmp_path / "docs" / "rides.geojson.gz").exists()
