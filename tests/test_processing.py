"""Tests for GPS pre-processing helpers: distance, resampling, splitting, filtering."""

from __future__ import annotations

import numpy as np
import shapely.geometry
from conftest import LAT0, LON0, lonlat

from bike_routes import config, gps, graph


def test_haversine_known_distance():
    # One degree of latitude is ~111.2 km
    d = gps.haversine_m(40.0, -74.0, 41.0, -74.0)
    assert abs(d - 111_195) < 200


def test_haversine_zero():
    assert gps.haversine_m(40.73, -73.99, 40.73, -73.99) == 0.0


def test_haversine_array():
    lats = np.array([40.0, 40.0])
    d = gps.haversine_m(lats, np.array([-74.0, -74.0]), lats, np.array([-74.0, -73.0]))
    assert d.shape == (2,)
    assert d[0] == 0.0
    # One degree of longitude at 40N is ~85.2 km
    assert abs(d[1] - 85_180) < 300


def test_resample_spacing():
    # 1 km straight line north, points every ~5 m
    lats = LAT0 + np.linspace(0, 1000, 201) / 110_540
    coords = np.column_stack([lats, np.full(201, LON0)])
    rs = gps.resample_ride_by_distance(coords, 20.0)
    assert len(rs) in (50, 51)  # 1000m / 20m (float rounding may add one)
    gaps = gps.haversine_m(rs[:-1, 0], rs[:-1, 1], rs[1:, 0], rs[1:, 1])
    assert np.allclose(gaps, 20.0, atol=0.5)


def test_resample_short_ride_returns_endpoints():
    lon1, lat1 = lonlat(5.0, 0.0)
    coords = np.array([[LAT0, LON0], [lat1, lon1]])
    rs = gps.resample_ride_by_distance(coords, 20.0)
    assert len(rs) == 2
    assert np.array_equal(rs, coords)


def test_resample_single_point():
    coords = np.array([[LAT0, LON0]])
    assert np.array_equal(gps.resample_ride_by_distance(coords, 20.0), coords)


def test_split_at_gaps_no_gap():
    lats = LAT0 + np.arange(10) * 20 / 110_540
    coords = np.column_stack([lats, np.full(10, LON0)])
    parts = gps._split_at_gaps(coords, 300.0)
    assert len(parts) == 1
    assert len(parts[0]) == 10


def test_split_at_gaps_splits():
    ys = [0, 20, 40, 1000, 1020, 1040]  # 960m jump between index 2 and 3
    lats = LAT0 + np.array(ys) / 110_540
    coords = np.column_stack([lats, np.full(len(ys), LON0)])
    parts = gps._split_at_gaps(coords, 300.0)
    assert [len(p) for p in parts] == [3, 3]


def test_is_nyc_ride():
    nyc = np.array([[40.73, -73.99]])
    boston = np.array([[42.36, -71.06]])
    mixed = np.vstack([boston, nyc])
    assert gps._is_nyc_ride(nyc)
    assert not gps._is_nyc_ride(boston)
    assert gps._is_nyc_ride(mixed)


def test_compute_bbox_clamps_to_nyc():
    # Points extending well beyond the NYC bbox on all sides
    pts = np.array([[39.0, -75.0], [42.0, -72.0]])
    lon_min, lat_min, lon_max, lat_max = graph._compute_bbox(pts)
    b_lat_min, b_lon_min, b_lat_max, b_lon_max = config.NYC_BBOX
    buf = 0.005
    assert lat_min == b_lat_min - buf
    assert lat_max == b_lat_max + buf
    assert lon_min == b_lon_min - buf
    assert lon_max == b_lon_max + buf


def test_compute_bbox_inside_points():
    pts = np.array([[40.70, -74.00], [40.75, -73.95]])
    lon_min, lat_min, lon_max, lat_max = graph._compute_bbox(pts)
    assert abs(lat_min - (40.70 - 0.005)) < 1e-9
    assert abs(lat_max - (40.75 + 0.005)) < 1e-9
    assert abs(lon_min - (-74.00 - 0.005)) < 1e-9
    assert abs(lon_max - (-73.95 + 0.005)) < 1e-9


def _northbound(lat_end):
    """A ride from midtown straight north to lat_end, one fix every ~200 m."""
    lats = np.arange(40.75, lat_end, 200 / 110_540)
    return np.column_stack([lats, np.full(len(lats), -73.95)])


def test_outside_runs_keeps_the_fix_either_side_of_the_boundary():
    coords = _northbound(41.05)  # crosses the top of the box at 41.0
    (run,) = graph._outside_runs(coords)
    # The run starts on the last in-box fix, so the corridor meets the box.
    assert run[0][0] < config.NYC_BBOX[2] <= run[1][0]
    assert run[-1][0] == coords[-1][0]


def test_outside_runs_empty_for_a_ride_that_stays_in():
    assert graph._outside_runs(_northbound(40.90)) == []


def test_fetch_region_is_a_box_when_no_ride_leaves():
    rides = [("a.csv", _northbound(40.90))]
    region = graph._fetch_region(rides)
    assert region.equals(shapely.geometry.box(*graph._compute_bbox(rides[0][1])))


def test_fetch_region_follows_a_ride_out_of_the_box():
    rides = [("a.csv", _northbound(41.05))]
    region = graph._fetch_region(rides)
    # The corridor covers the track and a few hundred metres either side of
    # it, and stops there: the rest of that latitude is not fetched.
    assert region.contains(shapely.geometry.Point(-73.95, 41.04))
    assert region.contains(shapely.geometry.Point(-73.9455, 41.04))  # ~380 m east
    assert not region.contains(shapely.geometry.Point(-73.90, 41.04))  # ~4 km east
    assert not region.contains(shapely.geometry.Point(-73.95, 41.20))  # past the end


def test_fetch_region_costs_far_less_than_the_extent_as_a_box():
    # A ride across the city, and one that leaves it for 80 km -- which is
    # the case the corridor exists for: as a box that reach would buy the
    # whole Hudson Valley.
    across = np.column_stack([np.full(40, 40.72), np.linspace(-74.10, -73.70, 40)])
    rides = [("across.csv", across), ("north.csv", _northbound(41.70))]
    region = graph._fetch_region(rides)
    as_a_box = shapely.geometry.box(*region.bounds)
    assert graph._region_km2(region) < graph._region_km2(as_a_box) / 3


def test_fetch_region_corridor_is_wide_enough_for_the_matcher():
    region = graph._fetch_region([("a.csv", _northbound(41.05))])
    # Simplification runs after the buffer and can only cut inwards, so the
    # corridor has to be checked at its full width, not its nominal one.
    east = -73.95 + (config.CORRIDOR_BUFFER_M * 0.9) / config.M_PER_LON
    assert region.contains(shapely.geometry.Point(east, 41.02))
