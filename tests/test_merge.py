"""Tests for the parallel-feature merge used in GeoJSON export."""

from __future__ import annotations

from conftest import lonlat

from bike_routes import config, merge


def _feature(points_m, rides, name=None):
    coords = [lonlat(x, y) for x, y in points_m]
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"_rides": set(rides), "_name": name},
    }


def _line(x0, x1, y, step=50.0):
    n = max(1, int(abs(x1 - x0) // step))
    return [(x0 + (x1 - x0) * i / n, y) for i in range(n + 1)]


R1 = "2024-01-05_08-00-00_-0500.csv"
R2 = "2024-03-10_18-30-00_-0400.csv"
R3 = "2025-06-01_12-00-00_-0400.csv"


def test_heading_diff():
    assert merge._heading_diff(0.0, 170.0) == 10.0
    assert merge._heading_diff(90.0, 90.0) == 0.0
    assert merge._heading_diff(179.0, 1.0) == 2.0


def test_sample_line_spacing():
    coords = [lonlat(0.0, 0.0), lonlat(80.0, 0.0)]
    samples = merge._sample_line(coords)
    assert len(samples) >= 10  # ~every 8m over 80m
    assert abs(samples[0][0] - coords[0][0] * config.M_PER_LON) < 1e-6
    # Heading of an east-west line is ~0 (mod 180)
    assert all(merge._heading_diff(h, 0.0) < 1.0 for _x, _y, h in samples)


def test_parallel_lines_merge_to_one():
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(0, 300, 10.0), {R2})
    out = merge._merge_parallel_features([f1, f2])
    assert len(out) == 1
    props = out[0]["properties"]
    assert props["ride_count"] == 2
    assert props["rides"] == sorted([R1, R2])
    assert "_rides" not in props


def test_merge_keeps_the_busiest_members_name():
    """The kept geometry can be an unnamed way beside the named street."""
    named = _feature(_line(0, 300, 0.0), {R1, R2}, name="Bedford Avenue")
    unnamed = _feature(_line(0, 300, 10.0), {R3})
    out = merge._merge_parallel_features([named, unnamed])
    assert len(out) == 1
    assert out[0]["properties"]["_name"] == "Bedford Avenue"

    # Order of the inputs must not decide it -- ridership does.
    out = merge._merge_parallel_features([unnamed, named])
    assert len(out) == 1
    assert out[0]["properties"]["_name"] == "Bedford Avenue"


def test_merge_leaves_an_unnamed_cluster_unnamed():
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(0, 300, 10.0), {R2})
    out = merge._merge_parallel_features([f1, f2])
    assert len(out) == 1
    assert out[0]["properties"]["_name"] is None


def test_absorbed_span_donates_its_name():
    """A named span absorbed into unnamed receivers hands its name to them."""
    left = _feature(_line(0, 300, 0.0), {R1})
    right = _feature(_line(300, 600, 0.0), {R2})
    span = _feature(_line(0, 600, 8.0), {R3}, name="Grand Street")
    out = merge._merge_parallel_features([left, right, span])
    assert len(out) == 2
    assert all(f["properties"]["_name"] == "Grand Street" for f in out)


def test_absorbed_span_does_not_overwrite_a_named_receiver():
    left = _feature(_line(0, 300, 0.0), {R1}, name="Bedford Avenue")
    right = _feature(_line(300, 600, 0.0), {R2})
    span = _feature(_line(0, 600, 8.0), {R3}, name="Grand Street")
    out = merge._merge_parallel_features([left, right, span])
    assert len(out) == 2
    names = sorted(f["properties"]["_name"] for f in out)
    assert names == ["Bedford Avenue", "Grand Street"]


def test_merge_unions_shared_rides_without_double_count():
    f1 = _feature(_line(0, 300, 0.0), {R1, R2})
    f2 = _feature(_line(0, 300, 10.0), {R1})
    out = merge._merge_parallel_features([f1, f2])
    assert len(out) == 1
    assert out[0]["properties"]["ride_count"] == 2


def test_adjacent_segments_stay_separate():
    """End-to-end segments along a street have near-zero mutual coverage."""
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(300, 600, 0.0), {R2})
    out = merge._merge_parallel_features([f1, f2])
    assert len(out) == 2
    counts = sorted(f["properties"]["ride_count"] for f in out)
    assert counts == [1, 1]


def test_distant_lines_stay_separate():
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(0, 300, 500.0), {R2})
    out = merge._merge_parallel_features([f1, f2])
    assert len(out) == 2


def test_perpendicular_lines_stay_separate():
    """Crossing streets share a point but headings differ by 90 degrees."""
    f1 = _feature(_line(-150, 150, 0.0), {R1})
    f2 = _feature([(0.0, y) for y in range(-150, 151, 50)], {R2})
    out = merge._merge_parallel_features([f1, f2])
    assert len(out) == 2


