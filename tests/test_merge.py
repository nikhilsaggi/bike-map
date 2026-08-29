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
    assert abs(samples[0][0] - coords[0][0] * br.M_PER_LON) < 1e-6
    # Heading of an east-west line is ~0 (mod 180)
    assert all(br._heading_diff(h, 0.0) < 1.0 for _x, _y, h in samples)


def test_parallel_lines_merge_to_one():
    f1 = _feature(_line(0, 300, 0.0), {R1})
    f2 = _feature(_line(0, 300, 10.0), {R2})
    out = br._merge_parallel_features([f1, f2])
    assert len(out) == 1
    props = out[0]["properties"]
    assert props["ride_count"] == 2
    assert props["rides"] == sorted([R1, R2])
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


def test_merge_draws_cluster_centerline():
    busy = _feature(_line(0, 300, 0.0), {R1, R2, R3})
    quiet = _feature(_line(0, 300, 10.0), {R1})
    out = br._merge_parallel_features([busy, quiet])
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
    out = br._drop_redundant_rings([corridor, box])
    assert out == [corridor]


def test_ring_with_unique_ride_kept():
    corridor = _feature(_line(0, 400, 0.0), {R1})
    # Ring carries a ride the corridor doesn't -- dropping it would lose data
    box = _feature(
        [(200.0, 0.0), (230.0, 0.0), (230.0, 15.0), (200.0, 15.0), (200.0, 0.5)],
        {R2},
    )
    out = br._drop_redundant_rings([corridor, box])
    assert len(out) == 2


def test_isolated_ring_kept():
    # A ring with no nearby feature (e.g. a loop around a park) stays
    box = _feature(
        [(0.0, 0.0), (60.0, 0.0), (60.0, 60.0), (0.0, 60.0), (0.0, 0.5)],
        {R1},
    )
    out = br._drop_redundant_rings([box])
    assert len(out) == 1


def test_snap_moves_endpoint_for_short_gaps():
    # Feature ending 10m laterally from a corridor: endpoint moves onto it
    # (no elbow vertex added), so vertex count stays the same.
    corridor = _feature(_line(0, 400, 0.0), {R1})
    stub = _feature([(500.0, 10.0), (450.0, 10.0), (300.0, 10.0)], {R2})
    n_before = len(stub["geometry"]["coordinates"])
    br._snap_endpoints([corridor, stub])
    coords = stub["geometry"]["coordinates"]
    assert len(coords) == n_before
    # Moved endpoint now lies on the corridor (y=0)
    end_y_m = coords[-1][1] * br.M_PER_LAT
    corridor_y_m = corridor["geometry"]["coordinates"][0][1] * br.M_PER_LAT
    assert abs(end_y_m - corridor_y_m) < 1.0


def test_snap_appends_connector_for_long_gaps():
    corridor = _feature(_line(0, 400, 0.0), {R1})
    stub = _feature([(500.0, 30.0), (450.0, 30.0), (300.0, 30.0)], {R2})
    n_before = len(stub["geometry"]["coordinates"])
    br._snap_endpoints([corridor, stub])
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
    out = br._merge_parallel_features([a0, a1, b0, b1])
    assert len(out) == 2
    # The kept geometries connect end-to-end: they share an endpoint
    ends = [tuple(f["geometry"]["coordinates"][i]) for f in out for i in (0, -1)]
    assert len(ends) != len(set(ends))


def test_redundant_span_absorbed():
    """A long way covered by two already-kept halves is absorbed into them."""
    left = _feature(_line(0, 300, 0.0), {R1})
    right = _feature(_line(300, 600, 0.0), {R2})
    span = _feature(_line(0, 600, 8.0), {R3})
    out = br._merge_parallel_features([left, right, span])
    assert len(out) == 2
    for f in out:
        assert f["properties"]["ride_count"] == 2  # own ride + absorbed span ride
        assert R3 in f["properties"]["rides"]


# -- Direction-split speed through the merge ---------------------------------
#
# Three sites in the merge can flip a feature's direction relative to its
# geometry, and a missed flip produces a plausible-looking but reversed
# corridor rather than an error.  These cover all three.


def _speeded(coords, fwd, rev):
    """A pre-merge feature carrying one chunk record."""
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"_rides": set(), "_speed": [fwd, 1, 1, 1, rev, 1, 1, 1]},
    }


