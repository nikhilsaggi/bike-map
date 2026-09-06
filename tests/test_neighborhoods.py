"""Tests for the per-neighborhood coverage block (no network, synthetic polygons)."""

from __future__ import annotations

import gzip
import json

import pytest
from conftest import lonlat

from bike_routes import config, export, neighborhoods

R1 = "2024-01-05_08-00-00_-0500.csv"
R2 = "2025-06-01_12-00-00_-0400.csv"

# Two square areas side by side, 0-500 m and 600-1100 m east of the origin,
# with a 100 m gap between them so a point in the gap is inside neither and
# the boundary tolerance is testable.
WEST = (0.0, 500.0)
EAST = (600.0, 1100.0)


def _square(x0: float, x1: float, name: str, boro: str = "Brooklyn") -> dict:
    ring = [
        lonlat(x0, -500.0),
        lonlat(x1, -500.0),
        lonlat(x1, 500.0),
        lonlat(x0, 500.0),
        lonlat(x0, -500.0),
    ]
    return {
        "properties": {"ntaname": name, "boroname": boro, "ntatype": "0"},
        "geometry": {"type": "Polygon", "coordinates": [[list(c) for c in ring]]},
    }


@pytest.fixture
def areas() -> neighborhoods.Areas:
    return neighborhoods.Areas([_square(*WEST, "Westville"), _square(*EAST, "Eastburg", "Queens")])


def test_locate_places_points_and_rejects_the_far_field(areas):
    inside_west = lonlat(250.0, 0.0)
    inside_east = lonlat(800.0, 0.0)
    # 30 m past the west square's edge: inside the tolerance, so it still
    # belongs -- this is the pier-and-bridge-deck case.
    just_outside = lonlat(530.0, 0.0)
    far = lonlat(5000.0, 0.0)
    assert areas.locate([inside_west, inside_east, just_outside, far]) == [0, 1, 0, -1]


def test_locate_takes_the_nearest_when_a_point_is_between_two(areas):
    # 20 m east of the west square, 80 m west of the east one.
    assert areas.locate([lonlat(520.0, 0.0)]) == [0]
    assert areas.locate([lonlat(580.0, 0.0)]) == [1]


def test_locate_of_nothing_is_nothing(areas):
    assert areas.locate([]) == []


def test_measure_splits_the_coverage_measurement_by_area(areas):
    edge_geom = {
        (1, 2): [lonlat(100.0, 0.0), lonlat(200.0, 0.0)],  # west, ridden
        (2, 3): [lonlat(200.0, 0.0), lonlat(400.0, 0.0)],  # west, not ridden
        (4, 5): [lonlat(700.0, 0.0), lonlat(800.0, 0.0)],  # east, ridden
        (6, 7): [lonlat(700.0, 100.0), lonlat(800.0, 100.0)],  # east, a sidewalk
    }
    edge_hw = {(1, 2): "residential", (2, 3): "residential", (4, 5): "cycleway", (6, 7): "footway"}
    state = {"edge_rides": {(1, 2): [R2, R1], (4, 5): [R1]}}

    rows = neighborhoods.measure(areas, edge_geom, edge_hw, state)
    west, east = rows
    assert west["name"] == "Westville"
    assert west["boro"] == "Bk"
    assert round(west["net_m"]) == 300
    assert round(west["ridden_m"]) == 100
    assert west["rides"] == {R1, R2}
    # The excluded sidewalk is in neither the numerator nor the denominator,
    # exactly as in the citywide figure.
    assert east["boro"] == "Qn"
    assert round(east["net_m"]) == 100
    assert round(east["ridden_m"]) == 100
    # First ridden: the earliest ride on the edge, not the last one written.
    assert list(west["first"]) == ["2024-01-05"]


