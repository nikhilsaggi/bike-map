"""Tests for the Citibike trip ingest and stats block (no network at all)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from bike_routes.citibike import (
    SOURCE_OWN,
    SOURCE_UNKNOWN,
    TRIP_UNTRACED,
    _citibike_summary,
    ride_sources,
    trip_rides,
)
from bike_routes.ingest.citibike import (
    CacheUnreadableError,
    _cache_well_formed,
    _is_ebike,
    _load_cached_stations,
    _money,
    _normalise,
    _save_station_cache,
    ingest,
)

# Two docks GBFS knows, so the layer can place them.
STATIONS = {
    "A St & 1 Ave": (40.70, -73.94),
    "B St & 2 Ave": (40.75, -73.98),
}
# The same two docks as the cache stores them: [lon, lat].
STATIONS_LONLAT = {name: [lon, lat] for name, (lat, lon) in STATIONS.items()}


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
    """Point both caches at tmp_path and stub the GBFS fetch."""
    path = tmp_path / "citibike_trips.json"
    monkeypatch.setattr("bike_routes.config.CITIBIKE_TRIPS_PATH", path)
    monkeypatch.setattr(
        "bike_routes.config.CITIBIKE_STATIONS_PATH", tmp_path / "citibike_stations.json"
    )
    monkeypatch.setattr("bike_routes.ingest.citibike._fetch_stations", lambda: STATIONS)
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


def test_summary_rejects_a_stale_format(trips_path):
    """A cache from before the dock layer has no coordinates to draw."""
    trips_path.write_text(json.dumps({"format": 1, "trips": [{"t": 1}]}))
    assert _citibike_summary({}) is None


@pytest.mark.usefixtures("trips_path")
def test_summary_counts_docks_and_bikes(tmp_path):
    a, b = "A St & 1 Ave", "B St & 2 Ave"
    day = 1_700_000_000_000  # a Tuesday afternoon in NYC
    hour = 3_600_000
    records = [
        _record("r1", day, a, b, bike="100-0001"),
        _record("r2", day + hour, a, b, bike="100-0002"),
        _record("r3", day + 2 * hour, a, b, bike="100-0003"),
        _record("r4", day + 3 * hour, b, a, bike="100-0004"),
        # Same dock, one minute: an unlock re-docked. It still counts as a
        # trip and as a use at both ends of dock A.
        _record("r5", day + 4 * hour, a, a, dur=60_000, bike="100-0005"),
        # Bike 0001 again, from A -- but r1 left it at B, so it moved in
        # between and this is a bike met again rather than one picked back up.
        _record("r6", day + 5 * hour, a, b, bike="100-0001"),
    ]
    ingest(_write_export(tmp_path, records))
    s = _citibike_summary({})

    assert len(s["trips"]) == 6
    assert (s["reencounters"], s["resumes"]) == (1, 0)
    assert len(s["docks"]) == 2
    # A: four departures to B plus the re-docked unlock that also started
    # there, against r4's arrival and that unlock's own return. B mirrors it.
    docks = {d["name"]: (d["out"], d["in"]) for d in s["docks"]}
    assert docks == {a: (5, 2), b: (1, 4)}


@pytest.mark.usefixtures("trips_path")
def test_summary_splits_round_trips_from_bikes_met_again(tmp_path):
    """A repeated bike id means two different things; the panel reports both.

    See findings/bike-reencounters.md -- 200 of the 293 repeats in the real
    export are the bike you parked, and reporting them together made five
    years of round trips look like a small city.
    """
    a, b = "A St & 1 Ave", "B St & 2 Ave"
    day = 1_700_000_000_000
    hour = 3_600_000
    records = [
        # Out and back on one bike: it ends at A and is unlocked from A.
        _record("r1", day, a, b, bike="100-0001"),
        _record("r2", day + hour, b, a, bike="100-0001"),
        _record("r3", day + 2 * hour, a, b, bike="100-0001"),
        # Left at B on day one, taken from A a week later: it moved without me.
        _record("r4", day + 7 * 24 * hour, a, b, bike="100-0001"),
        # Left at A and unlocked from A four days on -- past the resume
        # window, so the shared dock does not make it a round trip.
        _record("r5", day, b, a, bike="100-0002"),
        _record("r6", day + 4 * 24 * hour, a, b, bike="100-0002"),
        # The same dock under Lyft's two spellings is still one dock (#26).
        _record("r7", day, a, "B St &\t2 Ave", bike="100-0003"),
        _record("r8", day + hour, b, a, bike="100-0003"),
    ]
    ingest(_write_export(tmp_path, records))
    s = _citibike_summary({})

    # r2, r3 and r8 are round trips; r4 and r6 are bikes met again.
    assert (s["reencounters"], s["resumes"]) == (2, 3)
    # ... and each met bike gets a row: [id, encounters, days between them,
    # recordings]. Encounters, not trips -- bike 0001 was ridden four times
    # but on two occasions, the middle two being the out-and-back that r1
    # started, so it reads 2x seven days apart rather than "4 trips". Getting
    # that wrong overstated every bike whose repeat was a round trip. No ride
    # index was passed, so nothing is recorded over any of them.
    assert s["met"] == [["100-0001", 2, [7], []], ["100-0002", 2, [4], []]]


def _flows(n, a, b, start=1_000):
    """n trips from dock a to dock b, each with a distinct rideId."""
    return [_record(f"{a}{b}{i}", start + i * 60_000, a, b, dur=300_000) for i in range(n)]


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
    assert len(s["days"]) == 2
    assert s["from"] == "2023-11-14"
    assert s["to"] == "2023-11-15"


@pytest.mark.usefixtures("trips_path")
def test_trip_rows_index_into_the_dock_and_day_lists(tmp_path):
    """The page filters docks by date, so each trip has to carry both.

    Indices rather than repeated strings: the rows are what makes the layer
    respond to the slider, and there is one per trip.
    """
    # 2023-11-14 and 2023-11-15, NYC local.
    day1, day2 = 1_699_982_000_000, 1_700_068_400_000
    ingest(
        _write_export(
            tmp_path,
            [
                _record("r1", day1, "A St & 1 Ave", "B St & 2 Ave"),
                _record("r2", day2, "B St & 2 Ave", "A St & 1 Ave"),
                _record("r3", day2, "A St & 1 Ave", "B St & 2 Ave"),
            ],
        )
    )
    s = _citibike_summary({})

    assert s["days"] == ["2023-11-14", "2023-11-15"]
    names = [d["name"] for d in s["docks"]]
    # Busiest first: A has 2 out + 1 in, B has 1 out + 2 in -- tied at 3, so
    # the name breaks it.
    assert names == ["A St & 1 Ave", "B St & 2 Ave"]
    # No ride index passed, so every trip cites no recording.
    assert s["trips"] == [[0, 1, 0, -1], [1, 0, 1, -1], [0, 1, 1, -1]]
    for a, b, d, ri in s["trips"]:
        assert names[a]
        assert names[b]
        assert s["days"][d]
        assert ri == TRIP_UNTRACED


@pytest.mark.usefixtures("trips_path")
def test_a_dock_gbfs_cannot_place_keeps_its_counts(tmp_path):
    """No coordinate means it is not drawn, not that it stops existing.

    Dropping it would quietly shrink the dock count and the totals in order
    to tidy the map.
    """
    ingest(
        _write_export(
            tmp_path,
            [
                _record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave"),
                _record("r2", 2_000, "A St & 1 Ave", "Gone St & Nowhere Ave"),
            ],
        )
    )
    s = _citibike_summary({})

    assert len(s["docks"]) == 3
    gone = next(d for d in s["docks"] if d["name"] == "Gone St & Nowhere Ave")
    assert gone["at"] is None
    assert (gone["out"], gone["in"]) == (0, 1)
    assert all(d["at"] is not None for d in s["docks"] if d["name"] != gone["name"])
    # Still a trip, still a day, still in the once-only count.
    assert len(s["trips"]) == 2


@pytest.mark.usefixtures("trips_path")
def test_station_cache_round_trip_and_shape_guard(tmp_path):
    _save_station_cache(STATIONS)
    assert _load_cached_stations() == STATIONS

    assert _cache_well_formed({"A": {"lat": 1.0, "lon": 2.0}})
    assert not _cache_well_formed({"A": {"lat": 1.0}})  # no lon
    assert not _cache_well_formed({"A": "somewhere"})
    assert not _cache_well_formed({})
    assert not _cache_well_formed([])

    (tmp_path / "citibike_stations.json").write_text('{"A": {"lat": 1.0}}')
    assert _load_cached_stations() is None


@pytest.mark.usefixtures("trips_path")
def test_ingest_falls_back_to_the_station_cache(tmp_path, monkeypatch):
    """A GBFS outage must not lose the docks placed on the last run."""
    _save_station_cache(STATIONS)

    def boom():
        msg = "gbfs down"
        raise OSError(msg)

    monkeypatch.setattr("bike_routes.ingest.citibike._fetch_stations", boom)
    payload = ingest(
        _write_export(tmp_path, [_record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave")])
    )
    assert all(at is not None for at in payload["docks"].values())


# -- Merging one export into another ------------------------------------------

# A year in milliseconds, roughly: enough to put two exports in different eras.
YEAR_MS = 365 * 24 * 3_600_000


@pytest.fixture
def two_exports(tmp_path):
    """An old export and a newer one that overlaps it by a single ride.

    The shape of the real problem: the console script pages back one year, so
    a second pull re-sends what it can still see and nothing older.
    """
    a, b = "A St & 1 Ave", "B St & 2 Ave"
    paths = []
    for name, records in (
        (
            "history_old.json",
            [_record("r1", 1_000_000, a, b), _record("r2", 1_000_000 + YEAR_MS, b, a)],
        ),
        # r2 again, exactly as the first export had it, plus a year's riding.
        (
            "history_new.json",
            [
                _record("r2", 1_000_000 + YEAR_MS, b, a),
                _record("r3", 1_000_000 + 2 * YEAR_MS, a, b),
            ],
        ),
    ):
        path = tmp_path / name
        path.write_text(json.dumps(records))
        paths.append(path)
    return paths


@pytest.mark.usefixtures("trips_path")
def test_ingest_merges_rather_than_replacing(two_exports):
    """The export covers one year; the cache covers everything ever ingested.

    Overwriting is what truncated the first history, so a later, shorter pull
    has to be a top-up.
    """
    first, second = two_exports
    ingest(first)
    payload = ingest(second)

    assert [t["id"] for t in payload["trips"]] == ["r1", "r2", "r3"]
    assert payload["sources"] == [first.name, second.name]


@pytest.mark.usefixtures("trips_path")
def test_merge_keys_on_ride_id_not_position(two_exports):
    """A ride in both files is one trip, and it is the fresher record."""
    first, second = two_exports
    ingest(first)
    payload = ingest(second)

    ids = [t["id"] for t in payload["trips"]]
    assert len(ids) == len(set(ids))
    # The second export is authoritative for a ride both describe.
    assert next(t for t in payload["trips"] if t["id"] == "r2")["dur"] == 600_000


@pytest.mark.usefixtures("trips_path")
def test_merge_recognises_a_cache_written_before_ids(trips_path, two_exports):
    """The owner's cache predates the id, so its trips are keyed by start time.

    Without this the first merge after the change would double every trip the
    two files share -- the same loss the merge exists to prevent, in the
    other direction.
    """
    first, second = two_exports
    ingest(first)
    stripped = json.loads(trips_path.read_text())
    for t in stripped["trips"]:
        del t["id"]
    trips_path.write_text(json.dumps(stripped))

    payload = ingest(second)
    # Three rides, not four: the shared one was recognised. The pre-id record
    # keeps no id -- there is none to invent -- so it is counted by its start.
    assert len(payload["trips"]) == 3
    assert [t["t"] for t in payload["trips"]] == sorted(t["t"] for t in payload["trips"])
    assert [t.get("id") for t in payload["trips"]] == [None, "r2", "r3"]


@pytest.mark.usefixtures("trips_path")
def test_replace_discards_the_cache_only_when_asked(two_exports):
    first, second = two_exports
    ingest(first)
    payload = ingest(second, replace=True)

    assert [t["id"] for t in payload["trips"]] == ["r2", "r3"]
    assert payload["sources"] == [second.name]


@pytest.mark.usefixtures("trips_path")
def test_ingest_refuses_to_overwrite_a_cache_it_cannot_read(trips_path, tmp_path):
    """A cache that reads as empty is a cache about to be silently truncated."""
    trips_path.write_text("{ not json")
    export = _write_export(tmp_path, [_record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave")])
    with pytest.raises(CacheUnreadableError):
        ingest(export)
    assert trips_path.read_text() == "{ not json"

    # --replace is the way out of it, so a broken cache must not block that.
    payload = ingest(export, replace=True)
    assert [t["id"] for t in payload["trips"]] == ["r1"]


@pytest.mark.usefixtures("trips_path")
def test_a_dock_keeps_a_coordinate_gbfs_stops_publishing(tmp_path, monkeypatch):
    """Docks do not move, and a merge must not un-draw one over a feed change."""
    ingest(_write_export(tmp_path, [_record("r1", 1_000, "A St & 1 Ave", "B St & 2 Ave")]))
    monkeypatch.setattr(
        "bike_routes.ingest.citibike._fetch_stations",
        lambda: {"B St & 2 Ave": STATIONS["B St & 2 Ave"]},
    )
    payload = ingest(
        _write_export(tmp_path, [_record("r2", 2_000, "A St & 1 Ave", "B St & 2 Ave")])
    )
    assert payload["docks"]["A St & 1 Ave"] == STATIONS_LONLAT["A St & 1 Ave"]


# -- Matching GPS rides to Citibike trips by the clock ------------------------

T0 = 1_700_000_000  # a Tuesday afternoon in NYC
# Two ten-minute trips an hour apart, so the covered window is T0 .. T0+4200.
TWO_TRIPS = [
    _record("t1", T0 * 1000, "A St & 1 Ave", "B St & 2 Ave", dur=600_000),
    _record("t2", (T0 + 3600) * 1000, "B St & 2 Ave", "A St & 1 Ave", dur=600_000),
]


def _stats(**rides):
    """Build a ride_stats dict from {name: (start_epoch, duration_s)}."""
    return {
        f"{name}.csv": {
            "start": dt.datetime.fromtimestamp(start, tz=dt.timezone.utc).isoformat(),
            "duration_s": float(dur),
            "dist_m": 1000.0,
        }
        for name, (start, dur) in rides.items()
    }


@pytest.mark.usefixtures("trips_path")
def test_ride_sources_matches_by_overlap(tmp_path):
    ingest(_write_export(tmp_path, TWO_TRIPS))
    src = ride_sources(
        _stats(
            inside=(T0 + 60, 300),  # sits within the first trip
            spanning=(T0, 4200),  # long enough to cover both
            between=(T0 + 1200, 300),  # in the window, between the trips
            grazing=(T0 + 570, 100),  # clips the first trip by 30s only
        )
    )
    assert src["inside.csv"] == 1
    assert src["spanning.csv"] == 2
    assert src["between.csv"] == SOURCE_OWN
    # Under MATCH_MIN_OVERLAP_S: abutting a trip is not riding it.
    assert src["grazing.csv"] == SOURCE_OWN


@pytest.mark.usefixtures("trips_path")
def test_ride_sources_calls_rides_outside_the_window_unknown(tmp_path):
    """The export's silence before it starts is not evidence of an own bike.

    This is the whole reason for a third state: with a truncated export most
    of the ride history predates any Citibike record.
    """
    ingest(_write_export(tmp_path, TWO_TRIPS))
    src = ride_sources(
        _stats(
            before=(T0 - 10_000, 600),
            after=(T0 + 10_000, 600),
            during=(T0 + 60, 300),
        )
    )
    assert src["before.csv"] == SOURCE_UNKNOWN
    assert src["after.csv"] == SOURCE_UNKNOWN
    assert src["during.csv"] == 1


@pytest.mark.usefixtures("trips_path")
def test_ride_sources_skips_rides_it_cannot_place_in_time(tmp_path):
    ingest(_write_export(tmp_path, TWO_TRIPS))
    src = ride_sources(
        {
            "no_stats.csv": None,
            "no_start.csv": {"duration_s": 600.0},
            "bad_start.csv": {"start": "not a date", "duration_s": 600.0},
            "fine.csv": {
                "start": dt.datetime.fromtimestamp(T0 + 60, tz=dt.timezone.utc).isoformat(),
                "duration_s": 300.0,
            },
        }
    )
    assert set(src) == {"fine.csv"}


@pytest.mark.usefixtures("trips_path")
def test_ride_sources_is_empty_without_a_cache():
    assert ride_sources(_stats(any=(T0, 600))) == {}


def _ride(start, dur):
    return {
        "start": dt.datetime.fromtimestamp(start, tz=dt.timezone.utc).isoformat(),
        "duration_s": float(dur),
        "dist_m": 1000.0,
    }


@pytest.mark.usefixtures("trips_path")
def test_summary_reports_the_own_bike_column(tmp_path):
    """The rides no Citibike trip overlaps, summarised beside the trips.

    Real filenames here rather than the label-based ones: the day count
    slices the date out of the name the way the pipeline's rides do.
    """
    ingest(_write_export(tmp_path, TWO_TRIPS))
    # The trips run T0..T0+600 and T0+3600..T0+4200; both own rides sit in
    # the gap between them, and all four share a date.
    s = _citibike_summary(
        {
            "2023-11-14_17-14-00_-0500.csv": _ride(T0 + 60, 300),  # in trip 1
            "2023-11-14_18-15-00_-0500.csv": _ride(T0 + 3660, 300),  # in trip 2
            "2023-11-14_17-25-00_-0500.csv": _ride(T0 + 700, 600),  # 10 min
            "2023-11-14_17-38-00_-0500.csv": _ride(T0 + 1500, 1800),  # 30 min
        }
    )
    own = s["own"]
    assert own["rides"] == 2
    assert own["hours"] == pytest.approx(0.7, abs=0.05)  # 10 + 30 minutes
    assert own["days"] == 1
    assert own["median_min"] == pytest.approx(20.0)  # median of 10 and 30
    # The Citibike column is the export's own count, not the matched subset.
    assert len(s["trips"]) == 2


@pytest.mark.usefixtures("trips_path")
def test_own_bike_column_is_empty_when_every_ride_matched(tmp_path):
    ingest(_write_export(tmp_path, TWO_TRIPS))
    s = _citibike_summary(_stats(cb=(T0 + 60, 300)))
    assert s["own"] == {"rides": 0, "hours": 0.0, "days": 0, "median_min": None}


# -- Naming the recording a trip was made on ----------------------------------


@pytest.mark.usefixtures("trips_path")
def test_trip_rides_names_the_recording_over_each_trip(tmp_path):
    """The same overlap ride_sources uses, read from the trip's side."""
    ingest(_write_export(tmp_path, TWO_TRIPS))
    traced = trip_rides(
        _stats(
            first=(T0 + 60, 300),  # sits within the first trip
            gap=(T0 + 1200, 300),  # in the window, over neither trip
        )
    )
    assert traced == ["first.csv", None]


