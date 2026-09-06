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

The one route a dock row can put on the map is a recorded one: a trip whose
clock a GPS ride runs over cites that ride, and the page draws the ride
itself. The dock-to-dock line stays straight underneath it -- what is drawn
is a trace that exists, never a path chosen for the gap between two docks.

Speed is deliberately absent. Every duration in the export is a whole number
of minutes and the end time is the start plus that duration, so at a median
8-minute trip the quantisation is +/-6%. Alongside GPS-measured edge speeds
it would read as the same kind of number and is not.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any, NamedTuple

import numpy as np

from . import config
from .gps import haversine_m

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

# Fourth element of each row in the export's `citibike.trips` array: the same
# match read from the trip's side, as an index into the export's `rides`. The
# trip is still not a trace -- this only names the recording that was running
# beside it, so the page can offer it.
TRIP_UNTRACED = -1

# A bike found at the dock its own last trip left it at, this recently, was
# parked rather than met. 48h rather than "same calendar day" so an overnight
# park is not split by midnight -- though on the real trips the two agree, and
# so does everything from 2 hours to 30 days.
RESUME_MAX_GAP_S = 48 * 3600

DAY_MS = 86_400_000

# The panel's two chance tests shuffle, so they are pinned to a seed: an export
# is a build artefact and must not move when nothing about the rides did.
# 5,000 trials place the marker to a fifth of a percent, which is finer than
# the 200px track it is drawn on. Below CHANCE_MIN_MEETINGS there are too few
# meetings for a median to mean anything and the block ships as None -- the
# page draws nothing rather than half a chart, the way it treats weather.
CHANCE_TRIALS = 5_000
CHANCE_SEED = 0
CHANCE_MIN_MEETINGS = 20

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


class _Meeting(NamedTuple):
    """A bike met again, beside the bikes that could have been met instead.

    ``pool_*`` is every *other* bike ridden by that moment, aged and placed as
    of that same unlock. It is the reference set the chance tests draw from:
    the question is never "is 306 days a long time" but "is it longer than the
    bikes I could equally have unlocked right here, right now".
    """

    age_d: float
    km: float | None
    pool_ages: list[float]
    pool_kms: list[float]


def _reencounters(
    raw: list[dict[str, Any]], coords: dict[str, list[float] | None]
) -> tuple[int, list[_Meeting]]:
    """Split repeated bike ids into round trips and bikes actually met again.

    2,225 distinct bikes over 2,518 trips leaves 293 repeats, and the panel
    used to report them as one number. 200 of them are the bike you parked:
    the previous trip on that id ended at the dock this one starts from, and
    nothing was met. Only the other 93 are a bike that moved on and came back
    -- a median 306 days later and 3.2 km from where it was left, at the rate
    a uniform draw from the classic fleet predicts
    ([findings](../findings/bike-reencounters.md), reproduced by
    ``tools/bike_reencounters.py``).

    Returns (round trips, meetings). RESUME_MAX_GAP_S is not a tuned
    threshold: every window from 2 hours to 30 days gives 190-202 round trips.
    """
    last: dict[str, tuple[float, str]] = {}  # bike -> (end ms, dock left at)
    seen: set[str] = set()
    resumes = 0
    met: list[_Meeting] = []
    for t in sorted((t for t in raw if t["bike"]), key=lambda t: t["t"]):
        # Whitespace-collapsed because the cache stores Lyft's raw spelling and
        # Lyft writes the busiest dock both with and without a tab (issue #26);
        # left and unlocked under different spellings, one park would read as a
        # meeting.
        here = " ".join(t["a"].split())
        prev = last.get(t["bike"])
        if prev is not None and prev[1] == here and t["t"] - prev[0] <= RESUME_MAX_GAP_S * 1000:
            resumes += 1
        else:
            if prev is not None and t["bike"] in seen:
                met.append(_meeting(t["bike"], t["t"], prev, last, coords, coords.get(here)))
            seen.add(t["bike"])
        last[t["bike"]] = (t["t"] + t["dur"], " ".join(t["b"].split()))
    return resumes, met


