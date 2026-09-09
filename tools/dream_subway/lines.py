"""Place stations on a hand-defined corridor from measured pass demand."""
import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO, work

LAT = 111320.0
d = json.load(gzip.open(GEO))
P = d["properties"]
names = P["street_names"]
docks = [x for x in P["citibike"]["docks"] if x.get("at")]
clusters = json.load(open(work("od_clusters.json")))


def m(lo1, la1, lo2, la2):
    la = (la1 + la2) / 2
    return math.hypot((lo2 - lo1) * 111320.0 * math.cos(math.radians(la)), (la2 - la1) * LAT)


# feature midpoints with pass count
F = []
for f in d["features"]:
    c = f["geometry"]["coordinates"]
    L = sum(m(*c[i], *c[i + 1]) for i in range(len(c) - 1))
    mid = c[len(c) // 2]
    sn = f["properties"].get("sn")
    F.append((mid[0], mid[1], len(f["properties"]["rides"]), L,
              names[sn] if sn is not None else ""))

cell = 0.004
grid = defaultdict(list)
for i, ft in enumerate(F):
    grid[(int(ft[0] / cell), int(ft[1] / cell))].append(i)


def near_feats(lo, la, r):
    gx, gy = int(lo / cell), int(la / cell)
    span = int(r / 300) + 1
    out = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            for i in grid.get((gx + dx, gy + dy), ()):
                if m(lo, la, F[i][0], F[i][1]) <= r:
                    out.append(i)
    return out


def nearest_dock(lo, la):
    best, bd = None, 1e18
    for x in docks:
        dd = m(lo, la, x["at"][0], x["at"][1])
        if dd < bd:
            bd, best = dd, x
    return best["name"], bd


def nearest_cluster(lo, la):
    best, bd = None, 1e18
    for c in clusters:
        dd = m(lo, la, c["at"][0], c["at"][1])
        if dd < bd:
            bd, best = dd, c
    return best, bd


def resample(way, step=100.0):
    """way: list of [lon,lat] -> densified points with cumulative distance."""
    pts = [(way[0][0], way[0][1], 0.0)]
    acc = 0.0
    for a, b in zip(way, way[1:]):
        L = m(a[0], a[1], b[0], b[1])
        k = max(1, int(L / step))
        for i in range(1, k + 1):
            t = i / k
            acc_i = acc + L * t
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, acc_i))
        acc += L
    return pts


def demand(lo, la, r=90.0):
    """Average passes on features within r metres, length-weighted."""
    idx = near_feats(lo, la, r)
    if not idx:
        return 0.0
    num = sum(F[i][2] * F[i][3] for i in idx)
    den = sum(F[i][3] for i in idx)
    return num / den if den else 0.0


def profile(way, step=100.0, r=90.0):
    return [(p[2], p[0], p[1], demand(p[0], p[1], r)) for p in resample(way, step)]


def place(way, min_gap=550.0, step=100.0, r=90.0, force=()):
    prof = profile(way, step, r)
    forced = []
    for fx, fy in force:
        best = min(prof, key=lambda p: m(fx, fy, p[1], p[2]))
        forced.append(best)
    picked = []
    for k, (s, lo, la, v) in enumerate(prof):
        lo_i = max(0, k - 3)
        hi_i = min(len(prof), k + 4)
        if v <= 0:
            continue
        if v < max(p[3] for p in prof[lo_i:hi_i]):
            continue
        picked.append((s, lo, la, v))
    out = list(forced)
    # anchors next: a top origin/destination within 300 m of the corridor is a stop
    for c in sorted(clusters, key=lambda c: -c["n"]):
        if c["n"] < 19:
            break
        best = min(prof, key=lambda p: m(c["at"][0], c["at"][1], p[1], p[2]))
        if m(c["at"][0], c["at"][1], best[1], best[2]) <= 300:
            if all(abs(best[0] - o[0]) >= min_gap * 0.5 for o in out):
                out.append(best)
    for s, lo, la, v in sorted(picked, key=lambda t: -t[3]):
        if all(abs(s - o[0]) >= min_gap for o in out):
            out.append((s, lo, la, v))
    out.sort()
    for end in (prof[0], prof[-1]):
        if all(abs(end[0] - o[0]) >= min_gap * 0.6 for o in out):
            out.append(end)
    out.sort()
    return out, prof


def report(label, way, **kw):
    st, prof = place(way, **kw)
    total = prof[-1][0]
    print("\n=== %s   %.2f km, %d stops" % (label, total / 1000, len(st)))
    prev = None
    for s, lo, la, v in st:
        dn, dd = nearest_dock(lo, la)
        cl, cd = nearest_cluster(lo, la)
        tag = ""
        if cd < 260 and cl["n"] >= 9:
            tag = "  <<OD %d>>" % cl["n"]
        gap = "" if prev is None else " (+%4dm)" % (s - prev)
        print("  %6.0fm%s  passes~%5.1f  %-34s d=%3dm%s" % (s, gap, v, dn, dd, tag))
        prev = s
    return st


LINES = json.load(open(work("corridors_def.json")))
for name, spec in LINES.items():
    if len(sys.argv) > 1 and name not in sys.argv[1:]:
        continue
    report(name, spec["way"], min_gap=spec.get("gap", 550.0), force=spec.get("force", ()))
