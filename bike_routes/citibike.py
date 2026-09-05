"""Citi Bike trip stats and dock layer for the interactive map.

Reads the cache ``ingest.citibike`` writes and aggregates it into the
``properties.citibike`` block. Pure aggregation: no network, no state, no
graph. Returns None when the cache is absent, which is every checkout but
the owner's.

These trips are dock-to-dock with no GPS trace, so nothing here may reach
``edge_counts``, ``edge_traversals`` or ``coverage`` -- those carry a
"measured" contract this data cannot honour, and no route between two docks
is ever drawn. What is exact is where a trip began and where it ended, and
when, so the block ships the trips themselves rather than a summary of them:
the page places a dock per marker, sizes it by use within the date range the
slider is showing, and draws a dock's own trips only when someone clicks it.
The point is to be explorable next to the ride heatmap, not to state a
finding -- a reader comparing where the bike goes against where the docks
are is doing something no ranked list does for them.

Speed is deliberately absent. Every duration in the export is a whole number
of minutes and the end time is the start plus that duration, so at a median
8-minute trip the quantisation is +/-6%. Alongside GPS-measured edge speeds
it would read as the same kind of number and is not.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from . import config

# Same dock, under this long: a bad bike unlocked and re-docked, not a ride.
# Reported on its own rather than silently folded into the totals.
ABORT_MAX_MS = 120_000

# How many docks from each end of the net-flow range the stats section lists.
# Ranked here rather than in the browser, the way the speed corridors are.
FLOW_TOP = 3
FLOW_BOTTOM = 2

try:
    from zoneinfo import ZoneInfo

    _NYC_TZ: ZoneInfo | None = ZoneInfo("America/New_York")
except Exception:  # tz database unavailable; fall back to UTC dates
    _NYC_TZ = None


def _local_date(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=_NYC_TZ or timezone.utc)
    return dt.strftime("%Y-%m-%d")


def _load_trips() -> dict[str, Any] | None:
    if not config.CITIBIKE_TRIPS_PATH.exists():
        return None
    try:
        with config.CITIBIKE_TRIPS_PATH.open() as f:
            payload = json.load(f)
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("trips"):
        return None
    if payload.get("format") != 2:
        print("  Citi Bike cache predates the dock layer; re-run ingest.citibike")
        return None
    return payload


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _flow_extremes(
    out_n: dict[str, int], in_n: dict[str, int], names: list[str]
) -> list[dict[str, Any]]:
    """Rank the most one-way docks at each end of the range, departures first.

    Both ends, so the list reads as a balance rather than a leaderboard. With
    few enough docks the two slices overlap, and listing one twice would read
    as two different docks that happen to share a name.
    """
    by_net = sorted(names, key=lambda n: (-(out_n.get(n, 0) - in_n.get(n, 0)), n))
    head = by_net[:FLOW_TOP]
    tail = [n for n in by_net[-FLOW_BOTTOM:] if n not in head]
    return [{"name": n, "out": out_n.get(n, 0), "in": in_n.get(n, 0)} for n in head + tail]


def _citibike_summary(
    ride_stats: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Aggregate the Citi Bike trip cache into the export's stats block."""
    payload = _load_trips()
    if payload is None:
        return None

    raw = payload["trips"]
    coords: dict[str, list[float] | None] = payload.get("docks", {})

    out_n: dict[str, int] = {}
    in_n: dict[str, int] = {}
    for t in raw:
        out_n[t["a"]] = out_n.get(t["a"], 0) + 1
        in_n[t["b"]] = in_n.get(t["b"], 0) + 1
    # Busiest first, so the page can slice off the top without re-sorting and
    # a marker's index is stable for the whole payload.
    names = sorted(
        out_n.keys() | in_n.keys(), key=lambda n: (-(out_n.get(n, 0) + in_n.get(n, 0)), n)
    )
    index = {n: i for i, n in enumerate(names)}
    docks = [
        {"name": n, "at": coords.get(n), "out": out_n.get(n, 0), "in": in_n.get(n, 0)}
        for n in names
    ]

    # One row per trip, against a shared day list, so the page can filter the
    # docks with the same slider that filters the ride edges. Dates are the
    # only thing the two sources share, and comparing them as ISO strings
    # avoids inventing a joint index over two different sets of days.
    days = sorted({_local_date(t["t"]) for t in raw})
    day_index = {d: i for i, d in enumerate(days)}
    trips = [[index[t["a"]], index[t["b"]], day_index[_local_date(t["t"])]] for t in raw]

    own_days = {fname[:10] for fname in ride_stats}
    minutes = [t["dur"] / 60_000 for t in raw]
    bikes: dict[str, int] = {}
    for t in raw:
        if t["bike"]:
            bikes[t["bike"]] = bikes.get(t["bike"], 0) + 1

    return {
        # The page counts array length, the way it does for a feature's rides.
        "trips": trips,
        "docks": docks,
        "days": days,
        "hours": round(sum(minutes) / 60, 1),
        "from": days[0],
        "to": days[-1],
        # Citi Bike days that were also own-bike days: the number that says
        # this is a supplement to the GPS map, not a substitute for it.
        "same_day": len(set(days) & own_days),
        "median_min": round(_median(minutes), 1),
        "longest_min": round(max(minutes)),
        # A floor: a free ebike ride can carry no line item naming one.
        "ebike_min": sum(1 for t in raw if t["ebike"]),
        "paid": round(sum(t["paid"] for t in raw), 2),
        "charged": round(sum(t["gross"] for t in raw), 2),
        "credits": round(sum(t["credit"] for t in raw), 2),
        "aborted": sum(1 for t in raw if t["a"] == t["b"] and t["dur"] <= ABORT_MAX_MS),
        "bikes": len(bikes),
        "repeat_bikes": sum(1 for n in bikes.values() if n > 1),
        "once_only": sum(1 for d in docks if d["out"] + d["in"] == 1),
        "flow": _flow_extremes(out_n, in_n, names),
    }
