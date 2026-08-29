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


def test_export_emits_speed_and_splits_long_edges(tmp_path, monkeypatch):
    """A long edge becomes one feature per chunk; a short one stays whole."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(br.config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    monkeypatch.setattr(br.config, "SPEED_MIN_PASSES", 1)

    long_edge = [lonlat(0.0, 0.0), lonlat(600.0, 0.0)]
    short_edge = [lonlat(0.0, 1000.0), lonlat(60.0, 1000.0)]
    edge_geom = {(1, 2): long_edge, (3, 4): short_edge}
    state = {
        "processed_files": {R1, R2},
        "edge_counts": {(1, 2): 2, (3, 4): 1},
        "edge_rides": {(1, 2): [R1, R2], (3, 4): [R1]},
        "ride_stats": {},
        "edge_speed": {
            # 600 m -> 4 chunks; give each a distinct forward speed.
            (1, 2): {
                "b": br._chord_bearing(long_edge),
                "c": [[100, 20, 20, 2, 100, 40, 40, 2] for _ in range(4)],
            },
            # 60 m -> 1 chunk, forward only.
            (3, 4): {"b": br._chord_bearing(short_edge), "c": [[60, 12, 12, 1, 0, 0, 0, 0]]},
        },
    }

    br._export_geojson(edge_geom, state)
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)

    # 4 chunk features from the long edge + 1 from the short one.
    assert len(data["features"]) == 5
    assert data["properties"]["speed"]["split_n"] == br.config.SPEED_SPLIT_PASSES

    by_len = sorted(data["features"], key=lambda f: len(f["properties"]["rides"]))
    short = by_len[0]
    # 60 m in 12 s = 18 km/h -> 180 tenths; reverse unmeasured -> 0.
    assert short["properties"]["sp"] == [180, 1, 0, 0]

    chunks = [f for f in data["features"] if len(f["properties"]["rides"]) == 2]
    assert len(chunks) == 4
    # 100 m in 20 s = 18 km/h forward, 100 m in 40 s = 9 km/h reverse.
    for f in chunks:
        assert f["properties"]["sp"] == [180, 2, 90, 2]
    # The pieces are contiguous and still span the original edge.
    ends = sorted(f["geometry"]["coordinates"] for f in chunks)
    assert ends[0][0] == [round(c, 6) for c in long_edge[0]]
    assert ends[-1][-1] == [round(c, 6) for c in long_edge[-1]]


def test_export_omits_speed_when_below_threshold(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(br.config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    monkeypatch.setattr(br.config, "SPEED_MIN_PASSES", 5)

    geom = [lonlat(0.0, 0.0), lonlat(60.0, 0.0)]
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
        "edge_speed": {(1, 2): {"b": br._chord_bearing(geom), "c": [[60, 12, 12, 1, 0, 0, 0, 0]]}},
    }
    br._export_geojson({(1, 2): geom}, state)
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    assert "sp" not in data["features"][0]["properties"]


def test_export_flips_speed_for_a_rebuilt_reversed_geometry(tmp_path, monkeypatch):
    """A render-cache rebuild can reverse an edge's vertex order."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(br.config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    monkeypatch.setattr(br.config, "SPEED_MIN_PASSES", 1)

    geom = [lonlat(0.0, 0.0), lonlat(60.0, 0.0)]
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
        # Recorded against the OPPOSITE orientation to the geometry below.
        "edge_speed": {
            (1, 2): {
                "b": br._chord_bearing(list(reversed(geom))),
                "c": [[60, 12, 12, 1, 0, 0, 0, 0]],
            }
        },
    }
    br._export_geojson({(1, 2): geom}, state)
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    # The measured direction must come back as REVERSE relative to the export.
    assert data["features"][0]["properties"]["sp"] == [0, 0, 180, 1]
