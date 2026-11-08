"""Tests for the bike-lane block (no network, synthetic lanes and streets)."""

from __future__ import annotations

import gzip
import json
import os
import time

import pytest
from conftest import lonlat

from bike_routes import bikelanes, config, export

R1 = "2023-04-01_08-00-00_-0400.csv"
R2 = "2024-06-01_08-00-00_-0400.csv"
R3 = "2025-05-01_08-00-00_-0400.csv"
DATES = ["2023-04-01", "2024-06-01", "2025-05-01"]


def _lane(
    coords: list[tuple[float, float]],
    *,
    bikeid: float,
    inst: str,
    ret: str | None = None,
    prev: str | None = None,
    cls: str = "I",
    ft: str | None = "Protected",
    tf: str | None = "Protected",
    street: str = "KENT AVE",
) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "MultiLineString", "coordinates": [[list(c) for c in coords]]},
        "properties": {
            "bikeid": str(bikeid),
            "prevbikeid": prev,
            "facilitycl": cls,
            "ft_facilit": ft,
            "tf_facilit": tf,
            "instdate": f"{inst}T00:00:00.000",
            "ret_date": f"{ret}T00:00:00.000" if ret else None,
            "street": street,
        },
    }


def _write(tmp_path, features: list[dict]):
    path = tmp_path / "lanes.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


# A 300 m street running east, ridden on R2 and R3 but not R1.
STREET = [lonlat(0.0, 0.0), lonlat(300.0, 0.0)]
EDGE_GEOM = {(1, 2): STREET}
STATE = {"edge_rides": {(1, 2): [R2, R3]}, "processed_files": {R1, R2, R3}}


def _summary(tmp_path, features, state=STATE, edge_geom=EDGE_GEOM):
    return bikelanes._bike_lane_summary(edge_geom, state, DATES, _write(tmp_path, features))


def test_no_cache_is_no_block(tmp_path):
    assert bikelanes._bike_lane_summary(EDGE_GEOM, STATE, DATES, tmp_path / "absent") is None


def test_a_lane_along_a_ridden_street_is_ridden_from_its_first_ride(tmp_path):
    out = _summary(tmp_path, [_lane(STREET, bikeid=1, inst="2020-01-01")])
    assert len(out["lines"]) == 1
    vi, first, coords = out["lines"][0]
    assert first == 1  # R2, the first ride on the street
    assert out["versions"][vi][:6] == ["I", 0, 0, "2020-01-01", None, 0]
    assert out["streets"] == ["Kent Ave"]
    assert out["facilities"] == ["Protected"]
    assert len(coords) == 2


def test_a_lane_drawn_the_other_way_round_still_matches(tmp_path):
    out = _summary(tmp_path, [_lane(STREET[::-1], bikeid=1, inst="2020-01-01")])
    assert out["lines"][0][1] == 1


def test_a_lane_beside_the_street_matches_within_the_tolerance(tmp_path):
    # An OSM cycleway drawn 8 m off DOT's centreline.
    beside = [lonlat(0.0, 8.0), lonlat(300.0, 8.0)]
    far = [lonlat(0.0, 40.0), lonlat(300.0, 40.0)]
    out = _summary(
        tmp_path,
        [_lane(beside, bikeid=1, inst="2020-01-01"), _lane(far, bikeid=2, inst="2020-01-01")],
    )
    firsts = sorted(first for _vi, first, _c in out["lines"])
    assert firsts == [-1, 1]


def test_a_cross_street_does_not_ride_the_lane_it_meets(tmp_path):
    # A north-south lane crossing the ridden street at its midpoint: only the
    # few metres at the crossing are within reach, and those are at right
    # angles to the street.
    cross = [lonlat(150.0, -150.0), lonlat(150.0, 150.0)]
    out = _summary(tmp_path, [_lane(cross, bikeid=1, inst="2020-01-01")])
    assert out["lines"][0][1] == -1


def test_only_rides_while_the_lane_existed_count(tmp_path):
    # Built after R2, so the street's first ride is not the lane's.
    out = _summary(tmp_path, [_lane(STREET, bikeid=1, inst="2025-01-01")])
    assert out["lines"][0][1] == 2  # R3
    # Retired before R2: never ridden while it was there.
    out = _summary(tmp_path, [_lane(STREET, bikeid=1, inst="2020-01-01", ret="2024-01-01")])
    assert out["lines"][0][1] == -1


def test_a_lane_half_ridden_counts_and_less_does_not(tmp_path):
    # The ridden street covers 300 m of a 500 m lane (60%) and 300 m of a
    # 900 m one (33%).
    half = [lonlat(0.0, 0.0), lonlat(500.0, 0.0)]
    third = [lonlat(0.0, 0.0), lonlat(900.0, 0.0)]
    assert _summary(tmp_path, [_lane(half, bikeid=1, inst="2020-01-01")])["lines"][0][1] == 1
    assert _summary(tmp_path, [_lane(third, bikeid=1, inst="2020-01-01")])["lines"][0][1] == -1


