"""Citibike trip stats and dock layer for the interactive map.

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

# A GPS ride and a Citibike trip are the same journey when their recorded
# spans overlap. Requiring a minute of it, rather than an instant, keeps a
# ride that merely abuts a trip from claiming it. The split is insensitive to
# the exact figure -- on the real rides anything from 1s to 120s gives the
# same 225 Citibike / 64 own-bike answer, and a match covers a median 92% of
# the trip it hits, so these are whole journeys rather than clipped edges.
MATCH_MIN_OVERLAP_S = 60.0

# Fourth element of each row in the export's `rides` array. A positive value
# is the number of Citibike trips the ride overlaps, so it doubles as the
# count for a ride that spans several.
SOURCE_UNKNOWN = -1  # outside the span the Citibike export covers
SOURCE_OWN = 0  # inside it, and nothing overlaps: the owner's own bike

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
        print("  Citibike cache predates the dock layer; re-run ingest.citibike")
        return None
    return payload


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def ride_sources(ride_stats: dict[str, dict[str, Any] | None]) -> dict[str, int]:
    """Label each GPS ride by the Citibike trips it overlaps in time.

    The two datasets share nothing but the clock -- the matcher never saw a
    dock and the export never saw a fix -- so co-occurrence in time is the
    whole of the evidence, and it is strong: a Garmin activity running while
    a Citibike is unlocked is that Citibike ride.

    **Absence of a match only means something inside the export's window.**
    Outside it there is no evidence either way, so those rides are
    SOURCE_UNKNOWN rather than "own bike" -- with a truncated export that is
    most of the history, and it corrects itself when a fuller one lands.
    """
    payload = _load_trips()
    if payload is None:
        return {}
    spans = sorted((t["t"] / 1000, (t["t"] + t["dur"]) / 1000) for t in payload["trips"])
    if not spans:
        return {}
    win_start = spans[0][0]
    win_end = max(end for _, end in spans)

    sources: dict[str, int] = {}
    for fname, rs in ride_stats.items():
        if not rs or not rs.get("start") or rs.get("duration_s") is None:
            continue
        try:
            start = datetime.fromisoformat(rs["start"]).timestamp()
        except ValueError:
            continue
        if not win_start <= start <= win_end:
            sources[fname] = SOURCE_UNKNOWN
            continue
        end = start + rs["duration_s"]
        sources[fname] = sum(
            1 for s, e in spans if min(end, e) - max(start, s) >= MATCH_MIN_OVERLAP_S
        )
    return sources


def _citibike_summary(
    ride_stats: dict[str, dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Aggregate the Citibike trip cache into the export's stats block.

    The block is a Citibike column and an own-bike one, side by side. The
    Citibike figures come from the export -- every trip, including the ones
    no GPS ride was recorded over -- while the own-bike figures come from the
    rides `ride_sources` found no Citibike trip under. Both are complete
    records of their own kind.
    """
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

    # The own-bike column: the GPS rides no Citibike trip overlaps.
    sources = ride_sources(ride_stats)
    own_minutes: list[float] = []
    own_days: set[str] = set()
    for fname, rs in ride_stats.items():
        if sources.get(fname) != SOURCE_OWN or not rs or rs.get("duration_s") is None:
            continue
        own_minutes.append(rs["duration_s"] / 60)
        own_days.add(fname[:10])
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
        "median_min": round(_median(minutes), 1),
        # The column beside it. Rides rather than trips -- one recorded ride
        # can span several Citibike trips, and nothing splits an own-bike one.
        "own": {
            "rides": len(own_minutes),
            "hours": round(sum(own_minutes) / 60, 1),
            "days": len(own_days),
            "median_min": round(_median(own_minutes), 1) if own_minutes else None,
        },
        # A floor, shown as a percentage of trips: an ebike ride is only
        # visible when it carries a line item naming one, and a free one may
        # not. `rideableName` gives no help -- across five years 26 ebike
        # line items sit on plain-numbered bikes, and the hyphenated share
        # climbs 41% -> 90% by year, so the id format is fleet generation.
        "ebike_min": sum(1 for t in raw if t["ebike"]),
        "bikes": len(bikes),
        "repeat_bikes": sum(1 for n in bikes.values() if n > 1),
    }
