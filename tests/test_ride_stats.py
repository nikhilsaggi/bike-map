"""Tests for per-ride time/distance stats."""

from __future__ import annotations

from pathlib import Path

from bike_routes import cache, config, ride_stats

CSV = """longitude,latitude,timestamp
-73.98478,40.76030,2023-12-17 18:41:53 -0500
-73.98478,40.76930,2023-12-17 18:51:53 -0500
-73.98478,40.77830,2023-12-17 19:01:53 -0500
"""


def test_parse_exporter_timestamp():
    dt = ride_stats._parse_ride_timestamp("2021-09-04 18:53:31 -0400")
    assert dt is not None
    assert dt.hour == 18
    assert dt.utcoffset().total_seconds() == -4 * 3600


def test_parse_iso_utc_timestamp():
    dt = ride_stats._parse_ride_timestamp("2024-06-19T17:35:52Z")
    assert dt is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_bad_timestamp():
    assert ride_stats._parse_ride_timestamp("not a date") is None
    assert ride_stats._parse_ride_timestamp("2024-06-19 17:35:52") is None  # no tz


def test_ride_stats_for_file(tmp_path):
    p = tmp_path / "ride.csv"
    p.write_text(CSV)
    rs = ride_stats._ride_stats_for_file(p)
    assert rs is not None
    # Two segments of ~0.009 deg latitude each (~1 km each)
    assert 1900 < rs["dist_m"] < 2100
    assert rs["duration_s"] == 20 * 60
    assert rs["start"].startswith("2023-12-17T18:41:53")


def test_ride_stats_missing_or_bad_file(tmp_path):
    assert ride_stats._ride_stats_for_file(tmp_path / "nope.csv") is None
    p = tmp_path / "empty.csv"
    p.write_text("longitude,latitude,timestamp\n")
    assert ride_stats._ride_stats_for_file(p) is None


def test_backfill_ride_stats(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rides = Path(config.RIDES_FOLDER)
    rides.mkdir()
    (rides / "2023-12-17_18-41-53_-0500.csv").write_text(CSV)

    state = cache._empty_state()
    state["processed_files"] = {
        "2023-12-17_18-41-53_-0500.csv",
        "2024-01-01_08-00-00_-0500.csv",  # not on disk: skipped, retried later
    }
    n = ride_stats._backfill_ride_stats(state)
    assert n == 1
    assert state["ride_stats"]["2023-12-17_18-41-53_-0500.csv"]["duration_s"] == 1200
    assert "2024-01-01_08-00-00_-0500.csv" not in state["ride_stats"]

    # Second call: nothing new to do
    assert ride_stats._backfill_ride_stats(state) == 0


def test_riding_summary():
    stats = {
        "a.csv": {"start": "2023-12-17T18:41:53-05:00", "duration_s": 3600, "dist_m": 20000},
        "b.csv": {"start": "2024-06-01T08:00:00-04:00", "duration_s": 1800, "dist_m": 10000},
        "c.csv": None,  # unparseable ride is ignored
    }
    s = ride_stats._riding_summary(stats)
    assert s["total_km"] == 30.0
    assert s["total_h"] == 1.5
    assert s["avg_kmh"] == 20.0
    assert s["longest_km"] == 20.0
    assert s["km_by_year"] == {"2023": 20.0, "2024": 10.0}
    assert s["by_hour"][18] == 1
    assert s["by_hour"][8] == 1
    assert sum(s["by_hour"]) == 2
    # 2023-12-17 is a Sunday (6), 2024-06-01 is a Saturday (5)
    assert s["by_weekday"][6] == 1
    assert s["by_weekday"][5] == 1


def test_riding_summary_empty():
    assert ride_stats._riding_summary({}) is None
    assert ride_stats._riding_summary({"a.csv": None}) is None
