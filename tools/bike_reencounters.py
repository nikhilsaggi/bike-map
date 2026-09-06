"""Ask whether meeting the same Citibike twice means anything.

The stats panel used to report "253 of 2,225 bikes ridden more than once",
which sounds like a lot of coincidence for five years of riding.  This script
is the evidence for what that number actually is, and it takes three steps:

1. **Decompose it.**  Most of those repeats are not encounters at all.  A
   consecutive pair of trips on one bike where the earlier one *ended at the
   dock the later one starts from* is a round trip -- the bike is where it is
   because you parked it there.  Splitting those out is the whole ballgame,
   and the split is not sensitive to the window used (see `--resume-hours`).

2. **Invert the chance question rather than assuming a fleet size.**  "Is it
   more than chance?" needs an N nobody in this repo knows: the fleet grew
   from roughly 25,000 to 40,000 bikes across the export's five years.  So
   instead of testing against a guessed N, this reports the N the data
   implies -- total bike-draws divided by re-encounters -- and leaves the
   comparison against a published fleet to the reader.  Restricting both
   sides to bikes ridden recently matters, because a bike retired in 2023
   inflates the exposure without ever being available to meet.

3. **Test the structure, where no fleet size is needed at all.**  Two
   permutation tests hold the re-encounters fixed and ask what is special
   about the bike that was met: is it nearer to where it was left than a
   random bike you had ridden by then (space), and is it one you rode more
   recently (time)?  Those answer "do bikes stay in a neighborhood" and "do
   bikes leave the fleet" without any model of the system at all.

Reads cache/citibike_trips.json; writes nothing.  The findings are written up
in findings/bike-reencounters.md.

Usage:
    python tools/bike_reencounters.py [--resume-hours H] [--fleet N] [--trials N]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

# This script lives in tools/, so anchor inputs to the repo root rather than
# the working directory it happens to be launched from.
ROOT = Path(__file__).resolve().parent.parent
TRIPS_PATH = ROOT / "cache/citibike_trips.json"

# A trip whose bike was last left at this trip's own start dock, this recently,
# is a round trip rather than a meeting.  48h rather than "same calendar day"
# so an overnight park is not split by midnight; the count barely moves across
# the whole range (--resume-hours prints the sensitivity).
RESUME_HOURS = 48.0

# Bikes docked across the whole system in one GBFS station_status snapshot
# (2026-09-05), and how many of those were classic rather than electric.  A
# lower bound on the fleet -- bikes out on a trip are in neither number -- and
# a single day's reading of something that grew all through the export.  It is
# a yardstick to hold the implied pool against, never an input to it.
GBFS_FLEET = 34_239
GBFS_CLASSIC = 18_823

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
EARTH_R_KM = 6371.0
MIN_DOCK_DRAWS = 40  # below this a dock's re-encounter rate is one or two events


def _norm_name(name: str) -> str:
    r"""Collapse the whitespace Lyft's own dock names disagree about.

    The same rule as ``ingest.citibike._norm_name``, minus its "Av" -> "Ave"
    expansion, which no dock in the trips cache needs.  Copied rather than
    imported so this script needs nothing but the JSON: importing the ingest
    pulls in config, and config pulls in numpy and osmnx.

    It should not have to exist.  ``ingest.citibike`` normalises only when it
    asks GBFS for coordinates, and stores Lyft's raw spelling in the cache --
    so ``Broadway\t& W 48 St`` and ``Broadway & W 48 St`` are two keys, and
    the shipped dock layer draws the busiest dock as two markers stacked on
    one coordinate with its 770 touches split 633/137.
    """
    return re.sub(r"\s+", " ", name).strip()


def _load() -> tuple[list[dict[str, Any]], dict[str, tuple[float, float]]]:
    """Return the trips carrying a bike id, oldest first, and the dock coordinates.

    Dock names are normalised on the way in.  The cache stores Lyft's own
    spelling, and Lyft writes the busiest dock here two ways -- 633 touches as
    ``Broadway & W 48 St`` and 137 with a tab for the space.  Leaving them
    apart would break the one comparison this whole script rests on: a bike
    left at one spelling and unlocked from the other would read as a
    re-encounter rather than as the round trip it is.
    """
    if not TRIPS_PATH.exists():
        msg = f"{TRIPS_PATH} not found -- run python -m bike_routes.ingest.citibike first"
        raise SystemExit(msg)
    with TRIPS_PATH.open() as f:
        payload = json.load(f)
    trips = [t for t in payload.get("trips", []) if t.get("bike")]
    if not trips:
        msg = f"{TRIPS_PATH} carries no trip with a bike id"
        raise SystemExit(msg)
    for t in trips:
        t["a"] = _norm_name(t["a"])
        t["b"] = _norm_name(t["b"])
    trips.sort(key=lambda t: t["t"])
    docks = {_norm_name(n): (at[0], at[1]) for n, at in payload.get("docks", {}).items() if at}
    return trips, docks


def _date(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


@cache
def _haversine_km(a: tuple[float, float] | None, b: tuple[float, float] | None) -> float | None:
    """Great-circle km between two (lon, lat) docks, or None if either is unplaced.

    Memoised: the replay asks for the same few hundred dock pairs millions of
    times over.
    """
    if not a or not b:
        return None
    lon1, lat1 = a
    lon2, lat2 = b
    dlon = math.radians(lon2 - lon1)
    dlat = math.radians(lat2 - lat1)
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_R_KM * math.asin(math.sqrt(h))


class Draw(NamedTuple):
    """One trip, classified, with the state of the bikes already ridden at that moment.

    ``pool`` is every *other* bike ridden before this trip, each as (days since
    it was last ridden, km from where it was left to this dock).  It is the
    exposure a chance model needs and the reference set both permutation tests
    draw from, so it is captured as the replay passes rather than rebuilt
    after.  ``hit`` is that same pair for the bike actually unlocked, when it
    is one already ridden.

    A resume carries neither: unlocking the bike you parked is not a draw.
    """

    trip: dict[str, Any]
    resume: bool
    hit: tuple[float, float | None] | None
    pool: list[tuple[float, float | None]]

    @property
    def dock(self) -> str:
        """The dock this trip started from."""
        return str(self.trip["a"])


def _replay(
    trips: list[dict[str, Any]], docks: dict[str, tuple[float, float]], resume_hours: float
) -> list[Draw]:
    """Walk the trips in order, classifying each and recording what was met.

    A resume is not a fresh draw at all -- you are unlocking the bike you
    parked -- so it updates the bike's position without ever being offered a
    chance to collide.  Every other trip is a draw, and it hits when its bike
    is one already ridden.
    """
    last: dict[str, tuple[float, str]] = {}  # bike -> (last ride end ms, dock left at)
    out: list[Draw] = []
    for t in trips:
        prev = last.get(t["bike"])
        here = docks.get(t["a"])
        resume = (
            prev is not None and prev[1] == t["a"] and t["t"] - prev[0] <= resume_hours * HOUR_MS
        )
        hit = None
        pool: list[tuple[float, float | None]] = []
        if not resume:
            for bike, (end_ms, dock) in last.items():
                age = (t["t"] - end_ms) / DAY_MS
                km = _haversine_km(docks.get(dock), here)
                if bike == t["bike"]:
                    hit = (age, km)
                else:
                    pool.append((age, km))
        out.append(Draw(trip=t, resume=resume, hit=hit, pool=pool))
        last[t["bike"]] = (t["t"] + t["dur"], t["b"])
    return out


def _quantile(xs: list[float], f: float) -> float:
    xs = sorted(xs)
    i = f * (len(xs) - 1)
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _headline(trips: list[dict[str, Any]], draws: list[Draw]) -> None:
    """Print the panel's number, and what it is made of."""
    counts: dict[str, int] = {}
    for t in trips:
        counts[t["bike"]] = counts.get(t["bike"], 0) + 1
    repeats = sum(1 for n in counts.values() if n > 1)
    pairs = len(trips) - len(counts)
    resumes = sum(1 for d in draws if d.resume)
    met = sum(1 for d in draws if d.hit is not None)
    print("== the headline, decomposed ==")
    print(f"trips carrying a bike id       {len(trips):6}")
    print(f"distinct bikes                 {len(counts):6}")
    print(f"ridden more than once          {repeats:6}   <- what the panel says")
    print(f"repeat pairs (the events)      {pairs:6}")
    print(f"  round trips (resumes)        {resumes:6}   parked it, took it back")
    print(f"  re-encounters                {met:6}   the bike had moved on")
    print()