def test_merge_draws_cluster_centerline():
    busy = _feature(_line(0, 300, 0.0), {R1, R2, R3})
    quiet = _feature(_line(0, 300, 10.0), {R1})
    out = merge._merge_parallel_features([busy, quiet])
    assert len(out) == 1
    # Drawn geometry is the unweighted centerline between the parallel
    # ways (y=5), so adjacent clusters of the same ways line up exactly.
    _lon0, lat5 = lonlat(0.0, 5.0)
    for _lon, lat in out[0]["geometry"]["coordinates"]:
        assert abs(lat - lat5) < 1e-6  # within coordinate rounding (~0.1m)
    assert out[0]["properties"]["ride_count"] == 3


def test_redundant_ring_dropped():
    # Long corridor carrying both rides, with a small closed box (matched
    # onto the parallel way and back) hanging off it.
    corridor = _feature(_line(0, 400, 0.0), {R1, R2})
    box = _feature(
        [(200.0, 0.0), (230.0, 0.0), (230.0, 15.0), (200.0, 15.0), (200.0, 0.5)],
        {R1},
    )
    out = merge._drop_redundant_rings([corridor, box])
    assert out == [corridor]


def test_ring_with_unique_ride_kept():
    corridor = _feature(_line(0, 400, 0.0), {R1})
    # Ring carries a ride the corridor doesn't -- dropping it would lose data
    box = _feature(
        [(200.0, 0.0), (230.0, 0.0), (230.0, 15.0), (200.0, 15.0), (200.0, 0.5)],
        {R2},
    )
    out = merge._drop_redundant_rings([corridor, box])
    assert len(out) == 2


def test_isolated_ring_kept():
    # A ring with no nearby feature (e.g. a loop around a park) stays
    box = _feature(
        [(0.0, 0.0), (60.0, 0.0), (60.0, 60.0), (0.0, 60.0), (0.0, 0.5)],
        {R1},
    )
    out = merge._drop_redundant_rings([box])
    assert len(out) == 1


def test_snap_moves_endpoint_for_short_gaps():
    # Feature ending 10m laterally from a corridor: endpoint moves onto it
    # (no elbow vertex added), so vertex count stays the same.
    corridor = _feature(_line(0, 400, 0.0), {R1})
    stub = _feature([(500.0, 10.0), (450.0, 10.0), (300.0, 10.0)], {R2})
    n_before = len(stub["geometry"]["coordinates"])
    merge._snap_endpoints([corridor, stub])
    coords = stub["geometry"]["coordinates"]
    assert len(coords) == n_before
    # Moved endpoint now lies on the corridor (y=0)
    end_y_m = coords[-1][1] * config.M_PER_LAT
    corridor_y_m = corridor["geometry"]["coordinates"][0][1] * config.M_PER_LAT
    assert abs(end_y_m - corridor_y_m) < 1.0


def test_snap_appends_connector_for_long_gaps():
    corridor = _feature(_line(0, 400, 0.0), {R1})
    stub = _feature([(500.0, 30.0), (450.0, 30.0), (300.0, 30.0)], {R2})
    n_before = len(stub["geometry"]["coordinates"])
    merge._snap_endpoints([corridor, stub])
    # 30m gap: connector appended, original endpoint preserved
    assert len(stub["geometry"]["coordinates"]) == n_before + 1


def test_alternating_corridor_harmonized():
    # Two consecutive blocks, each a cluster of two parallel ways (y=0 and
    # y=10). Ride counts alone would pick y=0 for block one and y=10 for
    # block two, drawing a lateral jog; the continuity pass aligns them.
    a0 = _feature(_line(0, 300, 0.0), {R1, R2})
    a1 = _feature(_line(0, 300, 10.0), {R1})
    b0 = _feature(_line(300, 600, 0.0), {R3})
    b1 = _feature(_line(300, 600, 10.0), {R2, R3})
    out = merge._merge_parallel_features([a0, a1, b0, b1])
    assert len(out) == 2
    # The kept geometries connect end-to-end: they share an endpoint
    ends = [tuple(f["geometry"]["coordinates"][i]) for f in out for i in (0, -1)]
    assert len(ends) != len(set(ends))


def test_redundant_span_absorbed():
    """A long way covered by two already-kept halves is absorbed into them."""
    left = _feature(_line(0, 300, 0.0), {R1})
    right = _feature(_line(300, 600, 0.0), {R2})
    span = _feature(_line(0, 600, 8.0), {R3})
    out = merge._merge_parallel_features([left, right, span])
    assert len(out) == 2
    for f in out:
        assert f["properties"]["ride_count"] == 2  # own ride + absorbed span ride
        assert R3 in f["properties"]["rides"]
