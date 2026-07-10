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
    monkeypatch.setattr(br, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

    edge_geom = {
        (1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)],
        (3, 4): [lonlat(0.0, 1000.0), lonlat(300.0, 1000.0)],
    }
    state = {
        "processed_files": {R1, R2},
        "edge_counts": {(1, 2): 2, (3, 4): 1},
        "edge_rides": {(1, 2): [R1, R2], (3, 4): [R1]},
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
    assert props["rides_per_year"] == {"2024": 1, "2025": 1}
    assert abs(props["total_km"] - 0.6) < 0.05
    assert props["updated"] >= "2026-01-01"

    features = sorted(data["features"], key=lambda f: len(f["properties"]["ride_dates"]))
    # ride_dates are indices into the global date list; ride_count is dropped
    assert features[0]["properties"]["ride_dates"] == [0]
    assert features[1]["properties"]["ride_dates"] == [0, 1]
    assert all("ride_count" not in f["properties"] for f in features)


def test_export_geojson_no_edges_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(br, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    br._export_geojson({}, {"edge_counts": {}, "processed_files": set()})
    assert not (tmp_path / "docs" / "rides.geojson.gz").exists()