def test_measure_sums_the_distance_and_time_measured_on_each_area(areas):
    """Both come from edge_speed, and count tags coverage leaves out."""
    edge_geom = {
        (1, 2): [lonlat(100.0, 0.0), lonlat(200.0, 0.0)],  # west, rideable
        (4, 5): [lonlat(700.0, 0.0), lonlat(800.0, 0.0)],  # east, rideable
        (6, 7): [lonlat(700.0, 100.0), lonlat(800.0, 100.0)],  # east, a footway
    }
    edge_hw = {(1, 2): "residential", (4, 5): "residential", (6, 7): "footway"}
    # One chunk each: [f_dist, f_time, f_moving, f_n, r_dist, r_time, r_moving, r_n].
    state = {
        "edge_rides": {},
        "edge_speed": {
            (1, 2): {"b": 90.0, "c": [[100.0, 60.0, 55.0, 1.0, 0.0, 0.0, 0.0, 0.0]]},
            (4, 5): {"b": 90.0, "c": [[100.0, 20.0, 20.0, 1.0, 100.0, 25.0, 25.0, 1.0]]},
            # A sidewalk is out of the coverage denominator, but the time on
            # it was still spent in the neighborhood.
            (6, 7): {"b": 90.0, "c": [[100.0, 30.0, 30.0, 1.0, 0.0, 0.0, 0.0, 0.0]]},
        },
    }
    west, east = neighborhoods.measure(areas, edge_geom, edge_hw, state)
    assert west["time_s"] == 60.0  # forward only
    assert east["time_s"] == 75.0  # both directions, plus the footway's 30 s
    assert west["dist_m"] == 100.0
    assert east["dist_m"] == 300.0  # both directions, plus the footway's 100 m


def test_measure_distance_is_every_pass_where_ridden_m_is_the_street_once(areas):
    """The Ridden ranking's number: 300 m of riding on 100 m of street."""
    edge_geom = {(1, 2): [lonlat(100.0, 0.0), lonlat(200.0, 0.0)]}
    state = {
        "edge_rides": {(1, 2): [R1, R2]},
        # Two passes out and one back, all on the one 100 m edge.
        "edge_speed": {
            (1, 2): {"b": 90.0, "c": [[200.0, 120.0, 110.0, 2.0, 100.0, 50.0, 45.0, 1.0]]},
        },
    }
    west = neighborhoods.measure(areas, edge_geom, {(1, 2): "residential"}, state)[0]
    assert round(west["ridden_m"]) == 100
    assert west["dist_m"] == 300.0


def test_measure_without_speed_records_reports_no_distance_or_time(areas):
    edge_geom = {(1, 2): [lonlat(100.0, 0.0), lonlat(200.0, 0.0)]}
    rows = neighborhoods.measure(areas, edge_geom, {(1, 2): "residential"}, {"edge_rides": {}})
    assert rows[0]["time_s"] == 0.0
    assert rows[0]["dist_m"] == 0.0


def test_measure_without_boundaries_is_empty():
    assert neighborhoods.measure(None, {}, {}, {}) == []


def test_summary_ships_areas_and_tags_the_features(areas):
    edge_geom = {
        (1, 2): [lonlat(100.0, 0.0), lonlat(300.0, 0.0)],  # 200 m, ridden
        (2, 3): [lonlat(300.0, 0.0), lonlat(400.0, 0.0)],  # 100 m, not ridden
        (4, 5): [lonlat(700.0, 0.0), lonlat(800.0, 0.0)],  # 100 m, ridden
    }
    edge_hw = dict.fromkeys(edge_geom, "residential")
    state = {
        "edge_rides": {(1, 2): [R1], (4, 5): [R2]},
        # Two passes over the west edge and one over the east: 500 m ridden
        # on 300 m of street, which is the pair the Ridden ranking needs.
        "edge_speed": {
            (1, 2): {"b": 90.0, "c": [[400.0, 200.0, 190.0, 2.0, 0.0, 0.0, 0.0, 0.0]]},
            (4, 5): {"b": 90.0, "c": [[100.0, 40.0, 40.0, 1.0, 0.0, 0.0, 0.0, 0.0]]},
        },
    }
    features = [
        {"geometry": {"coordinates": [lonlat(100.0, 0.0), lonlat(300.0, 0.0)]}, "properties": {}},
        {"geometry": {"coordinates": [lonlat(700.0, 0.0), lonlat(800.0, 0.0)]}, "properties": {}},
        # Outside both squares: tagged -1, counted nowhere.
        {"geometry": {"coordinates": [lonlat(9000.0, 0.0), lonlat(9100.0, 0.0)]}, "properties": {}},
    ]
    date_index = {"2024-01-05": 0, "2025-06-01": 1}

    block = neighborhoods._neighborhood_summary(
        areas, edge_geom, edge_hw, state, date_index, features
    )

    # Ordered by ridden metres, busiest first.
    assert [a["name"] for a in block["areas"]] == ["Westville", "Eastburg"]
    assert block["net_m"] == 400
    assert block["ridden_m"] == 300
    assert block["dist_m"] == 500
    # The tag is an index into the block's own array, not the boundary file's.
    assert [f["properties"]["n"] for f in features] == [0, 1, -1]
    west, east = block["areas"]
    assert (west["dist_m"], east["dist_m"]) == (400, 100)
    assert west["new"] == [[0, 200]]
    assert east["new"] == [[1, 100]]
    # polygon -> ring -> point: one piece, one ring, five points closing it.
    assert len(west["rings"]) == 1
    assert len(west["rings"][0]) == 1
    assert len(west["rings"][0][0]) >= 4


