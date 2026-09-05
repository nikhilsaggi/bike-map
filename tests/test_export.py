"""Integration test for the GeoJSON export (merge, date indexing, stats)."""

from __future__ import annotations

import gzip
import json

from conftest import lonlat

from bike_routes import config, edge_speed, export

R1 = "2024-01-05_08-00-00_-0500.csv"
R2 = "2025-06-01_12-00-00_-0400.csv"


def test_export_geojson(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

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

    export._export_geojson(edge_geom, state)

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
    # No Citibike cache in this tree: the block is absent, not half-built.
    assert props["citibike"] is None

    features = sorted(data["features"], key=lambda f: len(f["properties"]["rides"]))
    # Feature rides are indices into the global ride list; ride_count is dropped
    assert features[0]["properties"]["rides"] == [0]
    assert features[1]["properties"]["rides"] == [0, 1]
    assert all("ride_count" not in f["properties"] for f in features)
    # Per-feature names are stripped; only the top segment's is shipped.
    assert all("_name" not in f["properties"] for f in features)


def test_export_repeats_a_ride_index_per_traversal(tmp_path, monkeypatch):
    """The page counts array entries, so a round trip must appear twice."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

    edge_geom = {(1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)]}
    state = {
        "processed_files": {R1, R2},
        "edge_counts": {(1, 2): 2},
        "edge_rides": {(1, 2): [R1, R2]},
        "edge_traversals": {(1, 2): {R1: [2, 1]}},
        "ride_stats": {},
    }

    export._export_geojson(edge_geom, state)

    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)

    # R1 crossed three times, R2 once: four passes, and the array stays sorted
    # so the page's binary-search membership test still works.
    assert data["properties"]["max_count"] == 4
    assert data["features"][0]["properties"]["rides"] == [0, 0, 0, 1]


def test_export_names_the_most_ridden_segment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

    edge_geom = {
        (1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)],
        (3, 4): [lonlat(0.0, 1000.0), lonlat(300.0, 1000.0)],
    }
    state = {
        "processed_files": {R1, R2},
        "edge_counts": {(1, 2): 2, (3, 4): 1},
        "edge_rides": {(1, 2): [R1, R2], (3, 4): [R1]},
        "ride_stats": {},
    }
    edge_name = {(1, 2): "Montrose Avenue", (3, 4): "Quiet Lane"}

    export._export_geojson(edge_geom, state, {}, edge_name)
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        props = json.load(f)["properties"]

    # (1, 2) has both rides, so it is the busiest -- not the other one.
    assert props["top_segment"]["name"] == "Montrose Avenue"
    assert props["max_count"] == 2
    # Points at the busy edge (y=0), not the quiet one 1 km north.
    _lon, lat = props["top_segment"]["at"]
    assert abs(lat - lonlat(0.0, 0.0)[1]) < 0.001


def test_export_top_segment_unnamed_when_the_edge_has_no_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

    edge_geom = {(1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)]}
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
    }

    export._export_geojson(edge_geom, state)
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        props = json.load(f)["properties"]
    assert props["top_segment"]["name"] is None


def test_export_geojson_no_edges_is_noop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    export._export_geojson({}, {"edge_counts": {}, "processed_files": set()})
    assert not (tmp_path / "docs" / "rides.geojson.gz").exists()


def test_export_ranks_corridors_and_leaves_features_alone(tmp_path, monkeypatch):
    """Speed rides in the top-level block; features stay one per edge."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    monkeypatch.setattr(config, "SPEED_SPLIT_PASSES", 2)

    long_edge = [lonlat(0.0, 0.0), lonlat(600.0, 0.0)]
    short_edge = [lonlat(0.0, 1000.0), lonlat(60.0, 1000.0)]
    edge_geom = {(1, 2): long_edge, (3, 4): short_edge}
    state = {
        "processed_files": {R1, R2},
        "edge_counts": {(1, 2): 2, (3, 4): 1},
        "edge_rides": {(1, 2): [R1, R2], (3, 4): [R1]},
        "ride_stats": {},
        "edge_speed": {
            # 600 m -> 4 chunks, 18 km/h forward against 9 km/h reverse.
            (1, 2): {
                "b": edge_speed._chord_bearing(long_edge),
                "c": [[100, 20, 20, 2, 100, 40, 40, 2] for _ in range(4)],
            },
            # Too short to be a corridor, and only measured one way.
            (3, 4): {
                "b": edge_speed._chord_bearing(short_edge),
                "c": [[60, 12, 12, 1, 0, 0, 0, 0]],
            },
        },
    }

    export._export_geojson(edge_geom, state, None, {(1, 2): "Kent Avenue"})
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)

    # One feature per edge, and no per-feature speed key.
    assert len(data["features"]) == 2
    assert all("sp" not in f["properties"] for f in data["features"])

    speed = data["properties"]["speed"]
    assert speed["split_n"] == 2
    assert len(speed["corridors"]) == 1
    c = speed["corridors"][0]
    assert c["name"] == "Kent Avenue"
    assert (c["fast"], c["slow"], c["gap"]) == (18.0, 9.0, 9.0)
    assert c["m"] == 600  # all four chunks agree, so they form one run
    assert c["n"] == 2
    assert c["dir"] == "E"  # the edge runs west to east and forward is faster