def _split(trips: list[dict[str, Any]], resume_hours: float) -> tuple[int, int]:
    """Count round trips and re-encounters alone, without the pools _replay carries."""
    last: dict[str, tuple[float, str]] = {}
    seen: set[str] = set()
    resumes = met = 0
    for t in trips:
        prev = last.get(t["bike"])
        if prev is not None and prev[1] == t["a"] and t["t"] - prev[0] <= resume_hours * HOUR_MS:
            resumes += 1
        else:
            met += t["bike"] in seen
            seen.add(t["bike"])
        last[t["bike"]] = (t["t"] + t["dur"], t["b"])
    return resumes, met


def _resume_sensitivity(trips: list[dict[str, Any]], resume_hours: float) -> None:
    """Show that the split is a fact about the data, not a choice of window."""
    print("== is the split an artefact of the 48h window? ==")
    print(" window   round trips   re-encounters")
    for h in (2, 6, 12, 24, 48, 72, 168, 720):
        res, met = _split(trips, float(h))
        mark = "  <-" if abs(h - resume_hours) < 1e-9 else ""
        print(f"{h:6}h   {res:11}   {met:13}{mark}")
    print()


def _shape(draws: list[Draw]) -> None:
    """Describe a re-encounter once the round trips are gone."""
    ages = [d.hit[0] for d in draws if d.hit is not None]
    kms = [d.hit[1] for d in draws if d.hit is not None and d.hit[1] is not None]
    print("== what a re-encounter looks like ==")
    print(
        f"days since last ridden      q25 {_quantile(ages, 0.25):6.0f}"
        f"  median {_quantile(ages, 0.5):6.0f}  q75 {_quantile(ages, 0.75):6.0f}"
    )
    print(
        f"km from where it was left   q25 {_quantile(kms, 0.25):6.2f}"
        f"  median {_quantile(kms, 0.5):6.2f}  q75 {_quantile(kms, 0.75):6.2f}   (n={len(kms)})"
    )
    same = sum(1 for d in draws if d.hit is not None and d.hit[1] is not None and d.hit[1] < 0.01)
    print(f"picked up at the very dock it was left at   {same}")
    print()


