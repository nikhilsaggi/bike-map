"""Build the network from the origin-destination matrix alone.

Stations are the places rides begin and end; lines are chosen to carry the most
demand without a change. Street geometry is deliberately not consulted: a tunnel
does not care which avenue has a bike lane.

Writes od_network.json. Run after od.py and odpairs.py.
"""
import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO, work  # noqa: E402

MIN_SPACING_M = 480.0   # two trip-end clusters this close are one station
MIN_WEIGHT = 9          # a place needs this many trip ends to be worth a stop
WALK_M = 800.0          # a ride end further than this from a station is unreachable
MAX_TURN_DEG = 62.0     # a line may not kink harder than this at a station
MAX_DETOUR = 1.38       # ... nor wander this much further than its own straight run
MAX_OVERLAP = 0.55      # ... nor repeat this much of a line already laid down
MIN_STOPS, MAX_STOPS = 5, 15
DENSITY_MIN = 3.5       # a stop must earn this many rides per km of route it adds
LAT_M = 111320.0


def metres(p, q):
    """Distance in metres between two [lon, lat] points."""
    lat = (p[1] + q[1]) / 2
    return math.hypot((q[0] - p[0]) * LAT_M * math.cos(math.radians(lat)),
                      (q[1] - p[1]) * LAT_M)


def bearing(p, q):
    """Compass bearing from p to q, in degrees."""
    lat = (p[1] + q[1]) / 2
    return math.degrees(math.atan2((q[0] - p[0]) * math.cos(math.radians(lat)),
                                   q[1] - p[1])) % 360


def turn(a, b, c):
    """How hard the path a-b-c kinks at b, in degrees (0 = dead straight)."""
    d = abs(bearing(b, c) - bearing(a, b)) % 360
    return min(d, 360 - d)


def run_km(path, at):
    """Total route length of an ordered station list."""
    return sum(metres(at[path[i]], at[path[i + 1]]) for i in range(len(path) - 1)) / 1000


def shape_ok(path, at):
    """Reject a path that kinks too hard anywhere, or wanders too far off its span."""
    for i in range(1, len(path) - 1):
        if turn(at[path[i - 1]], at[path[i]], at[path[i + 1]]) > MAX_TURN_DEG:
            return False
    span = metres(at[path[0]], at[path[-1]]) / 1000
    return not (span > 0.2 and run_km(path, at) / span > MAX_DETOUR)


def pick_stations(clusters, docks):
    """Trip-end clusters by weight, merging any that crowd an earlier one."""
    out = []
    for c in sorted(clusters, key=lambda c: -c["n"]):
        near = next((s for s in out if metres(s["at"], c["at"]) < MIN_SPACING_M), None)
        if near:
            near["n"] += c["n"]
            near["absorbed"].append(c["id"])
            continue
        out.append({"at": list(c["at"]), "n": c["n"], "id": c["id"], "absorbed": []})
    out = [s for s in out if s["n"] >= MIN_WEIGHT]
    for s in out:
        best, bd = None, 1e18
        for d in docks:
            dd = metres(s["at"], d["at"])
            if dd < bd:
                bd, best = dd, d
        s["near"] = best["name"]
        s["near_m"] = round(bd)
    return out


def demand_matrix(stations, clusters, trips):
    """Fold every A-to-B ride onto its nearest station at each end."""
    home = {}
    for c in clusters:
        best, bd = None, 1e18
        for i, s in enumerate(stations):
            d = metres(c["at"], s["at"])
            if d < bd:
                bd, best = d, i
        home[c["id"]] = best if bd <= WALK_M else None
    dem = defaultdict(float)
    reachable = 0
    for _fn, ca, cb in trips:
        a, b = home.get(ca), home.get(cb)
        if a is None or b is None or a == b:
            continue
        reachable += 1
        dem[(min(a, b), max(a, b))] += 1
    return dict(dem), reachable, home


def best_insertion(path, at, dem, served, pool):
    """The station and slot that buys the most unserved demand per km added."""
    best = None
    base = run_km(path, at)
    for c in pool:
        if c in path:
            continue
        gain = sum(dem.get((min(c, o), max(c, o)), 0) for o in path
                   if (min(c, o), max(c, o)) not in served)
        if gain <= 0:
            continue
        for i in range(len(path) + 1):
            trial = path[:i] + [c] + path[i:]
            if not shape_ok(trial, at):
                continue
            density = gain / max(run_km(trial, at) - base, 0.2)
            if density < DENSITY_MIN:
                continue
            if best is None or density > best[0]:
                best = (density, trial)
    return best


