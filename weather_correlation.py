"""Correlate riding behaviour with historical NYC weather.

Joins the ride index in docs/rides.geojson.gz (one [date_index, "HH:MM",
distance_km] entry per ride) against Open-Meteo's keyless historical daily
weather for Central Park, then reports how temperature and precipitation
affect ride probability and distance.

Usage:
    python weather_correlation.py            # prints stats, writes chart
    python weather_correlation.py --no-chart # stats only

Outputs sample_output/weather_correlation.png unless --no-chart is given.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt  # backend must be set before this import

GEOJSON_PATH = Path("docs/rides.geojson.gz")
CHART_PATH = Path("sample_output/weather_correlation.png")
NYC_LAT, NYC_LON = 40.78, -73.97  # Central Park
KM_TO_MI = 0.621371

TEMP_BANDS = [
    (-100, 32, "<32°F"),
    (32, 50, "32-50°F"),
    (50, 65, "50-65°F"),
    (65, 80, "65-80°F"),
    (80, 200, ">80°F"),
]
RAIN_BANDS = [(0.0, 0.04, "dry"), (0.04, 0.2, "light rain"), (0.2, 100.0, "wet")]


def load_rides(path: Path) -> dict[str, float]:
    """Return {date: total_miles} for every riding day in the map data."""
    with gzip.open(path) as f:
        data = json.load(f)
    dates = data["properties"]["dates"]
    per_day: dict[str, float] = {}
    for date_idx, _time, dist_km in data["properties"]["rides"]:
        d = dates[date_idx]
        per_day[d] = per_day.get(d, 0.0) + (dist_km or 0.0) * KM_TO_MI
    return per_day


def fetch_weather(start: str, end: str) -> dict[str, dict[str, float]]:
    """Fetch daily max temperature (°F) and precipitation (in) for NYC."""
    params = urllib.parse.urlencode(
        {
            "latitude": NYC_LAT,
            "longitude": NYC_LON,
            "start_date": start,
            "end_date": end,
            "daily": "temperature_2m_max,precipitation_sum",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "timezone": "America/New_York",
        }
    )
    url = f"https://archive-api.open-meteo.com/v1/archive?{params}"
    with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 -- fixed https host
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


def _band(value: float, bands: list[tuple[float, float, str]]) -> str:
    for lo, hi, label in bands:
        if lo <= value < hi:
            return label
    return bands[-1][2]


def summarize(
    per_day: dict[str, float], weather: dict[str, dict[str, float]]
) -> dict[str, dict[str, dict[str, float]]]:
    """Aggregate ride probability and distance per temperature/rain band.

    Returns {"temp": {band: {days, ride_days, pct, mi_per_ride_day}}, "rain": ...}.
    """
    out: dict[str, dict[str, dict[str, float]]] = {"temp": {}, "rain": {}}
    for kind, bands, key in (("temp", TEMP_BANDS, "tmax"), ("rain", RAIN_BANDS, "precip")):
        stats = {label: {"days": 0, "ride_days": 0, "miles": 0.0} for _lo, _hi, label in bands}
        for d, w in weather.items():
            row = stats[_band(w[key], bands)]
            row["days"] += 1
            if d in per_day:
                row["ride_days"] += 1
                row["miles"] += per_day[d]
        for label, row in stats.items():
            out[kind][label] = {
                "days": row["days"],
                "ride_days": row["ride_days"],
                "pct": 100 * row["ride_days"] / row["days"] if row["days"] else 0.0,
                "mi_per_ride_day": row["miles"] / row["ride_days"] if row["ride_days"] else 0.0,
            }
    return out


def render_chart(summary: dict[str, dict[str, dict[str, float]]], path: Path) -> None:
    """Save a two-panel bar chart of ride probability by temp and rain."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), facecolor="#0d0d0d")
    panels = (
        (axes[0], "temp", [b[2] for b in TEMP_BANDS], "Ride probability by max temperature"),
        (axes[1], "rain", [b[2] for b in RAIN_BANDS], "Ride probability by precipitation"),
    )
    for ax, kind, labels, title in panels:
        pcts = [summary[kind][label]["pct"] for label in labels]
        ax.bar(labels, pcts, color="#7b3ff2")
        ax.set_facecolor("#0d0d0d")
        ax.set_title(title, color="white", fontsize=11)
        ax.set_ylabel("% of days with a ride", color="#ccc", fontsize=9)
        ax.tick_params(colors="#ccc", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#444")
        for i, pct in enumerate(pcts):
            ax.text(i, pct + 1, f"{pct:.0f}%", ha="center", color="white", fontsize=9)
        ax.set_ylim(0, 100)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    print(f"Chart -> {path}")


def main(argv: list[str] | None = None) -> None:
    """Run the weather correlation analysis."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--no-chart", action="store_true", help="print stats only")
    args = parser.parse_args(argv)

    if not GEOJSON_PATH.exists():
        sys.exit(f"{GEOJSON_PATH} not found -- run the pipeline first")
    per_day = load_rides(GEOJSON_PATH)
    first, last = min(per_day), max(per_day)
    # The archive lags a few days; never request too close to today
    today = datetime.now(tz=timezone.utc).date()
    end = min(date.fromisoformat(last), today - timedelta(days=3)).isoformat()
    print(f"{len(per_day)} riding days between {first} and {last}; fetching weather...")
    weather = fetch_weather(first, end)

    summary = summarize(per_day, weather)
    total_days = sum(r["days"] for r in summary["temp"].values())
    total_ride_days = sum(r["ride_days"] for r in summary["temp"].values())
    print(
        f"\n{total_days} days, {total_ride_days} with a ride "
        f"({100 * total_ride_days / total_days:.0f}% overall)\n"
    )
    for kind, title in (("temp", "By max temperature"), ("rain", "By precipitation")):
        print(title)
        for label, row in summary[kind].items():
            print(
                f"  {label:>10}: rode {row['pct']:5.1f}% of {row['days']:4d} days"
                f"   ({row['mi_per_ride_day']:.1f} mi per riding day)"
            )
        print()

    if not args.no_chart:
        render_chart(summary, CHART_PATH)


if __name__ == "__main__":
    main()
