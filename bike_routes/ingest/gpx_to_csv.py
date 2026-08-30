"""Convert GPX files to the CSV format the pipeline reads from rides/.

Parses <trkpt> elements from GPX XML and writes CSV with columns:
    longitude,latitude,timestamp

Output files are named for the ride's first fix in NYC local time with its
UTC offset ("2026-01-02_13-11-19_-0500.csv") -- the whole rides/ directory
follows that convention, and export.py slices the date, hour and year
straight out of the filename.

Usage:
    python -m bike_routes.ingest.gpx_to_csv incoming/ rides/
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo

    _NYC_TZ: ZoneInfo | None = ZoneInfo("America/New_York")
except Exception:  # tz database unavailable; keep the timestamp's own offset
    _NYC_TZ = None


def _local_stem(first_ts: str) -> str:
    """Filename stem for a ride: NYC local time plus UTC offset.

    Accepts the exporter format ("2021-09-04 18:53:31 -0400") and ISO 8601
    as written by GPX ("2026-01-02T18:11:19Z").  Returns "" if the value is
    unparseable or carries no timezone.
    """
    ts = first_ts.strip()
    for parse in (
        lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z"),
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
    ):
        try:
            dt = parse(ts)
        except ValueError:
            continue
        if dt.tzinfo is None:
            continue
        if _NYC_TZ is not None:
            dt = dt.astimezone(_NYC_TZ)
        return dt.strftime("%Y-%m-%d_%H-%M-%S_%z")
    return ""


def gpx_to_csv(gpx_path: Path, output_dir: Path) -> Path | None:
    """Parse a GPX file and write a CSV. Returns output path, or None if skipped."""
    tree = ET.parse(gpx_path)  # noqa: S314 -- input is local GPX files, not untrusted data
    root = tree.getroot()

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    points: list[tuple[str, str, str]] = []
    for trkpt in root.iter(f"{ns}trkpt"):
        lat = trkpt.get("lat")
        lon = trkpt.get("lon")
        time_el = trkpt.find(f"{ns}time")
        if lat and lon and time_el is not None and time_el.text:
            points.append((lon, lat, time_el.text))

    if not points:
        print(f"  Skipping {gpx_path.name}: no trackpoints found")
        return None

    stem = _local_stem(points[0][2])
    if not stem:
        print(f"  Skipping {gpx_path.name}: unparseable timestamp {points[0][2]!r}")
        return None
    csv_name = stem + ".csv"

    out_path = output_dir / csv_name
    if out_path.exists():
        return None

    with out_path.open("w", newline="") as f:
        f.write("longitude,latitude,timestamp\n")
        for lon, lat, ts in points:
            f.write(f"{lon},{lat},{ts}\n")

    print(f"  {gpx_path.name} -> {csv_name} ({len(points)} points)")
    return out_path


def main() -> None:
    """Convert all GPX files in a directory to pipeline-ready CSVs."""
    if len(sys.argv) != 3:
        # argv[0] is the module file under `python -m`, which is not how
        # anyone invokes this; print the documented form instead.
        print("Usage: python -m bike_routes.ingest.gpx_to_csv <gpx_dir> <csv_dir>")
        sys.exit(1)

    gpx_dir = Path(sys.argv[1])
    csv_dir = Path(sys.argv[2])
    csv_dir.mkdir(parents=True, exist_ok=True)

    gpx_files = sorted(gpx_dir.glob("*.gpx"))
    if not gpx_files:
        print(f"No GPX files found in {gpx_dir}")
        return

    print(f"Converting {len(gpx_files)} GPX files...")
    converted = 0
    for gpx_path in gpx_files:
        result = gpx_to_csv(gpx_path, csv_dir)
        if result is not None:
            converted += 1

    print(f"Done: {converted} new CSV files written to {csv_dir}")


if __name__ == "__main__":
    main()