def trim_terminals(path, at, dem, served):
    """Drop an end stop that does not earn the route length it adds.

    The seed pair is laid down before any density test, so a line can otherwise
    finish with a long tail to a place almost nobody travels to.
    """
    changed = True
    while changed and len(path) > MIN_STOPS:
        changed = False
        for end in (0, -1):
            c = path[end]
            rest = path[1:] if end == 0 else path[:-1]
            keep = sum(dem.get((min(c, o), max(c, o)), 0) for o in rest
                       if (min(c, o), max(c, o)) not in served)
            saved = run_km(path, at) - run_km(rest, at)
            if saved > 0.2 and keep / saved < DENSITY_MIN:
                path = rest
                changed = True
                break
    return path


def build_lines(stations, dem, max_lines):
    """Lay down lines one at a time, each taking the most demand still unserved."""
    at = [s["at"] for s in stations]
    pool = list(range(len(stations)))
    served, lines = set(), []
    while len(lines) < max_lines:
        seeds = sorted((kv for kv in dem.items() if kv[0] not in served),
                       key=lambda kv: -kv[1])[:20]
        if not seeds:
            break
        best_path, best_new = None, 0.0
        for (a, b), _v in seeds:
            path = [a, b]
            while len(path) < MAX_STOPS:
                ins = best_insertion(path, at, dem, served, pool)
                if ins is None:
                    break
                path = ins[1]
            path = trim_terminals(path, at, dem, served)
            if len(path) < MIN_STOPS:
                continue
            if any(len(set(path) & set(old)) / len(path) > MAX_OVERLAP for old in lines):
                continue
            new = sum(v for k, v in dem.items()
                      if k not in served and k[0] in path and k[1] in path)
            if new > best_new:
                best_path, best_new = path, new
        if best_path is None or best_new < 5:
            break
        for k in dem:
            if k[0] in best_path and k[1] in best_path:
                served.add(k)
        lines.append(best_path)
    return lines


def score(lines, dem, reachable, total):
    """Split demand into no-change, one-change and stranded."""
    on = defaultdict(set)
    for li, path in enumerate(lines):
        for s in path:
            on[s].add(li)
    sets = [set(p) for p in lines]
    direct = one = 0.0
    for (a, b), v in dem.items():
        la, lb = on.get(a, set()), on.get(b, set())
        if la & lb:
            direct += v
        elif any(sets[x] & sets[y] for x in la for y in lb):
            one += v
    on_net = sum(dem.values())
    return {"rides": total, "reachable": reachable, "on_network": on_net,
            "direct": direct, "one_change": one, "stranded": on_net - direct - one,
            "pct_direct": 100 * direct / total,
            "pct_within_one": 100 * (direct + one) / total}


def main():
    """Sweep the line count, keep the knee, and write the network."""
    clusters = json.load(open(work("od_clusters.json")))
    trips = json.load(open(work("od_pairs.json")))["trips"]
    docks = [d for d in json.load(gzip.open(GEO))["properties"]["citibike"]["docks"]
             if d.get("at")]

    st = pick_stations(clusters, docks)
    dem, reach, _home = demand_matrix(st, clusters, trips)
    at = [s["at"] for s in st]
    print("%d stations (>= %d trip ends, merged at %.0f m)"
          % (len(st), MIN_WEIGHT, MIN_SPACING_M))
    print("reachable A-to-B rides: %d of %d (%.0f%%), %.0f m walk at both ends"
          % (reach, len(trips), 100 * reach / len(trips), WALK_M))

    print("\n  lines  no change  <=1 change  stranded  route km")
    runs, prev = [], 0.0
    for k in (4, 5, 6, 7, 8):
        ln = build_lines(st, dem, k)
        sc = score(ln, dem, reach, len(trips))
        km = sum(run_km(p, at) for p in ln)
        print("  %5d  %8.0f%%  %9.0f%%  %8.0f  %8.1f   (+%.1f pts)"
              % (k, sc["pct_direct"], sc["pct_within_one"], sc["stranded"], km,
                 sc["pct_within_one"] - prev))
        runs.append((k, ln, sc, sc["pct_within_one"] - prev))
        prev = sc["pct_within_one"]

    chosen = runs[0]
    for r in runs:
        if r[3] >= 3.0:
            chosen = r
    k, lines, sc, _step = chosen

    print("\nchosen: %d stations, %d lines" % (len(st), k))
    print("   no change    %4.0f%%  (%d rides)" % (sc["pct_direct"], sc["direct"]))
    print("   <= 1 change  %4.0f%%  (%d rides)"
          % (sc["pct_within_one"], sc["direct"] + sc["one_change"]))
    print("   stranded %d ; unreachable %d" % (sc["stranded"], len(trips) - reach))

    for i, path in enumerate(lines):
        print("\nline %d  %d stops  %.1f km" % (i + 1, len(path), run_km(path, at)))
        for s in path:
            print("     %-34s %4d" % (st[s]["near"], st[s]["n"]))

    json.dump({"stations": st, "lines": lines, "score": sc,
               "demand": [[a, b, v] for (a, b), v in dem.items()]},
              open(work("od_network.json"), "w"), indent=1)
    print("\nwrote", work("od_network.json"))


if __name__ == "__main__":
    main()
