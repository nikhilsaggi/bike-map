"""Dream-subway overlay for the interactive map.

Reads the network ``tools/dream_subway/odnet.py`` writes and reshapes it into
the ``properties.subway`` block. Pure reshaping: no network, no state, no
graph. Returns None when the file is absent, which is every checkout but the
owner's -- the same contract as ``citibike``.

The network is a hypothetical fitted to where the rides begin and end. It is
not a measurement of anything on the ground, so nothing here may reach
``edge_counts``, ``coverage`` or ``features[]``: those carry a "a trace was
recorded here" contract this data has no claim on. It lives only in its own
block, and the page draws it as a layer of its own that starts off.

**The chords between stations are not routes.** A station's position is a real
lon/lat -- the centroid of a cluster of ride endpoints -- but the line between
two of them is drawn straight, because the network was built without consulting
a single street. Drawing it along streets would make a guess look like a
measurement, which is the same reason ``citibike`` refuses to route between two
docks.

**It is all-time and cannot follow the slider.** A station's weight is its trip
ends over the whole history and the lines are fitted to the whole history, so a
date-filtered version would move the markers while leaving the network they sit
on unchanged -- a filter that appears to work and does not.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from . import config

if TYPE_CHECKING:
    from pathlib import Path


def load_network(path: Path | None = None) -> dict[str, Any] | None:
    """Read the network file, or None when it has not been generated."""
    p = config.SUBWAY_NETWORK_PATH if path is None else path
    if not p.exists():
        return None
    with p.open(encoding="utf-8") as fh:
        return json.load(fh)


def _subway_summary(path: Path | None = None) -> dict[str, Any] | None:
    """Build the ``properties.subway`` block, or None when the network is absent.

    Stations are renumbered to the ones a line actually uses: the builder keeps
    a pool of candidates wider than the network it lays down, and a station on
    no line would draw as a stop nothing serves.
    """
    net = load_network(path)
    if not net:
        return None
    stations, meta = net.get("stations") or [], net.get("meta") or []
    if not stations or not meta:
        return None

    keep = sorted({s for line in meta for s in line["stops"]})
    if not keep:
        return None
    remap = {old: new for new, old in enumerate(keep)}

    serves: dict[int, list[str]] = {}
    for line in meta:
        for s in line["stops"]:
            serves.setdefault(remap[s], []).append(line["id"])

    out_stations = []
    for new, old in enumerate(keep):
        s = stations[old]
        lines = serves.get(new, [])
        out_stations.append(
            {
                "name": s["near"],
                "at": [round(s["at"][0], 5), round(s["at"][1], 5)],
                "ends": s["n"],
                "lines": lines,
                "xf": len(lines) > 1,
            }
        )

    out_lines = [
        {
            "id": line["id"],
            "name": line["name"],
            "colour": line["colour"],
            "stops": [remap[s] for s in line["stops"]],
            "km": line["km"],
        }
        for line in meta
    ]

    score = net.get("score") or {}
    return {
        "lines": out_lines,
        "stations": out_stations,
        "rides": score.get("rides"),
        "direct": round(score.get("direct", 0)),
        "one_change": round(score.get("one_change", 0)),
        "stranded": round(score.get("stranded", 0)),
        "reachable": score.get("reachable"),
    }