def test_relative_orientation_same_and_opposite():
    a = [lonlat(0, 0), lonlat(200, 0)]
    assert br._relative_orientation(a, a) == 1
    assert br._relative_orientation(a, list(reversed(a))) == -1


def test_relative_orientation_survives_a_hairpin():
    """A chord dot product gets this wrong; along-parameter correlation does not."""
    hairpin = [lonlat(0, 0), lonlat(200, 0), lonlat(200, 12), lonlat(0, 12)]
    assert br._relative_orientation(hairpin, hairpin) == 1
    assert br._relative_orientation(hairpin, list(reversed(hairpin))) == -1


def test_speed_add_same_direction_sums_buckets():
    coords = [lonlat(0, 0), lonlat(200, 0)]
    dst = _speeded(coords, 10, 4)
    src = _speeded(coords, 6, 2)
    br._speed_add(dst["properties"], coords, src["properties"], coords)
    assert dst["properties"]["_speed"][br._FWD] == 16
    assert dst["properties"]["_speed"][br._REV] == 6


def test_speed_add_flips_buckets_for_an_antiparallel_source():
    """The divided-carriageway case: heading alignment is mod 180."""
    coords = [lonlat(0, 0), lonlat(200, 0)]
    rev_coords = list(reversed(coords))
    dst = _speeded(coords, 10, 4)
    src = _speeded(rev_coords, 6, 2)
    br._speed_add(dst["properties"], coords, src["properties"], rev_coords)
    # src's "forward" runs against dst's, so it must land in dst's reverse.
    assert dst["properties"]["_speed"][br._FWD] == 12  # 10 + src rev
    assert dst["properties"]["_speed"][br._REV] == 10  # 4 + src fwd


def test_reorient_speed_flips_when_geometry_is_replaced():
    """_harmonize_representatives swaps in an alt geometry that may be reversed."""
    coords = [lonlat(0, 0), lonlat(200, 0)]
    props = {"_speed": [10, 1, 1, 1, 4, 1, 1, 1]}
    br._reorient_speed(props, coords, list(reversed(coords)))
    assert props["_speed"][br._FWD] == 4
    assert props["_speed"][br._REV] == 10


def test_reorient_speed_leaves_same_direction_alone():
    coords = [lonlat(0, 0), lonlat(200, 0)]
    props = {"_speed": [10, 1, 1, 1, 4, 1, 1, 1]}
    br._reorient_speed(props, coords, list(coords))
    assert props["_speed"][br._FWD] == 10


def test_cluster_speed_orients_every_member_to_the_kept_geometry():
    coords = [lonlat(0, 0), lonlat(200, 0)]
    rev_coords = list(reversed(coords))
    # Member 1's geometry is reversed, so its "forward" (6) runs against
    # member 0's forward and must land in the reverse bucket.
    features = [_speeded(coords, 10, 4), _speeded(rev_coords, 6, 2)]
    rec = br._cluster_speed(features, [0, 1], 0)
    assert rec[br._FWD] == 12  # 10 + member 1's reverse (2)
    assert rec[br._REV] == 10  # 4 + member 1's forward (6)


def test_antiparallel_cluster_merges_without_inverting():
    """End to end: two carriageways 12 m apart, drawn in opposite directions."""
    # a is drawn west->east, b east->west, 12 m apart.  So a's forward and
    # b's REVERSE are both eastbound.
    a = [lonlat(x, 0) for x in range(0, 220, 20)]
    b = [lonlat(x, 12) for x in range(200, -20, -20)]
    fa = _speeded(a, 30, 6)  # 30 eastbound, 6 westbound
    fb = _speeded(b, 20, 4)  # 20 westbound, 4 eastbound
    fa["properties"]["_rides"] = {"r1"}
    fb["properties"]["_rides"] = {"r2"}
    out = br._merge_parallel_features([fa, fb])
    assert len(out) == 1, "the two carriageways should merge into one line"
    rec = out[0]["properties"]["_speed"]
    kept = out[0]["geometry"]["coordinates"]
    # Whichever geometry survived, eastbound is 30+4 and westbound is 6+20.
    # An unflipped add would give 50/10 -- confidently reversed, not an error.
    eastbound = kept[-1][0] > kept[0][0]
    east, west = (rec[br._FWD], rec[br._REV]) if eastbound else (rec[br._REV], rec[br._FWD])
    assert east == 34
    assert west == 26