def test_summary_drops_areas_with_no_rideable_street(areas):
    edge_geom = {(1, 2): [lonlat(100.0, 0.0), lonlat(200.0, 0.0)]}
    state = {"edge_rides": {}}
    block = neighborhoods._neighborhood_summary(
        areas, edge_geom, {(1, 2): "residential"}, state, {}, []
    )
    assert [a["name"] for a in block["areas"]] == ["Westville"]
    assert block["areas"][0]["ridden_m"] == 0


def test_summary_without_boundaries_is_none():
    assert neighborhoods._neighborhood_summary(None, {}, {}, {}, {}, []) is None


def test_load_areas_without_a_cache_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NTA_CACHE_PATH", tmp_path / "nope.geojson")
    assert neighborhoods.load_areas() is None


def test_load_areas_reads_the_cache(tmp_path, monkeypatch):
    path = tmp_path / "nta.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [_square(*WEST, "One")]}))
    monkeypatch.setattr(config, "NTA_CACHE_PATH", path)
    loaded = neighborhoods.load_areas()
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded.names == ["One"]


def test_ensure_boundaries_keeps_an_existing_cache(tmp_path, monkeypatch):
    path = tmp_path / "nta.geojson"
    path.write_text("{}")
    monkeypatch.setattr(config, "NTA_CACHE_PATH", path)

    def explode():
        msg = "should not fetch when the cache is there"
        raise AssertionError(msg)

    monkeypatch.setattr(neighborhoods, "_fetch_boundaries", explode)
    assert neighborhoods.ensure_boundaries() is True


def test_ensure_boundaries_survives_a_failed_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "NTA_CACHE_PATH", tmp_path / "nta.geojson")

    def fail():
        msg = "no network"
        raise OSError(msg)

    monkeypatch.setattr(neighborhoods, "_fetch_boundaries", fail)
    assert neighborhoods.ensure_boundaries() is False


def test_export_without_boundaries_omits_the_block(tmp_path, monkeypatch):
    """The whole layer is optional: no boundary cache, no block, no tags."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    monkeypatch.setattr(config, "NTA_CACHE_PATH", tmp_path / "absent.geojson")

    edge_geom = {(1, 2): [lonlat(0.0, 0.0), lonlat(300.0, 0.0)]}
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
    }
    export._export_geojson(edge_geom, state, {(1, 2): "residential"})

    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    assert data["properties"]["neighborhoods"] is None
    assert all("n" not in f["properties"] for f in data["features"])


def test_export_ships_the_block_when_the_boundaries_are_cached(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "docs" / "rides.geojson.gz")
    path = tmp_path / "nta.geojson"
    path.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": [_square(*WEST, "Westville")]},
        )
    )
    monkeypatch.setattr(config, "NTA_CACHE_PATH", path)

    edge_geom = {(1, 2): [lonlat(100.0, 0.0), lonlat(400.0, 0.0)]}
    state = {
        "processed_files": {R1},
        "edge_counts": {(1, 2): 1},
        "edge_rides": {(1, 2): [R1]},
        "ride_stats": {},
    }
    export._export_geojson(edge_geom, state, {(1, 2): "residential"})

    with gzip.open(tmp_path / "docs" / "rides.geojson.gz") as f:
        data = json.load(f)
    block = data["properties"]["neighborhoods"]
    assert [a["name"] for a in block["areas"]] == ["Westville"]
    assert block["areas"][0]["new"] == [[0, 300]]
    # Every drawn feature knows the area it is in, by the same midpoint rule.
    assert [f["properties"]["n"] for f in data["features"]] == [0]
