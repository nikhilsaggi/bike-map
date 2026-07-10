"""Tests for the parallel-feature merge used in GeoJSON export."""

from __future__ import annotations

from conftest import lonlat

import bike_routes as br


def _feature(points_m, rides):
    coords = [lonlat(x, y) for x, y in points_m]
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"_rides": set(rides)},
    }


def _line(x0, x1, y, step=50.0):
    n = max(1, int(abs(x1 - x0) // step))
    return [(x0 + (x1 - x0) * i / n, y) for i in range(n + 1)]


R1 = "2024-01-05_08-00-00_-0500.csv"
R2 = "2024-03-10_18-30-00_-0400.csv"
R3 = "2025-06-01_12-00-00_-0400.csv"


def test_heading_diff():
    assert br._heading_diff(0.0, 170.0) == 10.0
    assert br._heading_diff(90.0, 90.0) == 0.0
    assert br._heading_diff(179.0, 1.0) == 2.0


def test_sample_line_spacing():
    coords = [lonlat(0.0, 0.0), lonlat(80.0, 0.0)]
    samples = br._sample_line(coords)
    assert len(samples) >= 10  # ~every 8m over 80m
    assert abs(samples[0][0] - coords[0][0] * br._M_PER_LON) < 1e-6
    # Heading of an east-west line is ~0 (mod 180)
    assert all(br._heading_diff(h, 0.0) < 1.0 for _x, _y, h in samples)


def test_parallel_lines_merge_to_one():
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(0, 300, 10.0), {R2})
    out = br._merge_parallel_features([f1, f2])
    assert len(out) == 1
    props = out[0]["properties"]
    assert props["ride_count"] == 2
    assert props["ride_dates"] == ["2024-01-05", "2024-03-10"]
    assert "_rides" not in props


def test_merge_unions_shared_rides_without_double_count():
    f1 = _feature(_line(0, 300, 0.0), {R1, R2})
    f2 = _feature(_line(0, 300, 10.0), {R1})
    out = br._merge_parallel_features([f1, f2])
    assert len(out) == 1
    assert out[0]["properties"]["ride_count"] == 2


def test_adjacent_segments_stay_separate():
    """End-to-end segments along a street have near-zero mutual coverage."""
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(300, 600, 0.0), {R2})
    out = br._merge_parallel_features([f1, f2])
    assert len(out) == 2
    counts = sorted(f["properties"]["ride_count"] for f in out)
    assert counts == [1, 1]


def test_distant_lines_stay_separate():
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(0, 300, 500.0), {R2})
    out = br._merge_parallel_features([f1, f2])
    assert len(out) == 2


def test_perpendicular_lines_stay_separate():
    """Crossing streets share a point but headings differ by 90 degrees."""
    f1 = _feature(_line(-150, 150, 0.0), {R1})
    f2 = _feature([(0.0, y) for y in range(-150, 151, 50)], {R2})
    out = br._merge_parallel_features([f1, f2])
    assert len(out) == 2


def test_merge_keeps_most_ridden_geometry():
    busy = _feature(_line(0, 300, 0.0), {R1, R2, R3})
    quiet = _feature(_line(0, 300, 10.0), {R1})
    out = br._merge_parallel_features([busy, quiet])
    assert len(out) == 1
    # Kept geometry is the busy feature's line (y=0)
    _lon0, lat0 = lonlat(0.0, 0.0)
    assert abs(out[0]["geometry"]["coordinates"][0][1] - lat0) < 1e-9
    assert out[0]["properties"]["ride_count"] == 3


def test_redundant_span_absorbed():
    """A long way covered by two already-kept halves is absorbed into them."""
    left = _feature(_line(0, 300, 0.0), {R1})
    right = _feature(_line(300, 600, 0.0), {R2})
    span = _feature(_line(0, 600, 8.0), {R3})
    out = br._merge_parallel_features([left, right, span])
    assert len(out) == 2
    for f in out:
        assert f["properties"]["ride_count"] == 2  # own ride + absorbed span ride
        assert "2025-06-01" in f["properties"]["ride_dates"]
