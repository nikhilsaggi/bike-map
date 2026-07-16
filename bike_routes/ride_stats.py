"""Per-ride time/distance stats derived from CSV timestamps."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import config
from .gps import haversine_m

try:
    from zoneinfo import ZoneInfo

    _NYC_TZ = ZoneInfo("America/New_York")
except Exception:  # tz database unavailable; timestamps keep their own offset
    _NYC_TZ = None


def _parse_ride_timestamp(ts: str) -> datetime | None:
    """Parse a ride CSV timestamp.

    Accepts the exporter format ("2021-09-04 18:53:31 -0400") and ISO 8601
    as written by GPX (e.g. "2024-06-19T17:35:52Z").  Returns None if the
    value is unparseable or has no timezone.
    """
    ts = ts.strip()
    for parse in (
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z"),
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
    ):
        try:
            dt = parse(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            return None
        return dt
    return None


def _ride_stats_for_file(path: Path) -> dict[str, Any] | None:
    """Compute {start, duration_s, dist_m} for one ride CSV.

    start is ISO 8601 in NYC local time (or the timestamp's own offset if
    the tz database is unavailable -- equivalent for the exporter format,
    which already carries local offsets).  Distance is the raw GPS track
    length; duration is elapsed wall-clock time between first and last fix.
    """
    first_ts: str | None = None
    last_ts: str | None = None
    lats: list[float] = []
    lons: list[float] = []
    try:
        with path.open() as f:
            next(f)  # header
            for line in f:
                parts = line.rstrip("\n").split(",")
                if len(parts) < 3:
                    continue
                lons.append(float(parts[0]))
                lats.append(float(parts[1]))
                if first_ts is None:
                    first_ts = parts[2]
                last_ts = parts[2]
    except Exception:
        return None
    if len(lats) < 2 or first_ts is None or last_ts is None:
        return None

    la = np.array(lats)
    lo = np.array(lons)
    dist_m = float(haversine_m(la[:-1], lo[:-1], la[1:], lo[1:]).sum())

    t0 = _parse_ride_timestamp(first_ts)
    t1 = _parse_ride_timestamp(last_ts)
    if t0 is None:
        return None
    start = t0.astimezone(_NYC_TZ) if _NYC_TZ is not None else t0
    duration_s = (t1 - t0).total_seconds() if t1 is not None else None
    return {
        "start": start.isoformat(),
        "duration_s": duration_s,
        "dist_m": round(dist_m, 1),
    }


def _backfill_ride_stats(state: dict[str, Any]) -> int:
    """Compute stats for processed rides that don't have them yet.

    Unparseable rides are stored as None so they aren't retried every run;
    rides whose CSV is missing on disk are skipped and retried next run.
    """
    stats = state.setdefault("ride_stats", {})
    missing = sorted(state["processed_files"] - stats.keys())
    n = 0
    for fname in missing:
        path = Path(config.RIDES_FOLDER) / fname
        if not path.exists():
            continue
        stats[fname] = _ride_stats_for_file(path)
        n += 1
    return n


def _riding_summary(ride_stats: dict[str, dict[str, Any] | None]) -> dict[str, Any] | None:
    """Aggregate per-ride stats into the map's riding-summary property."""
    by_hour = [0] * 24
    by_weekday = [0] * 7
    km_by_year: dict[str, float] = {}
    total_km = 0.0
    total_s = 0.0
    longest_km = 0.0
    n = 0
    for rs in ride_stats.values():
        if not rs or not rs.get("start"):
            continue
        dt = datetime.fromisoformat(rs["start"])
        km = rs["dist_m"] / 1000
        by_hour[dt.hour] += 1
        by_weekday[dt.weekday()] += 1
        year = str(dt.year)
        km_by_year[year] = km_by_year.get(year, 0.0) + km
        total_km += km
        longest_km = max(longest_km, km)
        if rs.get("duration_s"):
            total_s += rs["duration_s"]
        n += 1
    if n == 0:
        return None
    return {
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "km_by_year": {y: round(v, 1) for y, v in sorted(km_by_year.items())},
        "total_km": round(total_km, 1),
        "total_h": round(total_s / 3600, 1),
        "avg_kmh": round(total_km / (total_s / 3600), 1) if total_s else None,
        "longest_km": round(longest_km, 1),
    }
