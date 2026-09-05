"""Normalise a Citi Bike (Lyft) account export into cache/citibike_trips.json.

Lyft's data download is station-to-station: each record carries the dock
names, a start time, a whole-minute duration and the fare line items, and no
GPS whatsoever. So this does not write to ``rides/`` and nothing here reaches
the matcher -- it produces a cache file that ``bike_routes.citibike``
summarises at export time, the way ``weather.py`` summarises Open-Meteo.

Offline by design. An earlier version resolved dock names to coordinates
against Citi Bike's public GBFS feed (which matched 214 of 216 names exactly)
in order to place markers on the map. That layer was dropped -- a dock's
position says nothing on its own, and a line between two docks is not a
route -- and it was the coordinates' only consumer, so the fetch, its cache
and its fallback went with it rather than sit here producing data nobody
reads. The dock name is the identity the stats need, and the export has it.

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
from pathlib import Path
from typing import Any

from bike_routes import config

# Bump when the trips file's layout changes, so citibike.py can reject a
# stale one rather than half-read it.
TRIPS_FORMAT = 1


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
    docks = {name for t in trips for name in (t["a"], t["b"])}
    print(f"  {len(trips)} trips ({duplicates} duplicate records dropped)")
    print(f"  {len(docks)} distinct docks")

    payload = {
        "format": TRIPS_FORMAT,
        "source": export_path.name,
        "trips": trips,
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
