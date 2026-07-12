"""Tests for street-network coverage stats."""

from __future__ import annotations

import bike_routes as br

# ~0.009 deg latitude = ~1 km
KM_SEG = [(-73.98, 40.760), (-73.98, 40.769)]


def test_primary_hw_tag():
    assert br._primary_hw_tag("residential") == "residential"
    assert br._primary_hw_tag(["residential", "cycleway"]) == "cycleway"
    assert br._primary_hw_tag(["footway", "residential"]) == "footway"
    assert br._primary_hw_tag([]) == ""
    assert br._primary_hw_tag("") == ""


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
    cov = br._coverage_summary(edge_geom, edge_hw, state)
    assert cov["pct"] == 50.0  # 1 of 2 rideable km
    assert 0.9 < cov["ridden_km"] < 1.1
    assert cov["network_km"] == 2
    # First traversal of (1,2) was 2021
    assert list(cov["new_km_by_year"]) == ["2021"]
    assert 0.9 < cov["new_km_by_year"]["2021"] < 1.1


def test_coverage_summary_empty():
    assert br._coverage_summary({}, {}, {"edge_counts": {}}) is None
    assert br._coverage_summary({(1, 2): KM_SEG}, {}, {"edge_counts": {}}) is None
