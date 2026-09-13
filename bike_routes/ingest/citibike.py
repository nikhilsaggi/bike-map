"""Normalise a Citibike (Lyft) account export into cache/citibike_trips.json.

Lyft's data download is station-to-station: each record carries the dock
names, a start time, a whole-minute duration and the fare line items, and no
GPS whatsoever. So this does not write to ``rides/`` and nothing here reaches
the matcher -- it produces a cache file that ``bike_routes.citibike``
summarises at export time, the way ``weather.py`` summarises Open-Meteo.

Dock names are resolved to coordinates against Citibike's public GBFS feed,
whose ``name`` field is the same string the export uses, so the map can place
a marker per dock. A name the feed no longer lists (a renamed or removed dock)
keeps its trips and its counts and simply gets no coordinate: it drops out of
the drawn layer, not out of the numbers.

The export comes from a JavaScript payload pasted into the browser console on
account.citibikenyc.com (see fhoffa/code_snippets' baywheels readme), so this
runs by hand rather than from update.py. Once the cache exists every
``python -m bike_routes`` picks it up without a flag.

**The trips cache is merged, not replaced.** The console script that produces
the export stops at a one-year cutoff, so the ordinary download is a window
rather than a history; overwriting the cache with one would discard every
year before it without a word. Each ingest folds its file into what is
already cached, keyed by Lyft's own ``rideId``, and reports how much of it
was new. That makes a default one-year pull a safe top-up.

**The fetch can start from the cache.** ``--script`` prints the console
script with the newest cached start time baked in, so it pages back only as
far as the cache already reaches instead of across a year. Most of a pull's
time is the per-ride detail request, one a second, so a top-up that re-reads
a year of known trips spends minutes restating them. The cutoff is inclusive:
the newest cached trip is fetched again, which is what lets the ingest's
``already known`` count prove the two meet. With no cache there is no cutoff
at all, and the script walks the whole history.

Usage:
    python -m bike_routes.ingest.citibike --script > citibike_fetch.js
    python -m bike_routes.ingest.citibike ~/citibikenyc_history_2026-09-04.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bike_routes import config

GBFS_URL = "https://gbfs.citibikenyc.com/gbfs/en/station_information.json"

# Bump when a reader would half-read the file -- when a key it needs changes
# meaning or goes away -- so citibike.py can reject a stale cache instead.
# Adding a key no reader requires (``id``, below) is not that: a format-2
# cache stays readable, and merges forward into a format-2 file.
TRIPS_FORMAT = 2

# Every key a station entry must carry for the summary to use it. Fails
# closed the same way weather's cache guard does.
STATION_KEYS = ("lat", "lon")

# The console script ``--script`` prints, run on
# account.citibikenyc.com/ride-history while logged in. It is the fhoffa
# baywheels script with its one-year cutoff replaced by ``__SINCE_MS__``:
# the list pages back from now until a page reaches that start time, and
# only rides at or after it are sent for details. Rides are deduped on
# ``rideId`` before the detail phase, because the cursor is inclusive and
# every page restates the last ride of the one before it.
FETCH_SCRIPT = """\
const SINCE_MS = __SINCE_MS__;  // newest cached start; 0 walks the whole history
const DELAY_MS = 1000;

const QUERY_LIST = `query GetCurrentUserRides($startTimeMs: String, $memberId: String) {
  member(id: $memberId) {
    id
    rideHistory(startTimeMs: $startTimeMs) {
      limit
      hasMore
      rideHistoryList {
        rideId
        startTimeMs
        endTimeMs
        price { formatted }
        duration
        rideableName
      }
    }
  }
}`;

const QUERY_DETAILS = `query GetCurrentUserRideDetails($rideId: String!) {
  me {
    rideDetails(rideId: $rideId) {
      rideId
      startAddressStr
      endAddressStr
      paymentBreakdownMap {
        lineItems {
          title
          amount { formatted }
        }
      }
    }
  }
}`;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchGQL(payload) {
  const response = await fetch(window.location.origin + "/bikesharefe-gql", {
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    method: "POST",
  });
  return await response.json();
}