def test_history_is_kept_but_only_the_riding_period_is_drawn(tmp_path):
    # Signed route 2001-2010, painted 2010-2024, protected since: the oldest
    # ended before the first ride, so it has no line but stays in the chain.
    out = _summary(
        tmp_path,
        [
            _lane(
                STREET,
                bikeid=1,
                inst="2001-05-01",
                ret="2010-06-01",
                cls="III",
                ft="Signed Route",
                tf="Signed Route",
            ),
            _lane(
                STREET,
                bikeid=2.0,
                inst="2010-06-01",
                ret="2024-01-01",
                prev="1",
                cls="II",
                ft="Conventional",
                tf=None,
            ),
            _lane(STREET, bikeid=3, inst="2024-01-01", prev="2.0"),
        ],
    )
    drawn = {out["versions"][vi][3]: first for vi, first, _c in out["lines"]}
    assert drawn == {"2010-06-01": -1, "2024-01-01": 1}
    by_inst = {v[3]: (i, v) for i, v in enumerate(out["versions"])}
    protected, painted, signed = by_inst["2024-01-01"], by_inst["2010-06-01"], by_inst["2001-05-01"]
    assert protected[1][6] == painted[0]
    assert painted[1][6] == signed[0]
    assert signed[1][6] == -1
    # A one-way lane leaves the other direction blank, and the page says so.
    assert out["facilities"][painted[1][2]] == ""


def test_a_lane_built_after_the_last_ride_is_left_out(tmp_path):
    out = _summary(
        tmp_path,
        [_lane(STREET, bikeid=1, inst="2020-01-01"), _lane(STREET, bikeid=2, inst="2026-01-01")],
    )
    assert [out["versions"][vi][3] for vi, _f, _c in out["lines"]] == ["2020-01-01"]


def test_segments_of_one_lane_are_joined_where_first_ridden_together(tmp_path):
    # Three consecutive segments of one lane: the first two on the ridden
    # street, the third beyond it. Two drawn lines, not three.
    segs = [
        _lane([lonlat(0.0, 0.0), lonlat(150.0, 0.0)], bikeid=7, inst="2020-01-01"),
        _lane([lonlat(150.0, 0.0), lonlat(300.0, 0.0)], bikeid=7, inst="2020-01-01"),
        _lane([lonlat(300.0, 0.0), lonlat(600.0, 0.0)], bikeid=7, inst="2020-01-01"),
    ]
    out = _summary(tmp_path, segs)
    assert len(out["versions"]) == 1
    assert sorted(first for _vi, first, _c in out["lines"]) == [-1, 1]
    joined = next(c for _vi, first, c in out["lines"] if first == 1)
    assert len(joined) == 2  # simplified to one straight 300 m line


def test_ensure_lanes_leaves_a_fresh_cache_alone(tmp_path, monkeypatch):
    path = tmp_path / "lanes.geojson"
    path.write_text("{}")

    def boom() -> dict:
        raise AssertionError

    monkeypatch.setattr(bikelanes, "_fetch", boom)
    assert bikelanes.ensure_lanes(path) is True


def test_ensure_lanes_keeps_a_stale_cache_when_the_fetch_fails(tmp_path, monkeypatch):
    path = tmp_path / "lanes.geojson"
    path.write_text("{}")
    old = time.time() - (bikelanes.MAX_AGE_DAYS + 1) * 86_400
    os.utime(path, (old, old))

    def boom() -> dict:
        raise OSError

    monkeypatch.setattr(bikelanes, "_fetch", boom)
    assert bikelanes.ensure_lanes(path) is True
    assert path.read_text() == "{}"


@pytest.mark.parametrize("n", [0, bikelanes.SOURCE_LIMIT])
def test_ensure_lanes_refuses_an_empty_or_truncated_download(tmp_path, monkeypatch, n):
    path = tmp_path / "lanes.geojson"
    monkeypatch.setattr(bikelanes, "_fetch", lambda: {"features": [{}] * n})
    assert bikelanes.ensure_lanes(path) is False
    assert not path.exists()


def test_ensure_lanes_writes_a_download(tmp_path, monkeypatch):
    path = tmp_path / "cache" / "lanes.geojson"
    payload = {
        "type": "FeatureCollection",
        "features": [_lane(STREET, bikeid=1, inst="2020-01-01")],
    }
    monkeypatch.setattr(bikelanes, "_fetch", lambda: payload)
    assert bikelanes.ensure_lanes(path) is True
    assert json.loads(path.read_text()) == payload


def test_export_ships_the_block_from_the_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "GEOJSON_OUTPUT_PATH", tmp_path / "rides.geojson.gz")
    monkeypatch.setattr(
        config,
        "BIKE_LANES_CACHE_PATH",
        _write(tmp_path, [_lane(STREET, bikeid=1, inst="2020-01-01")]),
    )
    state = {**STATE, "edge_counts": {(1, 2): 2}}
    export._export_geojson(EDGE_GEOM, state)
    with gzip.open(tmp_path / "rides.geojson.gz") as f:
        props = json.load(f)["properties"]
    assert props["bike_lanes"]["lines"][0][1] == props["dates"].index("2024-06-01")
