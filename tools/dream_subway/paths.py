"""Shared paths and readers for the Desire Lines derivation.

Every script runs from the repo root (``python tools/dream_subway/od.py``),
which puts this directory on ``sys.path`` first, so ``from paths import ...``
resolves without help.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
HERE = Path(__file__).resolve().parent
# Intermediates land in cache/ (gitignored), beside every other generated file.
WORK = Path(os.environ.get("DREAM_SUBWAY_WORK", ROOT / "cache" / "dream_subway"))
GEO = Path(os.environ.get("DREAM_SUBWAY_GEO", ROOT / "docs" / "rides.geojson.gz"))
RIDES = Path(os.environ.get("DREAM_SUBWAY_RIDES", ROOT / "rides"))

WORK.mkdir(parents=True, exist_ok=True)

# Metres per degree of latitude, and of longitude at a given latitude.
LAT = 111320.0


def lonm(lat: float) -> float:
    """Return metres per degree of longitude at ``lat``."""
    return 111320.0 * math.cos(math.radians(lat))


def work(name: str) -> Path:
    """Return the path of one intermediate file."""
    return WORK / name


def read_json(name: str) -> Any:  # noqa: ANN401 -- caller knows the shape
    """Load one intermediate file."""
    with work(name).open() as fh:
        return json.load(fh)


def write_json(
    name: str,
    obj: object,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
) -> Path:
    """Write one intermediate file; ``indent`` and ``separators`` go to ``json.dump``."""
    path = work(name)
    with path.open("w") as fh:
        json.dump(obj, fh, indent=indent, separators=separators)
    return path


def load_geo() -> dict[str, Any]:
    """Load the published map export."""
    with gzip.open(GEO) as fh:
        return json.load(fh)


def geo_docks(geo: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """List the Citi Bike docks the export could place -- the station gazetteer."""
    geo = geo if geo is not None else load_geo()
    return [d for d in geo["properties"]["citibike"]["docks"] if d.get("at")]


def ride_ends() -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    """List ``(file name, first fix, last fix)`` per ride CSV, in file-name order.

    A file with fewer than two fixes, or a first or last fix that does not
    parse, is skipped.
    """
    out = []
    for path in sorted(RIDES.glob("*.csv")):
        with path.open(newline="") as fh:
            rows = list(csv.reader(fh))
        if len(rows) < 3:
            continue
        body = rows[1:]
        try:
            a = (float(body[0][0]), float(body[0][1]))
            b = (float(body[-1][0]), float(body[-1][1]))
        except ValueError:
            continue
        out.append((path.name, a, b))
    return out


def seglen(coords: list[list[float]]) -> float:
    """Return a [lon, lat] polyline's length in metres."""
    t = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        la = (y1 + y2) / 2
        t += math.hypot((x2 - x1) * 111320.0 * math.cos(math.radians(la)), (y2 - y1) * LAT)
    return t