async function fetchSince() {
  const since = SINCE_MS ? new Date(SINCE_MS).toLocaleString() : "the beginning";
  console.log(`Fetching rides back to ${since}`);

  const byId = new Map();
  let cursor = String(Date.now());
  for (;;) {
    const res = await fetchGQL({
      operationName: "GetCurrentUserRides",
      query: QUERY_LIST,
      variables: { startTimeMs: cursor },
    });
    if (res.errors || !res.data || !res.data.member) {
      console.error("Stopped: unexpected response, nothing downloaded", res);
      return;
    }
    const history = res.data.member.rideHistory;
    const rides = history.rideHistoryList || [];
    for (const ride of rides) {
      if (Number(ride.startTimeMs) >= SINCE_MS) byId.set(ride.rideId, ride);
    }
    console.log(`Page ending ${new Date(Number(cursor)).toLocaleString()}: ${byId.size} rides so far`);
    if (rides.length === 0) break;
    const last = rides[rides.length - 1].startTimeMs;
    if (Number(last) <= SINCE_MS || !history.hasMore || last === cursor) break;
    cursor = last;
    await sleep(DELAY_MS);
  }

  const list = [...byId.values()];
  console.log(`${list.length} rides to detail`);
  const detailed = [];
  for (let i = 0; i < list.length; i++) {
    const ride = list[i];
    if (i % 5 === 0) console.log(`Details ${i} of ${list.length}`);
    try {
      const res = await fetchGQL({
        operationName: "GetCurrentUserRideDetails",
        query: QUERY_DETAILS,
        variables: { rideId: ride.rideId },
      });
      const d = res.data && res.data.me && res.data.me.rideDetails;
      detailed.push(d ? {
        ...ride,
        startAddress: d.startAddressStr,
        endAddress: d.endAddressStr,
        lineItems: (d.paymentBreakdownMap && d.paymentBreakdownMap.lineItems) || [],
      } : ride);
    } catch (e) {
      console.warn(`No details for ${ride.rideId}`, e);
      detailed.push(ride);
    }
    await sleep(DELAY_MS);
  }

  const system = window.location.hostname.split(".")[1] || "lyft";
  const blob = new Blob([JSON.stringify(detailed, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${system}_history_${new Date().toISOString().split("T")[0]}.json`;
  a.click();
  console.log(`Downloaded ${detailed.length} rides`);
}

fetchSince();
"""


def _norm_name(name: str) -> str:
    r"""Normalise a dock name enough to survive Lyft's own typing.

    Matching is still exact -- this only removes formatting the two feeds
    disagree about, and every rule below was checked against the whole GBFS
    list before being added. Across all 2,506 stations no two normalise to
    the same key, so nothing can be matched to the wrong dock.

    Over five years of history it recovers five docks: three where Lyft wrote
    a tab or a double space around the ampersand ("Broadway\t& W 48 St",
    "W 48 St &  Rockefeller Plaza") and two abbreviated "Av" for "Ave"
    ("Madison Av & E 51 St"). The 39 names that remain unmatched after this
    are docks that have genuinely been removed, which is what a five-year
    window should look like.
    """
    return re.sub(r"\bAv\b", "Ave", re.sub(r"\s+", " ", name).strip())


def _fetch_stations() -> dict[str, tuple[float, float]]:
    with urllib.request.urlopen(GBFS_URL, timeout=30) as resp:
        payload = json.load(resp)
    out: dict[str, tuple[float, float]] = {}
    for s in payload["data"]["stations"]:
        name, lat, lon = s.get("name"), s.get("lat"), s.get("lon")
        if name and lat is not None and lon is not None:
            out[name] = (lat, lon)
    return out


def _cache_well_formed(cached: object) -> bool:
    """Fail closed: a cache whose entries lack a coordinate is unusable."""
    if not isinstance(cached, dict) or not cached:
        return False
    return all(isinstance(v, dict) and all(k in v for k in STATION_KEYS) for v in cached.values())


def _load_cached_stations() -> dict[str, tuple[float, float]] | None:
    if not config.CITIBIKE_STATIONS_PATH.exists():
        return None
    try:
        with config.CITIBIKE_STATIONS_PATH.open() as f:
            cached = json.load(f)
    except Exception:
        return None
    if not _cache_well_formed(cached):
        print("  Cached station list has an unexpected shape; ignoring it")
        return None
    return {name: (v["lat"], v["lon"]) for name, v in cached.items()}


def _save_station_cache(stations: dict[str, tuple[float, float]]) -> None:
    config.CITIBIKE_STATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: {"lat": lat, "lon": lon} for name, (lat, lon) in stations.items()}
    with config.CITIBIKE_STATIONS_PATH.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))


def _get_stations() -> dict[str, tuple[float, float]] | None:
    try:
        stations = _fetch_stations()
        _save_station_cache(stations)
    except Exception as exc:
        print(f"  GBFS unavailable ({exc}); trying cache...")
        cached = _load_cached_stations()
        if cached:
            print(f"  Using cached station list ({len(cached)} stations)")
        else:
            print("  No station cache available; cannot resolve dock coordinates")
        return cached
    else:
        print(f"  Fetched {len(stations)} stations from GBFS")
        return stations


def _money(formatted: object) -> float:
    """Parse a Lyft money string ("$1.62", "-$1.28", "$0.00") to a float."""
    if not isinstance(formatted, str):
        return 0.0
    text = formatted.strip()
    sign = -1.0 if text.startswith("-") else 1.0
    digits = text.lstrip("-").lstrip("$").replace(",", "")
    try:
        return sign * float(digits)
    except ValueError:
        return 0.0


def _is_ebike(record: dict[str, Any]) -> bool:
    """Report whether a line item names an ebike.

    A floor, not a count: a free ebike ride can carry no such line item, so
    callers must report this as "at least", never as the total.
    """
    return any(
        "ebike" in str(li.get("title", "")).lower().replace("e-bike", "ebike")
        for li in record.get("lineItems", [])
    )


def _normalise(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Dedupe by rideId and flatten each record. Returns (trips, duplicates)."""
    seen: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for r in records:
        ride_id = r.get("rideId")
        if not ride_id:
            continue
        if ride_id in seen:
            duplicates += 1
            continue
        seen[ride_id] = r

    trips = []
    for ride_id, r in sorted(seen.items(), key=lambda kv: int(kv[1]["startTimeMs"])):
        amounts = [_money(li.get("amount", {}).get("formatted")) for li in r.get("lineItems", [])]
        trips.append(
            {
                # Lyft's own identity for the ride, kept so a later export can
                # be folded into this cache instead of replacing it.
                "id": ride_id,
                "t": int(r["startTimeMs"]),
                "dur": int(r["duration"]),
                "a": r["startAddress"],
                "b": r["endAddress"],
                "bike": r.get("rideableName", ""),
                "paid": round(_money(r.get("price", {}).get("formatted")), 2),
                "gross": round(sum(a for a in amounts if a > 0), 2),
                "credit": round(-sum(a for a in amounts if a < 0), 2),
                "ebike": _is_ebike(r),
            }
        )
    return trips, duplicates


class CacheUnreadableError(RuntimeError):
    """The trips cache exists but cannot be folded into.

    Raised rather than starting from empty: a cache that reads as nothing is
    a cache about to be overwritten by a one-year window, which is the exact
    loss this merge exists to prevent.
    """


def _trip_key(trip: dict[str, Any]) -> str:
    """Identity of a cached trip: Lyft's rideId, or a stand-in for a pre-id one.

    Caches written before the merge existed carry no ``id``. Their start time
    in milliseconds stands in: one account does not begin two rides in the
    same millisecond, and ``_merge`` trusts the stand-in only where that
    holds for the record in hand.
    """
    return str(trip.get("id") or f"t{trip['t']}")


def _load_existing() -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Read the trips, docks and export names already cached. Fails closed and loud."""
    path = config.CITIBIKE_TRIPS_PATH
    if not path.exists():
        return [], {}, []
    try:
        with path.open() as f:
            payload = json.load(f)
        trips = payload["trips"]
        docks = payload.get("docks") or {}
        sources = payload.get("sources") or ([payload["source"]] if payload.get("source") else [])
        well_formed = (
            isinstance(trips, list)
            and isinstance(docks, dict)
            and all(isinstance(t, dict) and "t" in t and "dur" in t for t in trips)
        )
    except Exception as exc:
        msg = f"{path} cannot be read ({exc})"
        raise CacheUnreadableError(msg) from exc
    if not well_formed:
        msg = f"{path} does not hold the trip records this ingest writes"
        raise CacheUnreadableError(msg)
    return trips, docks, list(sources)


def _merge(
    existing: list[dict[str, Any]], new: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    """Fold a fresh export's trips into the cached ones. Returns (trips, added).

    A ride already cached is replaced by the fresh record -- same ride, newer
    reading of it -- and is not counted as added: what the count answers is
    whether this file carried anything the cache did not already have.
    """
    kept: dict[str, dict[str, Any]] = {_trip_key(t): t for t in existing}

    # A cache written before ids existed files the same ride under its start
    # time, and the merge has to recognise it there or it doubles every trip
    # the two files share. Only an id-less record is looked up this way, and
    # only when its start names exactly one of them: an ambiguous start adds
    # a duplicate, which is recoverable, rather than deleting a ride, which
    # is not.
    at_start: dict[int, list[str]] = {}
    for t in existing:
        if not t.get("id"):
            at_start.setdefault(t["t"], []).append(_trip_key(t))
    unique_start = {ms: keys[0] for ms, keys in at_start.items() if len(keys) == 1}

    added = 0
    for t in new:
        key = _trip_key(t)
        prior = None if key in kept else unique_start.pop(t["t"], None)
        if prior is not None:
            del kept[prior]
        elif key not in kept:
            added += 1
        kept[key] = t

    return sorted(kept.values(), key=lambda t: t["t"]), added


def _span(trips: list[dict[str, Any]]) -> str:
    """Describe the dates a trip list covers.

    A one-year export looks exactly like a complete history that happens to
    start a year ago, and the span is what tells them apart, so every run
    prints it. Dates are the machine's own -- this is a line for the person
    at the keyboard, not a value anything stores.
    """
    if not trips:
        return "no trips"
    fmt = "%Y-%m-%d"
    first = datetime.fromtimestamp(trips[0]["t"] / 1000, tz=timezone.utc).astimezone()
    last = datetime.fromtimestamp(trips[-1]["t"] / 1000, tz=timezone.utc).astimezone()
    return f"{first.strftime(fmt)} to {last.strftime(fmt)}"


def fetch_script() -> str:
    """Build the console fetch script, cut off at the newest trip already cached.

    Raises ``CacheUnreadableError`` rather than printing a from-scratch
    script for a cache that cannot be read: that script is correct, but the
    ingest would refuse its output anyway.
    """
    cached, _, _ = _load_existing()
    since = max((t["t"] for t in cached), default=0)
    return FETCH_SCRIPT.replace("__SINCE_MS__", str(since))


def ingest(export_path: Path, *, replace: bool = False) -> dict[str, Any]:
    """Read a Lyft export and fold it into the normalised trips cache.

    The cache is merged by default. ``replace`` discards whatever is already
    there, which is only ever right when the cached records are themselves
    wrong -- the ordinary export covers one year, and overwriting with it
    loses every year before that.
    """
    with export_path.open() as f:
        records = json.load(f)
    if not isinstance(records, list):
        msg = f"{export_path} is not a list of ride records"
        raise TypeError(msg)

    fresh, duplicates = _normalise(records)
    print(f"  {len(fresh)} trips in the export ({duplicates} duplicate records dropped)")
    print(f"    spanning {_span(fresh)}")

    try:
        cached, prev_docks, sources = _load_existing()
    except CacheUnreadableError:
        # --replace is the way out of a cache that cannot be read, so it must
        # not be the one thing a broken cache blocks.
        if not replace:
            raise
        cached, prev_docks, sources = [], {}, []
    if replace and cached:
        print(f"  --replace: discarding the {len(cached)} trips already cached ({_span(cached)})")
        cached, sources = [], []
    trips, added = _merge(cached, fresh)
    if cached:
        print(
            f"  Merged into {len(cached)} cached trips: "
            f"{added} new, {len(fresh) - added} already known"
        )
        if fresh and added == len(fresh):
            print(
                "  WARNING: nothing in the export was already cached, so there may be a "
                "gap between the two. Re-fetch with a script from --script."
            )
    if export_path.name not in sources:
        sources = [*sources, export_path.name]

    used = sorted({name for t in trips for name in (t["a"], t["b"])})

    stations = _get_stations() or {}
    by_norm = {_norm_name(n): ll for n, ll in stations.items()}
    # Every dock the export names, whether or not GBFS could place it. A dock
    # with no coordinate keeps its counts and simply is not drawn -- dropping
    # it would quietly shrink totals to tidy the map. The export's own string
    # stays the identity; normalisation only finds the coordinates.
    docks: dict[str, list[float] | None] = {}
    for name in used:
        ll = by_norm.get(_norm_name(name))
        # A dock GBFS cannot place now keeps the coordinate the last run
        # found for it: docks do not move, and un-drawing a marker because a
        # feed dropped a name loses more than it tidies.
        docks[name] = [round(ll[1], 5), round(ll[0], 5)] if ll else prev_docks.get(name)
    placed = sum(1 for v in docks.values() if v)
    print(f"  {len(used)} distinct docks, {placed} placed by GBFS")
    for name, at in docks.items():
        if at is None:
            print(f"    no coordinates: {name}")

    payload = {
        "format": TRIPS_FORMAT,
        # The export this run read, and every export the cache was built from,
        # since no one file accounts for a merged history any more.
        "source": export_path.name,
        "sources": sources,
        "trips": trips,
        "docks": docks,
    }
    config.CITIBIKE_TRIPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.CITIBIKE_TRIPS_PATH.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
    return payload


def main() -> int:
    """Ingest the export named on the command line."""
    parser = argparse.ArgumentParser(
        description="Normalise a Citibike account export into the pipeline cache."
    )
    parser.add_argument("export", type=Path, nargs="?", help="citibikenyc_history_*.json from Lyft")
    parser.add_argument(
        "--script",
        action="store_true",
        help="print the browser console script that fetches only trips since the "
        "newest one cached, and exit",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="discard the cached trips instead of merging into them (destructive: "
        "the usual export covers only the last year)",
    )
    args = parser.parse_args()

    if args.script:
        try:
            script = fetch_script()
        except CacheUnreadableError as exc:
            print(f"Cannot read the cutoff: {exc}", file=sys.stderr)
            return 1
        # The script alone goes to stdout, so it can be redirected to a file.
        print(script, end="")
        return 0
    if args.export is None:
        parser.error("name an export to ingest, or pass --script")
    if not args.export.exists():
        print(f"No such file: {args.export}")
        return 1

    print(f"Reading {args.export}...")
    try:
        payload = ingest(args.export, replace=args.replace)
    except CacheUnreadableError as exc:
        print(f"Refusing to overwrite the trips cache: {exc}")
        print("Move it aside if you mean to start a fresh one.")
        return 1
    print(f"Done: wrote {config.CITIBIKE_TRIPS_PATH} ({len(payload['trips'])} trips)")
    print(f"      spanning {_span(payload['trips'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