def _implied_pool(draws: list[Draw], fleet: int, classic: int) -> None:
    """How big a pool the re-encounter rate implies, given only the data.

    Total bike-draws over re-encounters.  Restricting both to bikes ridden
    within a window is what separates "the pool" from "every bike ever ridden",
    most of which the system has since retired.
    """
    print("== how big is the pool being drawn from? ==")
    print("bikes ridden within   exposure    met   implied pool   95% CI")
    for window in (60, 90, 180, 365, 730, None):
        exposure = sum(
            sum(1 for age, _ in d.pool if window is None or age <= window) for d in draws
        )
        met = sum(1 for d in draws if d.hit is not None and (window is None or d.hit[0] <= window))
        if not met:
            continue
        lo = met - 1.96 * math.sqrt(met)
        hi = met + 1.96 * math.sqrt(met)
        label = "any time" if window is None else f"{window}d"
        print(
            f"{label:>19}   {exposure:8}  {met:5}   {exposure / met:12,.0f}"
            f"   {exposure / hi:,.0f} - {exposure / lo:,.0f}"
        )
    print()
    print(
        f"against a GBFS snapshot: {fleet:,} bikes docked system-wide, {classic:,} of them classic"
    )
    print("(a lower bound -- bikes out on a trip are in neither -- and one day's reading")
    print(" of a fleet that grew from roughly 25,000 to 40,000 across the export)")
    print()
    print("Read the top rows, not the bottom one. Every bike ever ridden counts toward")
    print("'any time' whether or not the system still runs it, and the drift down the")
    print("column is that inflation arriving. The short windows are the estimate, and")
    print("they land on the classic fleet -- which is the pool, since 93% of these trips")
    print("carry no ebike line item.")
    print()


def _permutation(draws: list[Draw], trials: int, seed: int) -> None:
    """Hold the re-encounters fixed; ask what was special about the bike met.

    Each re-encounter contributes its own reference set -- the bikes ridden by
    then, aged and placed as of that same moment -- so the null is not "some
    other bike" but "some other bike that could equally have been unlocked right
    here, right now".  Nothing about fleet size enters.
    """
    rng = random.Random(seed)  # noqa: S311 -- a permutation test, not a secret
    print(f"== two permutation tests ({trials:,} draws each) ==")
    for name, index, unit, question in (
        ("space", 1, "km", "is it nearer to where it was left than any other bike ridden by then?"),
        ("time", 0, "days", "is it one ridden more recently than the others?"),
    ):
        obs = [d.hit[index] for d in draws if d.hit is not None and d.hit[index] is not None]
        pools = [
            [p[index] for p in d.pool if p[index] is not None]
            for d in draws
            if d.hit is not None and d.hit[index] is not None
        ]
        pools = [p for p in pools if p]
        median = statistics.median(obs)
        null = sorted(statistics.median([rng.choice(p) for p in pools]) for _ in range(trials))
        p = sum(1 for m in null if m <= median) / trials
        print(f"-- {name}: {question}")
        print(f"   observed median   {median:8.2f} {unit}")
        print(
            f"   null median       {null[trials // 2]:8.2f} {unit}"
            f"   (95% band {null[trials // 40]:.2f} - {null[trials - trials // 40]:.2f})"
        )
        print(f"   one-sided p       {p:8.3f}")
    print()


