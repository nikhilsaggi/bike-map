"""Citi Bike trip stats for the interactive map sidebar.

Reads the cache ``ingest.citibike`` writes and aggregates it into the
``properties.citibike`` block. Pure aggregation: no network, no state, no
graph. Returns None when the cache is absent, which is every checkout but
the owner's.

These trips are dock-to-dock with no GPS trace, so nothing here may reach
``edge_counts``, ``edge_traversals`` or ``coverage`` -- those carry a
"measured" contract this data cannot honour. It is a stats block and only a
stats block: there is no Citi Bike map layer, because the only two things
that could be drawn are a dock's position (which says nothing on its own)
and a line between two docks (which is not a route). What the numbers can
say exactly is that a dock is one-way, and that is a fact about *trips* the
matched-edge map has no way to express -- an edge has a direction but no
origin.

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

# How many docks from each end of the net-flow range the section lists.
# Ranked here rather than in the browser, the way the speed corridors are:
# the page gets the rows it draws, not the whole 216-dock table.
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
    if payload.get("format") != 1:
        print("  Citi Bike cache has an unexpected format; skipping trip stats")
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

    trips = payload["trips"]

    out_n: dict[str, int] = {}
    in_n: dict[str, int] = {}
    for t in trips:
        out_n[t["a"]] = out_n.get(t["a"], 0) + 1
        in_n[t["b"]] = in_n.get(t["b"], 0) + 1
    names = sorted(set(out_n) | set(in_n))

    dates = {_local_date(t["t"]) for t in trips}
    own_days = {fname[:10] for fname in ride_stats}
    minutes = [t["dur"] / 60_000 for t in trips]
    bikes: dict[str, int] = {}
    for t in trips:
        if t["bike"]:
            bikes[t["bike"]] = bikes.get(t["bike"], 0) + 1

    return {
        "trips": len(trips),
        "hours": round(sum(minutes) / 60, 1),
        "days": len(dates),
        "from": min(dates),
        "to": max(dates),
        # Citi Bike days that were also own-bike days: the number that says
        # this is a supplement to the GPS map, not a substitute for it.
        "same_day": len(dates & own_days),
        "median_min": round(_median(minutes), 1),
        "longest_min": round(max(minutes)),
        # A floor: a free ebike ride can carry no line item naming one.
        "ebike_min": sum(1 for t in trips if t["ebike"]),
        "paid": round(sum(t["paid"] for t in trips), 2),
        "charged": round(sum(t["gross"] for t in trips), 2),
        "credits": round(sum(t["credit"] for t in trips), 2),
        "aborted": sum(1 for t in trips if t["a"] == t["b"] and t["dur"] <= ABORT_MAX_MS),
        "bikes": len(bikes),
        "repeat_bikes": sum(1 for n in bikes.values() if n > 1),
        "docks": len(names),
        "once_only": sum(1 for n in names if out_n.get(n, 0) + in_n.get(n, 0) == 1),
        "flow": _flow_extremes(out_n, in_n, names),
    }
