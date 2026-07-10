"""Tests for GPS pre-processing helpers: distance, resampling, splitting, filtering."""

from __future__ import annotations

import numpy as np
from conftest import LAT0, LON0, lonlat

import bike_routes as br


def test_haversine_known_distance():
    # One degree of latitude is ~111.2 km
    d = br.haversine_m(40.0, -74.0, 41.0, -74.0)
    assert abs(d - 111_195) < 200


def test_haversine_zero():
    assert br.haversine_m(40.73, -73.99, 40.73, -73.99) == 0.0


def test_haversine_array():
    lats = np.array([40.0, 40.0])
    d = br.haversine_m(lats, np.array([-74.0, -74.0]), lats, np.array([-74.0, -73.0]))
    assert d.shape == (2,)
    assert d[0] == 0.0
    # One degree of longitude at 40N is ~85.2 km
    assert abs(d[1] - 85_180) < 300


def test_resample_spacing():
    # 1 km straight line north, points every ~5 m
    lats = LAT0 + np.linspace(0, 1000, 201) / 110_540
    coords = np.column_stack([lats, np.full(201, LON0)])
    rs = br.resample_ride_by_distance(coords, 20.0)
    assert len(rs) in (50, 51)  # 1000m / 20m (float rounding may add one)
    gaps = br.haversine_m(rs[:-1, 0], rs[:-1, 1], rs[1:, 0], rs[1:, 1])
    assert np.allclose(gaps, 20.0, atol=0.5)


def test_resample_short_ride_returns_endpoints():
    lon1, lat1 = lonlat(5.0, 0.0)
    coords = np.array([[LAT0, LON0], [lat1, lon1]])
    rs = br.resample_ride_by_distance(coords, 20.0)
    assert len(rs) == 2
    assert np.array_equal(rs, coords)


def test_resample_single_point():
    coords = np.array([[LAT0, LON0]])
    assert np.array_equal(br.resample_ride_by_distance(coords, 20.0), coords)


def test_split_at_gaps_no_gap():
    lats = LAT0 + np.arange(10) * 20 / 110_540
    coords = np.column_stack([lats, np.full(10, LON0)])
    parts = br._split_at_gaps(coords, 300.0)
    assert len(parts) == 1
    assert len(parts[0]) == 10


def test_split_at_gaps_splits():
    ys = [0, 20, 40, 1000, 1020, 1040]  # 960m jump between index 2 and 3
    lats = LAT0 + np.array(ys) / 110_540
    coords = np.column_stack([lats, np.full(len(ys), LON0)])
    parts = br._split_at_gaps(coords, 300.0)
    assert [len(p) for p in parts] == [3, 3]


def test_is_nyc_ride():
    nyc = np.array([[40.73, -73.99]])
    boston = np.array([[42.36, -71.06]])
    mixed = np.vstack([boston, nyc])
    assert br._is_nyc_ride(nyc)
    assert not br._is_nyc_ride(boston)
    assert br._is_nyc_ride(mixed)


def test_compute_bbox_clamps_to_nyc():
    # Points extending well beyond the NYC bbox on all sides
    pts = np.array([[39.0, -75.0], [42.0, -72.0]])
    lon_min, lat_min, lon_max, lat_max = br._compute_bbox(pts)
    b_lat_min, b_lon_min, b_lat_max, b_lon_max = br.NYC_BBOX
    buf = 0.005
    assert lat_min == b_lat_min - buf
    assert lat_max == b_lat_max + buf
    assert lon_min == b_lon_min - buf
    assert lon_max == b_lon_max + buf


def test_compute_bbox_inside_points():
    pts = np.array([[40.70, -74.00], [40.75, -73.95]])
    lon_min, lat_min, lon_max, lat_max = br._compute_bbox(pts)
    assert abs(lat_min - (40.70 - 0.005)) < 1e-9
    assert abs(lat_max - (40.75 + 0.005)) < 1e-9
    assert abs(lon_min - (-74.00 - 0.005)) < 1e-9
    assert abs(lon_max - (-73.95 + 0.005)) < 1e-9
