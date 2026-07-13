"""Tests for the weather correlation analysis (no network)."""

from __future__ import annotations

import gzip
import json

import weather_correlation as wc


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
        "2024-06-01": {"tmax": 70.0, "precip": 0.0},   # warm + dry: rode
        "2024-06-02": {"tmax": 72.0, "precip": 0.5},   # warm + wet: rode
        "2024-06-03": {"tmax": 75.0, "precip": 0.0},   # warm + dry: no ride
        "2024-01-15": {"tmax": 20.0, "precip": 0.0},   # freezing: no ride
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
