"""Convert GPX files to the CSV format expected by bike_routes.py.

Parses <trkpt> elements from GPX XML and writes CSV with columns:
    longitude,latitude,timestamp

Usage:
    python gpx_to_csv.py incoming/ rides/
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


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

    first_ts = points[0][2]
    safe_ts = first_ts.replace(":", "-").replace("T", "_").replace("Z", "+0000")
    if "+" not in safe_ts and "-" not in safe_ts[11:]:
        safe_ts += "+0000"
    csv_name = safe_ts.split(".")[0] + ".csv"

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
        print(f"Usage: {sys.argv[0]} <gpx_dir> <csv_dir>")
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