def _ebikes(draws: list[Draw], fleet: int, classic: int) -> None:
    """Weigh the ebike deficit before and after correcting for exposure.

    An ebike trip can only re-encounter an ebike already ridden, and there are
    few of those -- so the raw rate is guaranteed to look low whether or not
    ebikes behave differently.  The comparison that means anything is against
    that smaller exposure.
    """
    print("== does the ebike/classic split matter? ==")
    seen: set[str] = set()
    e_draws = e_hits = 0
    e_exposure = 0
    o_draws = o_hits = 0
    for d in draws:
        if d.resume:
            if d.trip["ebike"]:
                seen.add(d.trip["bike"])
            continue
        if d.trip["ebike"]:
            e_draws += 1
            e_exposure += len(seen)
            e_hits += 1 if d.trip["bike"] in seen else 0
            seen.add(d.trip["bike"])
        else:
            o_draws += 1
            o_hits += 1 if d.hit is not None else 0
    print(f"ebike-flagged draws {e_draws:5}   met {e_hits:3}   ({100 * e_hits / e_draws:.1f}%)")
    print(f"every other draw    {o_draws:5}   met {o_hits:3}   ({100 * o_hits / o_draws:.1f}%)")
    ebike_fleet = fleet - classic
    print(
        f"but an ebike draw is exposed to only {e_exposure / e_draws:.0f} prior ebikes on average,"
    )
    print(
        f"so against the {ebike_fleet:,} electric bikes in the snapshot it should meet"
        f" {e_exposure / ebike_fleet:.1f}"
    )
    print("(the flag is a floor: a free ebike ride carries no line item naming one)")
    print()


def _by_dock(draws: list[Draw]) -> None:
    """Show where the re-encounters happen, against the exposure each dock earned.

    A busy dock meets bikes twice simply by being used more, and a dock used
    late in the history draws against a longer list of bikes already ridden --
    so the raw count ranks docks by neither locality nor luck.  The expected
    column shares the 93 re-encounters out in proportion to each dock's
    exposure, which needs no fleet size: a ratio above 1 is a dock meeting old
    bikes more often than its own draws entitle it to.
    """
    per: dict[str, list[float]] = {}
    total_exposure = 0.0
    total_met = 0
    for d in draws:
        if d.resume:
            continue
        row = per.setdefault(d.dock, [0.0, 0.0, 0.0])
        row[0] += 1
        row[1] += len(d.pool)
        row[2] += 1 if d.hit is not None else 0
        total_exposure += len(d.pool)
        total_met += 1 if d.hit is not None else 0
    print(f"== where re-encounters happen (docks with >= {MIN_DOCK_DRAWS} draws) ==")
    print(f"{'dock':34} draws   met   expected   ratio")
    for dock, (n, exposure, met) in sorted(per.items(), key=lambda kv: -kv[1][0]):
        if n < MIN_DOCK_DRAWS:
            continue
        exp = total_met * exposure / total_exposure
        ratio = f"{met / exp:5.2f}" if exp else "    -"
        print(f"{dock[:34]:34} {n:5.0f}   {met:3.0f}   {exp:8.1f}   {ratio}")
    print()


def main() -> None:
    """Run the whole audit against the real trip cache."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--resume-hours",
        type=float,
        default=RESUME_HOURS,
        help="a bike found at the dock it was left at, this recently, is a round trip",
    )
    ap.add_argument("--fleet", type=int, default=GBFS_FLEET, help="bikes system-wide, for scale")
    ap.add_argument(
        "--classic", type=int, default=GBFS_CLASSIC, help="how many of those are classic"
    )
    ap.add_argument("--trials", type=int, default=20_000, help="permutation draws")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    trips, docks = _load()
    draws = _replay(trips, docks, args.resume_hours)
    span = f"{_date(trips[0]['t'])} .. {_date(trips[-1]['t'])}"
    print(f"{TRIPS_PATH.name}: {len(trips):,} trips with a bike id, {span}\n")
    _headline(trips, draws)
    _resume_sensitivity(trips, args.resume_hours)
    _shape(draws)
    _implied_pool(draws, args.fleet, args.classic)
    _permutation(draws, args.trials, args.seed)
    _ebikes(draws, args.fleet, args.classic)
    _by_dock(draws)


if __name__ == "__main__":
    main()