@pytest.mark.usefixtures("trips_path")
def test_a_recording_spanning_two_trips_is_named_by_both(tmp_path):
    """One ride can hold several trips, and each of them cites it.

    This is the case the page has to caption: showing the recording draws
    more than the hop a reader clicked.
    """
    ingest(_write_export(tmp_path, TWO_TRIPS))
    assert trip_rides(_stats(long=(T0, 4200))) == ["long.csv", "long.csv"]


@pytest.mark.usefixtures("trips_path")
def test_trip_rides_takes_the_longer_overlap(tmp_path):
    """Two recordings over one trip: the one that covers more of it wins."""
    ingest(_write_export(tmp_path, TWO_TRIPS[:1]))
    assert trip_rides(_stats(clipped=(T0 - 200, 300), full=(T0, 600))) == ["full.csv"]
    # Under MATCH_MIN_OVERLAP_S neither is a candidate at all.
    assert trip_rides(_stats(grazing=(T0 - 200, 230))) == [None]


@pytest.mark.usefixtures("trips_path")
def test_trip_rides_is_empty_without_a_cache():
    assert trip_rides(_stats(any=(T0, 600))) == []


@pytest.mark.usefixtures("trips_path")
def test_trip_rows_cite_the_recording_by_export_index(tmp_path):
    """The fourth element indexes the same `rides` array a feature cites."""
    ingest(_write_export(tmp_path, TWO_TRIPS))
    stats = _stats(**{"2023-11-14_17-14-20_-0500": (T0 + 60, 300)})
    fname = next(iter(stats))
    s = _citibike_summary(stats, {"2021-01-01_00-00-00_-0500.csv": 0, fname: 1})
    assert [row[3] for row in s["trips"]] == [1, TRIP_UNTRACED]
    # A ride the export never indexed cannot be cited, and says so rather
    # than pointing at whatever sits at that position.
    s = _citibike_summary(stats, {"2021-01-01_00-00-00_-0500.csv": 0})
    assert [row[3] for row in s["trips"]] == [TRIP_UNTRACED, TRIP_UNTRACED]
