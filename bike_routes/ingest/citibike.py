"""Normalise a Citi Bike (Lyft) account export into cache/citibike_trips.json.

Lyft's data download is station-to-station: each record carries the dock
names, a start time, a whole-minute duration and the fare line items, and no
GPS whatsoever. So this does not write to ``rides/`` and nothing here reaches
the matcher -- it produces a cache file that ``bike_routes.citibike``
summarises at export time, the way ``weather.py`` summarises Open-Meteo.

Dock names are resolved to coordinates against Citi Bike's public GBFS feed,
whose ``name`` field is the same string the export uses, so the map can place
a marker per dock. A name the feed no longer lists (a renamed or removed dock)
keeps its trips and its counts and simply gets no coordinate: it drops out of
the drawn layer, not out of the numbers.

The export is a manual browser download from account.lyft.com/privacy/data,
so this runs by hand rather than from update.py. Once the cache exists every
``python -m bike_routes`` picks it up without a flag.

Usage:
    python -m bike_routes.ingest.citibike ~/citibikenyc_history_2026-09-04.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

from bike_routes import config

GBFS_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_information.json"

# Bump when the trips file's layout changes, so citibike.py can reject a
# stale one rather than half-read it.
TRIPS_FORMAT = 2

# Every key a station entry must carry for the summary to use it. Fails
# closed the same way weather's cache guard does.
STATION_KEYS = ("lat", "lon")


def _fetch_stations() -> dict[str, tuple[float, float]]:
    with urllib.request.urlopen(GBFS_URL, timeout=30) as resp:
        payload = json.load(resp)
    out: dict[str, tuple[float, float]] = {}
    for s in payload["data"]["stations"]:
        name, lat, lon = s.get("name"), s.get("lat"), s.get("lon")
        if name and lat is not None and lon is not None:
            out[name] = (lat, lon)
    return out


def _cache_well_formed(cached: object) -> bool:
    """Fail closed: a cache whose entries lack a coordinate is unusable."""
    if not isinstance(cached, dict) or not cached:
        return False
    return all(isinstance(v, dict) and all(k in v for k in STATION_KEYS) for v in cached.values())


def _load_cached_stations() -> dict[str, tuple[float, float]] | None:
    if not config.CITIBIKE_STATIONS_PATH.exists():
        return None
    try:
        with config.CITIBIKE_STATIONS_PATH.open() as f:
            cached = json.load(f)
    except Exception:
        return None
    if not _cache_well_formed(cached):
        print("  Cached station list has an unexpected shape; ignoring it")
        return None
    return {name: (v["lat"], v["lon"]) for name, v in cached.items()}


def _save_station_cache(stations: dict[str, tuple[float, float]]) -> None:
    config.CITIBIKE_STATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: {"lat": lat, "lon": lon} for name, (lat, lon) in stations.items()}
    with config.CITIBIKE_STATIONS_PATH.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))


def _get_stations() -> dict[str, tuple[float, float]] | None:
    try:
        stations = _fetch_stations()
        _save_station_cache(stations)
    except Exception as exc:
        print(f"  GBFS unavailable ({exc}); trying cache...")
        cached = _load_cached_stations()
        if cached:
            print(f"  Using cached station list ({len(cached)} stations)")
        else:
            print("  No station cache available; cannot resolve dock coordinates")
        return cached
    else:
        print(f"  Fetched {len(stations)} stations from GBFS")
        return stations


def _money(formatted: object) -> float:
    """Parse a Lyft money string ("$1.62", "-$1.28", "$0.00") to a float."""
    if not isinstance(formatted, str):
        return 0.0
    text = formatted.strip()
    sign = -1.0 if text.startswith("-") else 1.0
    digits = text.lstrip("-").lstrip("$").replace(",", "")
    try:
        return sign * float(digits)
    except ValueError:
        return 0.0


def _is_ebike(record: dict[str, Any]) -> bool:
    """Report whether a line item names an ebike.

    A floor, not a count: a free ebike ride can carry no such line item, so
    callers must report this as "at least", never as the total.
    """
    return any(
        "ebike" in str(li.get("title", "")).lower().replace("e-bike", "ebike")
        for li in record.get("lineItems", [])
    )


def _normalise(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Dedupe by rideId and flatten each record. Returns (trips, duplicates)."""
    seen: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for r in records:
        ride_id = r.get("rideId")
        if not ride_id:
            continue
        if ride_id in seen:
            duplicates += 1
            continue
        seen[ride_id] = r

    trips = []
    for r in sorted(seen.values(), key=lambda r: int(r["startTimeMs"])):
        amounts = [_money(li.get("amount", {}).get("formatted")) for li in r.get("lineItems", [])]
        trips.append(
            {
                "t": int(r["startTimeMs"]),
                "dur": int(r["duration"]),
                "a": r["startAddress"],
                "b": r["endAddress"],
                "bike": r.get("rideableName", ""),
                "paid": round(_money(r.get("price", {}).get("formatted")), 2),
                "gross": round(sum(a for a in amounts if a > 0), 2),
                "credit": round(-sum(a for a in amounts if a < 0), 2),
                "ebike": _is_ebike(r),
            }
        )
    return trips, duplicates


def ingest(export_path: Path) -> dict[str, Any]:
    """Read a Lyft export and write the normalised trips cache."""
    with export_path.open() as f:
        records = json.load(f)
    if not isinstance(records, list):
        msg = f"{export_path} is not a list of ride records"
        raise TypeError(msg)

    trips, duplicates = _normalise(records)
    used = sorted({name for t in trips for name in (t["a"], t["b"])})
    print(f"  {len(trips)} trips ({duplicates} duplicate records dropped)")

    stations = _get_stations() or {}
    # Every dock the export names, whether or not GBFS could place it. A dock
    # with no coordinate keeps its counts and simply is not drawn -- dropping
    # it would quietly shrink totals to tidy the map.
    docks = {
        name: (
            [round(stations[name][1], 5), round(stations[name][0], 5)] if name in stations else None
        )
        for name in used
    }
    placed = sum(1 for v in docks.values() if v)
    print(f"  {len(used)} distinct docks, {placed} placed by GBFS")
    for name, at in docks.items():
        if at is None:
            print(f"    no coordinates: {name}")

    payload = {
        "format": TRIPS_FORMAT,
        "source": export_path.name,
        "trips": trips,
        "docks": docks,
    }
    config.CITIBIKE_TRIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.CITIBIKE_TRIPS_PATH.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
    return payload


def main() -> int:
    """Ingest the export named on the command line."""
    parser = argparse.ArgumentParser(
        description="Normalise a Citi Bike account export into the pipeline cache."
    )
    parser.add_argument("export", type=Path, help="citibikenyc_history_*.json from Lyft")
    args = parser.parse_args()

    if not args.export.exists():
        print(f"No such file: {args.export}")
        return 1

    print(f"Reading {args.export}...")
    payload = ingest(args.export)
    print(f"Done: wrote {config.CITIBIKE_TRIPS_PATH} ({len(payload['trips'])} trips)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
