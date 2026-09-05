"""Tests for the Citi Bike trip ingest and stats block (no network)."""

from __future__ import annotations

import json

import pytest

from bike_routes.citibike import _citibike_summary
from bike_routes.ingest.citibike import (
    _cache_well_formed,
    _is_ebike,
    _load_cached_stations,
    _money,
    _normalise,
    _save_station_cache,
    ingest,
)

# Two docks a hand-checkable distance apart, plus one GBFS has never heard of.
STATIONS = {
    "A St & 1 Ave": (40.70, -73.94),
    "B St & 2 Ave": (40.75, -73.98),
}


def _record(ride_id, start_ms, a, b, *, dur=600_000, bike="100-0001", items=None):
    return {
        "rideId": ride_id,
        "startTimeMs": str(start_ms),
        "endTimeMs": str(start_ms + dur),
        "duration": dur,
        "rideableName": bike,
        "startAddress": a,
        "endAddress": b,
        "price": {"formatted": "$0.00"},
        "lineItems": items
        if items is not None
        else [{"title": "Free unlock", "amount": {"formatted": "$0.00"}}],
    }


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point both cache paths at tmp_path and stub the GBFS fetch."""
    trips = tmp_path / "citibike_trips.json"
    stations = tmp_path / "citibike_stations.json"
    monkeypatch.setattr("bike_routes.config.CITIBIKE_TRIPS_PATH", trips)
    monkeypatch.setattr("bike_routes.config.CITIBIKE_STATIONS_PATH", stations)
    monkeypatch.setattr("bike_routes.ingest.citibike._fetch_stations", lambda: STATIONS)
    return trips, stations


def _write_export(tmp_path, records):
    p = tmp_path / "export.json"
    p.write_text(json.dumps(records))
    return p


def test_money_parses_lyft_strings():
    assert _money("$1.62") == pytest.approx(1.62)
    assert _money("$0.00") == 0.0
    # Credits come back signed, and dropping the sign would turn a refund into
    # a charge and double-count it in the gross.
    assert _money("-$1.28") == pytest.approx(-1.28)
    assert _money("$1,024.00") == pytest.approx(1024.0)
    assert _money(None) == 0.0
    assert _money("free") == 0.0


def test_is_ebike_reads_line_items():
    assert _is_ebike({"lineItems": [{"title": "Ebike ride with low assist"}]})
    assert _is_ebike({"lineItems": [{"title": "E-bike ride ($0.27 per min for 6 min)"}]})
    assert not _is_ebike({"lineItems": [{"title": "Free unlock"}]})
    assert not _is_ebike({})


def test_normalise_dedupes_by_ride_id():
    """The real export repeats whole records; rideId is the identity."""
    records = [
        _record("r2", 2_000, "A St & 1 Ave", "B St & 2 Ave"),
        _record("r1", 1_000, "B St & 2 Ave", "A St & 1 Ave"),
        _record("r2", 2_000, "A St & 1 Ave", "B St & 2 Ave"),
    ]
    trips, duplicates = _normalise(records)
    assert duplicates == 1
    assert [t["t"] for t in trips] == [1_000, 2_000]  # sorted by start time


def test_normalise_splits_gross_from_credits():
    records = [
        _record(
            "r1",
            1_000,
            "A St & 1 Ave",
            "B St & 2 Ave",
            items=[
                {"title": "Ebike ride ($0.27 per min for 6 min)", "amount": {"formatted": "$1.62"}},
                {"title": "NYC Sales Tax", "amount": {"formatted": "$0.14"}},
                {"title": "Credit applied", "amount": {"formatted": "-$1.28"}},
            ],
        )
    ]
    trips, _ = _normalise(records)
    assert trips[0]["gross"] == pytest.approx(1.76)
    assert trips[0]["credit"] == pytest.approx(1.28)
    assert trips[0]["ebike"] is True


def test_ingest_keeps_a_trip_whose_dock_is_unlisted(paths, tmp_path):
    """A renamed dock loses its coordinates, not the whole trip.

    Duration, cost and date are unaffected by not knowing where one end was,
    so dropping the record would understate the totals to fix the geography.
    """
    trips_path, _ = paths
    records = [
        _record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave"),
        _record("r2", 2_000, "A St & 1 Ave", "Gone St & Nowhere Ave"),
    ]
    payload = ingest(_write_export(tmp_path, records))

    assert len(payload["trips"]) == 2
    assert payload["unmatched"] == ["Gone St & Nowhere Ave"]
    assert set(payload["stations"]) == set(STATIONS)
    assert trips_path.exists()

    summary = _citibike_summary({})
    assert summary["trips"] == 2
    assert summary["unmatched"] == 1
    # r2's start is still a resolvable dock, so it counts; only its end is lost.
    a = next(s for s in summary["stations"] if s["name"] == "A St & 1 Ave")
    assert (a["out"], a["in"]) == (2, 0)
    assert len(summary["pairs"]) == 1  # only r1 has both ends


@pytest.mark.usefixtures("paths")
def test_summary_is_none_without_a_cache():
    """Every checkout but the owner's hits this path."""
    assert _citibike_summary({}) is None


