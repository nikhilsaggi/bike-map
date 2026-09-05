"""Tests for the Citi Bike trip ingest and stats block (no network at all)."""

from __future__ import annotations

import json

import pytest

from bike_routes.citibike import _citibike_summary
from bike_routes.ingest.citibike import _is_ebike, _money, _normalise, ingest


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
def trips_path(tmp_path, monkeypatch):
    """Point the trips cache at tmp_path."""
    path = tmp_path / "citibike_trips.json"
    monkeypatch.setattr("bike_routes.config.CITIBIKE_TRIPS_PATH", path)
    return path


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


@pytest.mark.usefixtures("trips_path")
def test_summary_is_none_without_a_cache():
    """Every checkout but the owner's hits this path."""
    assert _citibike_summary({}) is None


def test_summary_rejects_a_future_format(trips_path):
    trips_path.write_text(json.dumps({"format": 99, "trips": [{"t": 1}]}))
    assert _citibike_summary({}) is None


@pytest.mark.usefixtures("trips_path")
def test_summary_counts_docks_and_oddities(tmp_path):
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
    assert s["docks"] == 2
    assert s["once_only"] == 0
    # A: four departures to B plus the aborted unlock that also started there,
    # against r4's arrival and the aborted unlock's own return. B is the mirror.
    flow = {f["name"]: (f["out"], f["in"]) for f in s["flow"]}
    assert flow == {a: (5, 2), b: (1, 4)}


@pytest.mark.usefixtures("trips_path")
def test_flow_lists_both_ends_without_repeating_a_dock(tmp_path):
    """The top-3 and bottom-2 slices overlap on a short list.

    Listing one dock twice would read as two different docks that happen to
    share a name, so the tail drops anything already in the head.
    """
    day = 1_700_000_000_000
    # Six docks, each with a distinct net: D5 is +5 down to D0 at 0.
    records = []
    n = 0
    for i in range(6):
        for _ in range(i):
            n += 1
            records.append(_record(f"r{n}", day + n * 60_000, f"D{i}", "Sink St"))
    ingest(_write_export(tmp_path, records))
    s = _citibike_summary({})

    names = [f["name"] for f in s["flow"]]
    assert len(names) == len(set(names))
    # Three most departed-from, then the two most arrived-at. D1 is the only
    # other dock with any use, so it takes the second tail slot.
    assert names == ["D5", "D4", "D3", "D1", "Sink St"]
    assert s["flow"][-1] == {"name": "Sink St", "out": 0, "in": 15}
    assert s["once_only"] == 1  # D1, used once


@pytest.mark.usefixtures("trips_path")
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


@pytest.mark.usefixtures("trips_path")
def test_ingest_ships_no_coordinates(tmp_path):
    """Nothing draws a dock, so nothing should be carrying its position.

    The GBFS lookup that produced coordinates went out with the map layer;
    a payload that grew them back would be dead weight in every download.
    """
    payload = ingest(
        _write_export(tmp_path, [_record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave")])
    )
    assert set(payload) == {"format", "source", "trips"}
    assert all(
        set(t) == {"t", "dur", "a", "b", "bike", "paid", "gross", "credit", "ebike"}
        for t in payload["trips"]
    )

    s = _citibike_summary({})
    assert "stations" not in s
    assert "pairs" not in s
    assert all(set(f) == {"name", "out", "in"} for f in s["flow"])
