"""Tests for street-network coverage stats."""

from __future__ import annotations

from bike_routes import export, render

# ~0.009 deg latitude = ~1 km
KM_SEG = [(-73.98, 40.760), (-73.98, 40.769)]


def test_primary_hw_tag():
    assert render._primary_hw_tag("residential") == "residential"
    assert render._primary_hw_tag(["residential", "cycleway"]) == "cycleway"
    assert render._primary_hw_tag(["footway", "residential"]) == "footway"
    assert render._primary_hw_tag([]) == ""
    assert render._primary_hw_tag("") == ""


def test_coverage_summary():
    edge_geom = {
        (1, 2): KM_SEG,  # ridden residential
        (3, 4): KM_SEG,  # unridden residential
        (5, 6): KM_SEG,  # ridden footway: excluded from both sides
        (7, 8): KM_SEG,  # unridden motorway: excluded
    }
    edge_hw = {
        (1, 2): "residential",
        (3, 4): "residential",
        (5, 6): "footway",
        (7, 8): "motorway",
    }
    state = {
        "edge_counts": {(1, 2): 3, (5, 6): 1},
        "edge_rides": {
            (1, 2): ["2023-05-01_08-00-00_-0400.csv", "2021-06-01_08-00-00_-0400.csv"],
            (5, 6): ["2024-01-01_08-00-00_-0500.csv"],
        },
    }
    cov = export._coverage_summary(edge_geom, edge_hw, state)
    assert cov["pct"] == 50.0  # 1 of 2 rideable km
    assert 0.9 < cov["ridden_km"] < 1.1
    assert cov["network_km"] == 2
    # First traversal of (1,2) was 2021
    assert list(cov["new_km_by_year"]) == ["2021"]
    assert 0.9 < cov["new_km_by_year"]["2021"] < 1.1
    # What the denominator dropped, largest first, so the page can name it
    # instead of asserting the contents of COVERAGE_EXCLUDE from memory.
    assert list(cov["excluded_km"]) == ["footway", "motorway"]
    assert all(0.9 < v < 1.1 for v in cov["excluded_km"].values())


def test_coverage_excluded_km_is_ordered_longest_first():
    edge_geom = {
        (1, 2): KM_SEG,
        (3, 4): [(-73.98, 40.760), (-73.98, 40.7627)],  # ~0.3 km footway
        (5, 6): KM_SEG,
        (7, 8): KM_SEG,
    }
    edge_hw = {(1, 2): "residential", (3, 4): "footway", (5, 6): "service", (7, 8): "service"}
    cov = export._coverage_summary(edge_geom, edge_hw, {"edge_counts": {}, "edge_rides": {}})
    # 2 km of service outranks 0.3 km of footway; the page reads the order.
    assert list(cov["excluded_km"]) == ["service", "footway"]
    assert cov["excluded_km"]["service"] > cov["excluded_km"]["footway"]


def test_coverage_ignores_edges_outside_the_city_box():
    # The graph follows a ride up the Hudson; those roads are drawn, but a
    # city percentage is not measured over them -- neither side of it.
    upstate = [(-73.94, 41.500), (-73.94, 41.509)]
    edge_geom = {(1, 2): KM_SEG, (3, 4): upstate, (5, 6): upstate}
    edge_hw = {(1, 2): "residential", (3, 4): "residential", (5, 6): "residential"}
    state = {"edge_counts": {(1, 2): 1, (3, 4): 1}, "edge_rides": {}}
    cov = export._coverage_summary(edge_geom, edge_hw, state)
    # 1 ridden km of 1 in-box km: the ridden upstate edge does not raise it,
    # and the unridden one does not lower it.
    assert cov["pct"] == 100.0
    assert cov["network_km"] == 1


def test_coverage_excluded_km_is_city_only_too():
    # excluded_km is the denominator's own footnote, so it counts the same
    # edges: upstate footway is not what the page's caption is naming.
    upstate = [(-73.94, 41.500), (-73.94, 41.509)]
    edge_geom = {(1, 2): KM_SEG, (3, 4): KM_SEG, (5, 6): upstate}
    edge_hw = {(1, 2): "residential", (3, 4): "footway", (5, 6): "footway"}
    cov = export._coverage_summary(edge_geom, edge_hw, {"edge_counts": {}, "edge_rides": {}})
    assert 0.9 < cov["excluded_km"]["footway"] < 1.1  # the one in the box, not both


def test_coverage_summary_empty():
    assert export._coverage_summary({}, {}, {"edge_counts": {}}) is None
    assert export._coverage_summary({(1, 2): KM_SEG}, {}, {"edge_counts": {}}) is None
