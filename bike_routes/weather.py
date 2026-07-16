"""Weather correlation stats for the interactive map sidebar."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

NYC_LAT, NYC_LON = 40.78, -73.97
KM_TO_MI = 0.621371

WEATHER_CACHE_PATH = Path("weather_cache.json")

TEMP_BANDS: list[tuple[float, float, str]] = [
    (-100, 32, "<32°F"),
    (32, 50, "32-50°F"),
    (50, 65, "50-65°F"),
    (65, 80, "65-80°F"),
    (80, 200, ">80°F"),
]
RAIN_BANDS: list[tuple[float, float, str]] = [
    (0.0, 0.04, "Dry"),
    (0.04, 0.2, "Light rain"),
    (0.2, 100.0, "Wet"),
]


def _band(value: float, bands: list[tuple[float, float, str]]) -> str:
    for lo, hi, label in bands:
        if lo <= value < hi:
            return label
    return bands[-1][2]


def _fetch_weather(start: str, end: str) -> dict[str, dict[str, float]]:
    params = urllib.parse.urlencode({
        "latitude": NYC_LAT,
        "longitude": NYC_LON,
        "start_date": start,
        "end_date": end,
        "daily": "temperature_2m_max,precipitation_sum",
        "temperature_unit": "fahrenheit",
        "precipitation_unit": "inch",
        "timezone": "America/New_York",
    })
    url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        payload = json.load(resp)
    daily = payload["daily"]
    out: dict[str, dict[str, float]] = {}
    for d, tmax, precip in zip(
        daily["time"], daily["temperature_2m_max"], daily["precipitation_sum"]
    ):
        if tmax is None:
            continue
        out[d] = {"tmax": tmax, "precip": precip or 0.0}
    return out


def _load_cached_weather() -> dict[str, dict[str, float]] | None:
    if not WEATHER_CACHE_PATH.exists():
        return None
    try:
        with WEATHER_CACHE_PATH.open() as f:
            return json.load(f)
    except Exception:
        return None


def _save_weather_cache(weather: dict[str, dict[str, float]]) -> None:
    with WEATHER_CACHE_PATH.open("w") as f:
        json.dump(weather, f, separators=(",", ":"))


def _get_weather(start: str, end: str) -> dict[str, dict[str, float]] | None:
    try:
        weather = _fetch_weather(start, end)
        _save_weather_cache(weather)
        return weather
    except Exception as exc:
        print(f"  Weather API unavailable ({exc}); trying cache...")
        cached = _load_cached_weather()
        if cached:
            print(f"  Using cached weather ({len(cached)} days)")
        else:
            print("  No weather cache available; skipping weather stats")
        return cached


def _weather_summary(
    ride_stats: dict[str, dict[str, Any] | None],
) -> dict[str, list[dict[str, Any]]] | None:
    """Compute ride-probability-by-weather summary for the GeoJSON."""
    per_day: dict[str, float] = {}
    for fname, rs in ride_stats.items():
        if not rs or not rs.get("dist_m"):
            continue
        d = fname[:10]
        per_day[d] = per_day.get(d, 0.0) + rs["dist_m"] / 1000 * KM_TO_MI

    if len(per_day) < 10:
        return None

    first, last = min(per_day), max(per_day)
    today = datetime.now(tz=timezone.utc).date()
    end = min(date.fromisoformat(last), today - timedelta(days=3)).isoformat()

    weather = _get_weather(first, end)
    if not weather:
        return None

    result: dict[str, list[dict[str, Any]]] = {}
    for kind, bands, key in (
        ("temp", TEMP_BANDS, "tmax"),
        ("rain", RAIN_BANDS, "precip"),
    ):
        rows = []
        for _lo, _hi, label in bands:
            days = ride_days = 0
            miles = 0.0
            for d, w in weather.items():
                if _band(w[key], bands) != label:
                    continue
                days += 1
                if d in per_day:
                    ride_days += 1
                    miles += per_day[d]
            pct = round(100 * ride_days / days, 1) if days else 0.0
            avg_mi = round(miles / ride_days, 1) if ride_days else 0.0
            rows.append({"label": label, "pct": pct, "avg_mi": avg_mi})
        result[kind] = rows

    return result
