"""The dream-subway export block."""

from __future__ import annotations

import json

import pytest

from bike_routes.subway import _subway_summary

# Five candidate stations; the two lines between them use only four, so index
# 2 is a station the builder considered and no line serves.
NETWORK = {
    "stations": [
        {"near": "Center Station", "at": [-73.960001, 40.737502], "n": 40},
        {"near": "West End", "at": [-73.975, 40.7375], "n": 12},
        {"near": "Nowhere", "at": [-73.90, 40.70], "n": 11},
        {"near": "East End", "at": [-73.945, 40.7375], "n": 9},
        {"near": "South End", "at": [-73.96, 40.7325], "n": 6},
    ],
    "lines": [[1, 0, 3], [0, 4]],
    "meta": [
        {
            "id": "1",
            "name": "Cross Line",
            "colour": "#d6262b",
            "stops": [1, 0, 3],
            "km": 2.5,
            "from": "West End",
            "to": "East End",
        },
        {
            "id": "2",
            "name": "South Line",
            "colour": "#0a7bc2",
            "stops": [0, 4],
            "km": 0.6,
            "from": "Center Station",
            "to": "South End",
        },
    ],
    "score": {"rides": 10, "direct": 6.0, "one_change": 2.0, "stranded": 1.0, "reachable": 9},
}


@pytest.fixture
def network(tmp_path):
    p = tmp_path / "od_network.json"
    p.write_text(json.dumps(NETWORK), encoding="utf-8")
    return p


def test_missing_network_is_none(tmp_path):
    assert _subway_summary(tmp_path / "absent.json") is None


def test_empty_network_is_none(tmp_path):
    p = tmp_path / "od_network.json"
    p.write_text(json.dumps({"stations": [], "meta": []}), encoding="utf-8")
    assert _subway_summary(p) is None


def test_only_stations_a_line_serves_are_shipped(network):
    block = _subway_summary(network)
    names = [s["name"] for s in block["stations"]]
    # "Nowhere" is heavier than two stations that are kept, so it is dropped
    # for being on no line rather than for being small.
    assert names == ["Center Station", "West End", "East End", "South End"]


def test_stops_are_renumbered_to_the_shipped_stations(network):
    block = _subway_summary(network)
    names = [s["name"] for s in block["stations"]]
    routes = {line["id"]: [names[i] for i in line["stops"]] for line in block["lines"]}
    assert routes == {
        "1": ["West End", "Center Station", "East End"],
        "2": ["Center Station", "South End"],
    }


def test_a_station_lists_every_line_that_serves_it(network):
    block = _subway_summary(network)
    by_name = {s["name"]: s for s in block["stations"]}
    assert by_name["Center Station"]["lines"] == ["1", "2"]
    assert by_name["Center Station"]["xf"] is True
    assert by_name["South End"]["lines"] == ["2"]
    assert by_name["South End"]["xf"] is False


def test_coordinates_are_rounded_but_kept_real(network):
    block = _subway_summary(network)
    assert block["stations"][0]["at"] == [-73.96, 40.7375]


def test_score_is_carried_through_as_whole_rides(network):
    block = _subway_summary(network)
    assert block["rides"] == 10
    assert block["direct"] == 6
    assert block["one_change"] == 2
    assert block["stranded"] == 1
    assert block["reachable"] == 9
    assert all(isinstance(block[k], int) for k in ("direct", "one_change", "stranded"))


def test_line_identity_comes_from_the_network_file(network):
    block = _subway_summary(network)
    assert [(line["id"], line["name"], line["colour"]) for line in block["lines"]] == [
        ("1", "Cross Line", "#d6262b"),
        ("2", "South Line", "#0a7bc2"),
    ]