def _meeting(
    bike: str,
    now: float,
    prev: tuple[float, str],
    last: dict[str, tuple[float, str]],
    coords: dict[str, list[float] | None],
    here: list[float] | None,
) -> _Meeting:
    """Measure one meeting, and every bike that could have been unlocked instead."""
    others = [state for other, state in last.items() if other != bike]
    ages = [(now - end) / DAY_MS for end, _ in others]
    kms: list[float] = []
    if here is not None:
        left = [c for c in (coords.get(dock) for _, dock in others) if c]
        if left:
            lons = np.array([c[0] for c in left])
            lats = np.array([c[1] for c in left])
            # float(), not the numpy scalar: these reach json.dumps by way of
            # the medians below, and np.float64 is not serialisable.
            kms = [float(x) for x in haversine_m(lats, lons, here[1], here[0]) / 1000]
    mine = coords.get(prev[1])
    return _Meeting(
        age_d=(now - prev[0]) / DAY_MS,
        km=None
        if here is None or not mine
        else float(haversine_m(mine[1], mine[0], here[1], here[0]) / 1000),
        pool_ages=ages,
        pool_kms=kms,
    )


def _chance_test(
    observed: list[float], pools: list[list[float]], rng: random.Random
) -> dict[str, float] | None:
    """Measure a median against the medians chance would have produced.

    Hold the meetings fixed and swap in, for each one, a bike that could have
    been unlocked in its place. Repeat, and the medians of those shuffles are
    what "no pattern at all" looks like.

    Everything ships in the measurement's own units -- ``lo``/``hi`` are the
    middle 95% of the shuffles, ``chance`` their median -- because the panel
    draws ``obs`` against that band on a real axis. An earlier version shipped
    only ``pct``, the rank among shuffles, and drew *that*: on a percentile
    axis the band is 95% of the track by construction, so every answer but an
    extreme one looked the same. ``pct`` stays for the tooltip, where a rank
    is the right thing to say.

    No fleet size enters, and no threshold: the pools are the export's own
    history. That is the whole reason the panel can show this at all -- the
    counting question needs an N nobody here has
    (findings/bike-reencounters.md).
    """
    if len(observed) < CHANCE_MIN_MEETINGS:
        return None
    obs = _median(observed)
    shuffled = sorted(_median([rng.choice(p) for p in pools]) for _ in range(CHANCE_TRIALS))
    beaten = sum(1 for m in shuffled if m <= obs)
    edge = round(0.025 * CHANCE_TRIALS)
    return {
        # Rounded because this is an export, not a working figure: the page
        # shows one decimal at most, in miles or whole days.
        "obs": round(obs, 3),
        "chance": round(_median(shuffled), 3),
        "lo": round(shuffled[edge], 3),
        "hi": round(shuffled[-edge - 1], 3),
        "pct": round(100 * beaten / CHANCE_TRIALS, 1),
        "n": len(observed),
    }


def _chance_tests(meetings: list[_Meeting]) -> dict[str, dict[str, float]] | None:
    """Measure the two questions a meeting can answer with no model of the system.

    Space says nothing (the marker lands mid-chance) and time says something
    (it lands outside): a bike carries a lifetime, not a neighbourhood. Both
    are drawn, because one of them being flat is what makes the other mean
    anything.
    """
    rng = random.Random(CHANCE_SEED)  # noqa: S311 -- a shuffle test, not a secret
    placed = [(m.km, m.pool_kms) for m in meetings if m.km is not None and m.pool_kms]
    aged = [(m.age_d, m.pool_ages) for m in meetings if m.pool_ages]
    where = _chance_test([k for k, _ in placed], [p for _, p in placed], rng)
    when = _chance_test([a for a, _ in aged], [p for _, p in aged], rng)
    if where is None or when is None:
        return None
    return {"where": where, "when": when}


def _trip_spans(payload: dict[str, Any]) -> list[tuple[float, float]]:
    """(start, end) of every trip in seconds, in the cache's own order."""
    return [(t["t"] / 1000, (t["t"] + t["dur"]) / 1000) for t in payload["trips"]]


