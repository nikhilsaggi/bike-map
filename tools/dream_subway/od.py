import csv
import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO, RIDES, work

pts = []
for f in sorted(glob.glob(os.path.join(RIDES, "*.csv"))):
    with open(f, newline="") as fh:
        r = list(csv.reader(fh))
    if len(r) < 3:
        continue
    body = r[1:]
    try:
        a = (float(body[0][0]), float(body[0][1]))
        b = (float(body[-1][0]), float(body[-1][1]))
    except ValueError:
        continue
    base = os.path.basename(f)
    pts.append((a[0], a[1], "start", base))
    pts.append((b[0], b[1], "end", base))

print("rides", len(pts) // 2, "endpoints", len(pts))

R = 300.0
LAT = 111320.0


def lonm(lat):
    return 111320.0 * math.cos(math.radians(lat))


cell = 0.005
grid = defaultdict(list)
for i, p in enumerate(pts):
    grid[(int(p[0] / cell), int(p[1] / cell))].append(i)


def neigh(i):
    lo, la = pts[i][0], pts[i][1]
    gx, gy = int(lo / cell), int(la / cell)
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in grid.get((gx + dx, gy + dy), ()):
                dlo = (pts[j][0] - lo) * lonm(la)
                dla = (pts[j][1] - la) * LAT
                if dlo * dlo + dla * dla <= R * R:
                    out.append(j)
    return out


order = list(range(len(pts)))
assigned = [-1] * len(pts)
clusters = []
dens = {i: len(neigh(i)) for i in order}
for i in sorted(order, key=lambda i: -dens[i]):
    if assigned[i] >= 0:
        continue
    members = [j for j in neigh(i) if assigned[j] < 0]
    if not members:
        continue
    cid = len(clusters)
    for j in members:
        assigned[j] = cid
    clusters.append(members)

recs = []
for cid, mem in enumerate(clusters):
    lo = sum(pts[j][0] for j in mem) / len(mem)
    la = sum(pts[j][1] for j in mem) / len(mem)
    s = sum(1 for j in mem if pts[j][2] == "start")
    recs.append({"id": cid, "at": [round(lo, 5), round(la, 5)], "n": len(mem),
                 "start": s, "end": len(mem) - s})
recs.sort(key=lambda r: -r["n"])

docks = [d for d in json.load(gzip.open(GEO))["properties"]["citibike"]["docks"] if d.get("at")]


def nearest(lo, la):
    best, bd = None, 1e18
    for d in docks:
        dlo = (d["at"][0] - lo) * lonm(la)
        dla = (d["at"][1] - la) * LAT
        dd = dlo * dlo + dla * dla
        if dd < bd:
            bd, best = dd, d
    return best["name"], math.sqrt(bd)


for r in recs[:45]:
    nm, dist = nearest(*r["at"])
    r["near"] = nm
    r["near_m"] = round(dist)
    print("%5d  s%4d/e%4d  %s  %s (%dm)" % (r["n"], r["start"], r["end"], r["at"], nm, round(dist)))

json.dump(recs, open(work("od_clusters.json"), "w"))
print("clusters", len(recs), "top20 share", round(100 * sum(r["n"] for r in recs[:20]) / len(pts), 1))
