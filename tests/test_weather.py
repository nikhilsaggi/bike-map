"""Tests for the weather correlation analysis (no network)."""

from __future__ import annotations

import gzip
import json

import pytest
import weather_correlation as wc

from bike_routes.weather import (
    RAIN_BANDS,
    TEMP_BANDS,
    _band,
    _cache_well_formed,
    _load_cached_weather,
    _save_weather_cache,
    _weather_summary,
)


def test_load_rides(tmp_path):
    geojson = {
        "properties": {
            "dates": ["2024-01-05", "2024-06-01"],
            "rides": [[0, "08:00", 10.0], [0, "18:00", 5.0], [1, "12:00", None]],
        },
        "features": [],
    }
    p = tmp_path / "rides.geojson.gz"
    with gzip.open(p, "wt") as f:
        json.dump(geojson, f)
    per_day = wc.load_rides(p)
    # Two rides on the first date sum their miles; None distance counts as 0
    assert abs(per_day["2024-01-05"] - 15.0 * wc.KM_TO_MI) < 1e-9
    assert per_day["2024-06-01"] == 0.0


def test_summarize_bands():
    per_day = {"2024-06-01": 20.0, "2024-06-02": 10.0}
    weather = {
        "2024-06-01": {"tmax": 70.0, "precip": 0.0},  # warm + dry: rode
        "2024-06-02": {"tmax": 72.0, "precip": 0.5},  # warm + wet: rode
        "2024-06-03": {"tmax": 75.0, "precip": 0.0},  # warm + dry: no ride
        "2024-01-15": {"tmax": 20.0, "precip": 0.0},  # freezing: no ride
    }
    s = wc.summarize(per_day, weather)
    assert s["temp"]["65-80°F"]["days"] == 3
    assert s["temp"]["65-80°F"]["ride_days"] == 2
    assert abs(s["temp"]["65-80°F"]["pct"] - 200 / 3) < 1e-9
    assert s["temp"]["<32°F"] == {"days": 1, "ride_days": 0, "pct": 0.0, "mi_per_ride_day": 0.0}
    assert s["rain"]["dry"]["ride_days"] == 1
    assert s["rain"]["wet"]["ride_days"] == 1
    assert s["rain"]["wet"]["mi_per_ride_day"] == 10.0


def test_band_edges():
    assert wc._band(32.0, wc.TEMP_BANDS) == "32-50°F"
    assert wc._band(31.9, wc.TEMP_BANDS) == "<32°F"
    assert wc._band(150.0, wc.TEMP_BANDS) == ">80°F"
    assert wc._band(0.0, wc.RAIN_BANDS) == "dry"


# --- bike_routes.weather module tests ---


def test_module_band_classification():
    assert _band(25.0, TEMP_BANDS) == "<32°F"
    assert _band(55.0, TEMP_BANDS) == "50-65°F"
    assert _band(90.0, TEMP_BANDS) == ">80°F"
    assert _band(0.0, RAIN_BANDS) == "Dry"
    assert _band(0.1, RAIN_BANDS) == "Light rain"
    assert _band(0.5, RAIN_BANDS) == "Wet"


def test_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {"2024-06-01": {"tmax": 75.0, "precip": 0.0}}
    _save_weather_cache(data)
    loaded = _load_cached_weather()
    assert loaded == data


def test_cache_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _load_cached_weather() is None


def test_weather_summary_too_few_rides():
    ride_stats = {
        "2024-06-01_12-00-00_-0400.csv": {"start": "2024-06-01T12:00:00-04:00", "dist_m": 5000.0},
    }
    assert _weather_summary(ride_stats) is None


def test_weather_summary_with_mock_api(monkeypatch):
    ride_stats = {}
    for i in range(1, 21):
        fname = f"2024-06-{i:02d}_12-00-00_-0400.csv"
        ride_stats[fname] = {"start": f"2024-06-{i:02d}T12:00:00-04:00", "dist_m": 10000.0}

    fake_weather = {}
    for i in range(1, 31):
        fake_weather[f"2024-06-{i:02d}"] = {"tmax": 72.0, "precip": 0.0}

    monkeypatch.setattr(
        "bike_routes.weather._get_weather",
        lambda _start, _end: fake_weather,
    )

    result = _weather_summary(ride_stats)
    assert result is not None
    assert "temp" in result
    assert "rain" in result
    assert len(result["temp"]) == 5
    assert len(result["rain"]) == 3

    # Every ride day is 72F and dry, so those two bands take all the rides.
    band_65_80 = next(r for r in result["temp"] if r["label"] == "65-80°F")
    assert band_65_80["share"] == 100.0
    assert band_65_80["ride_days"] == 20
    assert band_65_80["days"] == 30
    assert band_65_80["expected"] == 100.0
    assert band_65_80["pct"] == pytest.approx(66.7, abs=0.1)
    dry = next(r for r in result["rain"] if r["label"] == "Dry")
    assert dry["share"] == 100.0

    # Shares are a composition of the rides: each chart sums to 100.
    for kind in ("temp", "rain"):
        assert sum(r["share"] for r in result[kind]) == pytest.approx(100.0, abs=0.1)
        # And expected is a composition of the days.
        assert sum(r["expected"] for r in result[kind]) == pytest.approx(100.0, abs=0.1)


def test_weather_summary_share_vs_expected(monkeypatch):
    """A band ridden more often than it occurs must exceed its expected share."""
    # 20 warm days, 20 freezing; rides on every warm day and 4 freezing ones.
    fake_weather = {}
    ride_stats = {}
    for i in range(1, 21):
        fake_weather[f"2024-06-{i:02d}"] = {"tmax": 72.0, "precip": 0.0}
        fake_weather[f"2024-01-{i:02d}"] = {"tmax": 20.0, "precip": 0.0}
        ride_stats[f"2024-06-{i:02d}_12-00-00_-0400.csv"] = {"dist_m": 10000.0}
    for i in range(1, 5):
        ride_stats[f"2024-01-{i:02d}_12-00-00_-0400.csv"] = {"dist_m": 10000.0}
    monkeypatch.setattr("bike_routes.weather._get_weather", lambda _s, _e: fake_weather)

    result = _weather_summary(ride_stats)
    assert result is not None
    warm = next(r for r in result["temp"] if r["label"] == "65-80°F")
    cold = next(r for r in result["temp"] if r["label"] == "<32°F")
    # 20 of 24 rides on half the days: sought out. 4 of 24 on the other half:
    # avoided. Both bands occur equally often, so expected splits evenly.
    assert warm["expected"] == cold["expected"] == 50.0
    assert warm["share"] == pytest.approx(83.3, abs=0.1)
    assert cold["share"] == pytest.approx(16.7, abs=0.1)
    assert warm["share"] > warm["expected"] > cold["share"]


def test_cache_well_formed_rejects_missing_keys(tmp_path, monkeypatch):
    """A cache written before a variable was added must not reach `w[key]`."""
    assert _cache_well_formed({"2024-06-01": {"tmax": 70.0, "precip": 0.0}})
    assert not _cache_well_formed({"2024-06-01": {"tmax": 70.0}})  # no precip
    assert not _cache_well_formed({"2024-06-01": "sunny"})
    assert not _cache_well_formed({})
    assert not _cache_well_formed([])

    path = tmp_path / "weather_cache.json"
    path.write_text('{"2024-06-01": {"tmax": 70.0}}')
    monkeypatch.setattr("bike_routes.config.WEATHER_CACHE_PATH", path)
    assert _load_cached_weather() is None