def _ride_windows(
    ride_stats: dict[str, dict[str, Any] | None],
) -> list[tuple[str, float, float]]:
    """(filename, start, end) in seconds for every ride that can be placed in time.

    A ride with no start or no duration is not "own bike" -- it is a ride the
    clock cannot speak for at all, so it is left out of both directions of
    the match rather than answered wrongly.
    """
    out = []
    for fname, rs in ride_stats.items():
        if not rs or not rs.get("start") or rs.get("duration_s") is None:
            continue
        try:
            start = datetime.fromisoformat(rs["start"]).timestamp()
        except ValueError:
            continue
        out.append((fname, start, start + rs["duration_s"]))
    return out


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
    spans = _trip_spans(payload)
    if not spans:
        return {}
    win_start = min(start for start, _ in spans)
    win_end = max(end for _, end in spans)

    sources: dict[str, int] = {}
    for fname, start, end in _ride_windows(ride_stats):
        if not win_start <= start <= win_end:
            sources[fname] = SOURCE_UNKNOWN
            continue
        sources[fname] = sum(
            1 for s, e in spans if min(end, e) - max(start, s) >= MATCH_MIN_OVERLAP_S
        )
    return sources


def trip_rides(ride_stats: dict[str, dict[str, Any] | None]) -> list[str | None]:
    """Name the GPS recording running over each trip, in cache order.

    The same clock overlap `ride_sources` uses, read from the trip's side:
    that function keeps only how many trips a ride hit, and which trip it was
    is thrown away. The page wants the other direction -- a dock popup's row
    is a pair of docks, and what a reader can be shown for it is the
    recording that was running at the time.

    Where two recordings overlap one trip (an activity stopped and restarted
    mid-trip) the longer overlap wins, with the filename breaking a tie, so
    the export does not depend on dict order.
    """
    payload = _load_trips()
    if payload is None:
        return []
    spans = _trip_spans(payload)
    best: list[tuple[float, str] | None] = [None] * len(spans)
    for fname, start, end in _ride_windows(ride_stats):
        for i, (s, e) in enumerate(spans):
            overlap = min(end, e) - max(start, s)
            if overlap < MATCH_MIN_OVERLAP_S:
                continue
            held = best[i]
            if held is None or (-overlap, fname) < (-held[0], held[1]):
                best[i] = (overlap, fname)
    return [None if held is None else held[1] for held in best]


def _citibike_summary(
    ride_stats: dict[str, dict[str, Any] | None],
    ride_index: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    """Aggregate the Citibike trip cache into the export's stats block.

    The block is a Citibike column and an own-bike one, side by side. The
    Citibike figures come from the export -- every trip, including the ones
    no GPS ride was recorded over -- while the own-bike figures come from the
    rides `ride_sources` found no Citibike trip under. Both are complete
    records of their own kind.

    `ride_index` maps a ride filename to its position in the export's `rides`
    array, which is how a trip cites the recording that was running over it.
    Without it every trip ships untraced, which is what a caller with no ride
    index is entitled to say.
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
    # ... and, fourth, the GPS recording that was running over the trip, so a
    # dock popup can offer it. An index into the same `rides` array a feature
    # cites, TRIP_UNTRACED where no recording covers the trip.
    traced = trip_rides(ride_stats) if ride_index else [None] * len(raw)

    def cite(fname: str | None) -> int:
        if fname is None or ride_index is None:
            return TRIP_UNTRACED
        return ride_index.get(fname, TRIP_UNTRACED)

    trips = [
        [index[t["a"]], index[t["b"]], day_index[_local_date(t["t"])], cite(fname)]
        for t, fname in zip(raw, traced, strict=True)
    ]

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
    resumes, meetings = _reencounters(raw, coords)

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
        # The panel draws `reencounters` alone. `resumes` rides along undrawn,
        # because it is the number the old "bikes ridden more than once" line
        # was mostly made of -- shipping it keeps the correction checkable.
        "reencounters": len(meetings),
        "resumes": resumes,
        # Where those meetings sit against chance, on the two questions the
        # data can answer on its own. None when there are too few to median.
        "again": _chance_tests(meetings),
    }
