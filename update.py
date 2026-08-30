"""Fetch new rides from Garmin Connect and rebuild the map.

Run from anywhere; paths resolve against the repo root:

    python update.py              # or: python update.py --days 30

Arguments are passed through to bike_routes.ingest.garmin_sync. The commit
is left unpushed on purpose -- check the map, then `git push`.

This is Python rather than a shell script so it runs the same on Windows,
WSL, macOS, and Linux (a .sh here also picks up CRLF endings on a Windows
checkout, which bash refuses to run).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GEOJSON = "docs/rides.geojson.gz"


def _run(*args: str, check: bool = True) -> int:
    """Run a command from the repo root and return its exit status.

    With check=True (the default) a failing step aborts the update.
    """
    return subprocess.run(args, cwd=ROOT, check=check).returncode  # noqa: S603 -- fixed argv, no shell


def main() -> int:
    """Fetch new rides, reprocess the map, and commit it if it changed."""
    py = sys.executable
    try:
        _run(py, "-m", "bike_routes.ingest.garmin_sync", "incoming", *sys.argv[1:])
        _run(py, "-m", "bike_routes.ingest.gpx_to_csv", "incoming", "rides")
        # Drop --no-png to also refresh the static PNGs.
        _run(py, "-m", "bike_routes", "--no-png")
    except subprocess.CalledProcessError as exc:
        # The failing step has already explained itself on stderr; a traceback
        # on top of that just buries it.  Every step runs as `python -m <mod>`,
        # so argv[2] is the module that failed.
        print(f"\nUpdate stopped: {exc.cmd[2]} exited {exc.returncode}.", file=sys.stderr)
        return exc.returncode

    changed = _run("git", "diff", "--quiet", "--", GEOJSON, check=False)
    if not changed:
        print("Map unchanged; nothing to commit.")
        return 0

    _run("git", "add", GEOJSON)
    _run("git", "commit", "-m", "Update bike map GeoJSON")
    print("Committed. Review the map, then: git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
