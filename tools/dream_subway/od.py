"""Cluster ride endpoints into candidate stations.

Each ride contributes its first and last fix. Endpoints are clustered by
density at a 300 m radius, densest first, and the 45 busiest clusters are
named after their nearest Citi Bike dock. Writes ``od_clusters.json``.
"""

from __future__ import annotations

import math
from collections import defaultdict

from paths import LAT, geo_docks, lonm, ride_ends, write_json

R = 300.0
CELL = 0.005

pts: list[tuple[float, float, str, str]] = []
for name, a, b in ride_ends():
    pts.append((a[0], a[1], "start", name))
    pts.append((b[0], b[1], "end", name))

print("rides", len(pts) // 2, "endpoints", len(pts))

grid: dict[tuple[int, int], list[int]] = defaultdict(list)
for i, p in enumerate(pts):
    grid[(int(p[0] / CELL), int(p[1] / CELL))].append(i)


def neigh(i: int) -> list[int]:
    """Return every endpoint within ``R`` of endpoint ``i``, itself included."""
    lo, la = pts[i][0], pts[i][1]
    gx, gy = int(lo / CELL), int(la / CELL)
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
clusters: list[list[int]] = []
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
    recs.append(
        {
            "id": cid,
            "at": [round(lo, 5), round(la, 5)],
            "n": len(mem),
            "start": s,
            "end": len(mem) - s,
        }
    )
recs.sort(key=lambda r: -r["n"])

docks = geo_docks()


def nearest(lo: float, la: float) -> tuple[str, float]:
    """Return the name of the dock nearest ``(lo, la)`` and its distance in metres."""
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
    print(f"{r['n']:5d}  s{r['start']:4d}/e{r['end']:4d}  {r['at']}  {nm} ({round(dist)}m)")

write_json("od_clusters.json", recs)
top20 = round(100 * sum(r["n"] for r in recs[:20]) / len(pts), 1)
print("clusters", len(recs), "top20 share", top20)