def test_export_omits_speed_without_a_qualifying_corridor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")

    geom = [lonlat(0.0, 0.0), lonlat(600.0, 0.0)]
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
        # Measured, but one direction only -- nothing to compare.
        "edge_speed": {
            (1, 2): {
                "b": edge_speed._chord_bearing(geom),
                "c": [[100, 20, 20, 5, 0, 0, 0, 0] for _ in range(4)],
            }
        },
    }
    export._export_geojson({(1, 2): geom}, state, None, {(1, 2): "Kent Avenue"})
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    assert data["properties"]["speed"] is None


def test_export_ignores_unnamed_corridors(tmp_path, monkeypatch):
    """A ranking row with no street name is unusable in the stats panel."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    monkeypatch.setattr(config, "SPEED_SPLIT_PASSES", 2)

    geom = [lonlat(0.0, 0.0), lonlat(600.0, 0.0)]
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
        "edge_speed": {
            (1, 2): {
                "b": edge_speed._chord_bearing(geom),
                "c": [[100, 20, 20, 2, 100, 40, 40, 2] for _ in range(4)],
            }
        },
    }
    export._export_geojson({(1, 2): geom}, state, None, {})
    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    assert data["properties"]["speed"] is None


def test_citibike_block_leaves_the_drawn_edges_alone(tmp_path, monkeypatch):
    """Dock trips ride along in properties and touch nothing that is measured.

    They have no GPS trace, so a route between two docks is a guess. The
    export may report them beside the map; it may never let them into the
    edges, their pass counts, or the coverage the page draws conclusions from.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    trips = tmp_path / "citibike_trips.json"
    monkeypatch.setattr(config, "CITIBIKE_TRIPS_PATH", trips)
    trips.write_text(
        json.dumps(
            {
                "format": 2,
                "trips": [
                    {
                        "t": 1_700_000_000_000,
                        "dur": 600_000,
                        "a": "A St",
                        "b": "B St",
                        "bike": "1",
                        "paid": 0.0,
                        "gross": 0.0,
                        "credit": 0.0,
                        "ebike": False,
                    }
                ],
                "docks": {"A St": [-73.9, 40.7], "B St": [-73.95, 40.75]},
            }
        )
    )

    edge_geom = {(1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)]}
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {R1: {"dist_m": 1000.0, "duration_s": 600, "start": None}},
    }
    export._export_geojson(edge_geom, state)

    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    props = data["properties"]

    assert len(props["citibike"]["trips"]) == 1
    assert len(props["citibike"]["docks"]) == 2
    # The measured side is untouched: one own-bike ride, one drawn edge, and
    # the features carry ride indices and nothing else.
    assert props["total_rides"] == 1
    assert props["total_edges"] == 1
    assert props["rides"] == [[0, "08:00", 1.0]]
    assert [set(f["properties"]) for f in data["features"]] == [{"rides"}]