def test_summary_rejects_a_future_format(paths):
    trips_path, _ = paths
    trips_path.write_text(json.dumps({"format": 99, "trips": [{"t": 1}], "stations": {}}))
    assert _citibike_summary({}) is None


@pytest.mark.usefixtures("paths")
def test_summary_counts_flow_pairs_and_oddities(tmp_path):
    a, b = "A St & 1 Ave", "B St & 2 Ave"
    day = 1_700_000_000_000  # a Tuesday afternoon in NYC
    hour = 3_600_000
    records = [
        _record("r1", day, a, b, bike="100-0001"),
        _record("r2", day + hour, a, b, bike="100-0002"),
        _record("r3", day + 2 * hour, a, b, bike="100-0003"),
        _record("r4", day + 3 * hour, b, a, bike="100-0004"),
        # Same dock, one minute: an unlock re-docked, not a ride.
        _record("r5", day + 4 * hour, a, a, dur=60_000, bike="100-0005"),
        # Bike 0001 again, so one of five bikes was ridden twice.
        _record("r6", day + 5 * hour, a, b, bike="100-0001"),
    ]
    ingest(_write_export(tmp_path, records))
    s = _citibike_summary({})

    assert s["trips"] == 6
    assert s["aborted"] == 1
    assert s["bikes"] == 5
    assert s["repeat_bikes"] == 1
    # 4 A->B and 1 B->A. The same-dock trip is not a pair at all.
    assert s["pairs"] == [[0, 1, 4], [1, 0, 1]]
    st = {x["name"]: x for x in s["stations"]}
    # A: four departures to B plus the aborted unlock that also started there,
    # against r4's arrival and the aborted unlock's own return. B is the mirror.
    assert (st[a]["out"], st[a]["in"]) == (5, 2)
    assert (st[b]["out"], st[b]["in"]) == (1, 4)
    # Stations are busiest-first so the page can slice off the top unsorted.
    assert s["stations"][0]["name"] == a
    assert s["once_only"] == 0


@pytest.mark.usefixtures("paths")
def test_summary_same_day_matches_ride_filenames(tmp_path):
    # 2023-11-14 12:13 EST and 2023-11-15 12:13 EST.
    day1, day2 = 1_699_982_000_000, 1_700_068_400_000
    ingest(
        _write_export(
            tmp_path,
            [
                _record("r1", day1, "A St & 1 Ave", "B St & 2 Ave"),
                _record("r2", day2, "A St & 1 Ave", "B St & 2 Ave"),
            ],
        )
    )
    ride_stats = {"2023-11-14_08-00-00_-0500.csv": {"dist_m": 5000.0}}
    s = _citibike_summary(ride_stats)
    assert s["days"] == 2
    assert s["same_day"] == 1
    assert s["from"] == "2023-11-14"
    assert s["to"] == "2023-11-15"


def test_station_cache_round_trip_and_shape_guard(paths):
    _, stations_path = paths
    _save_station_cache(STATIONS)
    assert _load_cached_stations() == STATIONS

    assert _cache_well_formed({"A": {"lat": 1.0, "lon": 2.0}})
    assert not _cache_well_formed({"A": {"lat": 1.0}})  # no lon
    assert not _cache_well_formed({"A": "somewhere"})
    assert not _cache_well_formed({})
    assert not _cache_well_formed([])

    stations_path.write_text('{"A": {"lat": 1.0}}')
    assert _load_cached_stations() is None


@pytest.mark.usefixtures("paths")
def test_ingest_falls_back_to_the_station_cache(tmp_path, monkeypatch):
    """A GBFS outage must not lose the docks resolved on the last run."""
    _save_station_cache(STATIONS)

    def boom():
        msg = "gbfs down"
        raise OSError(msg)

    monkeypatch.setattr("bike_routes.ingest.citibike._fetch_stations", boom)
    payload = ingest(
        _write_export(tmp_path, [_record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave")])
    )
    assert set(payload["stations"]) == set(STATIONS)
    assert payload["unmatched"] == []
