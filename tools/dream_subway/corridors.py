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
names = d["properties"]["street_names"]
feats = d["features"]


def seglen(coords):
    t = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:]):
        la = (y1 + y2) / 2
        t += math.hypot((x2 - x1) * 111320.0 * math.cos(math.radians(la)), (y2 - y1) * LAT)
    return t


agg = defaultdict(lambda: [0.0, 0.0, 0, 0.0, 0.0, 0])  # passm, m, feats, clon, clat, maxn
for f in feats:
    p = f["properties"]
    sn = p.get("sn")
    nm = names[sn] if sn is not None else "(unnamed)"
    c = f["geometry"]["coordinates"]
    L = seglen(c)
    n = len(p["rides"])
    a = agg[nm]
    a[0] += n * L
    a[1] += L
    a[2] += 1
    mid = c[len(c) // 2]
    a[3] += mid[0] * L
    a[4] += mid[1] * L
    a[5] = max(a[5], n)

rows = []
for nm, a in agg.items():
    rows.append({"name": nm, "passm": a[0], "m": a[1], "feats": a[2],
                 "avg_n": a[0] / a[1] if a[1] else 0, "max_n": a[5],
                 "at": [round(a[3] / a[1], 5), round(a[4] / a[1], 5)] if a[1] else None})
rows.sort(key=lambda r: -r["passm"])

print("%-38s %9s %7s %6s %6s  %s" % ("street", "pass-km", "km", "avg", "max", "centroid"))
for r in rows[:50]:
    print("%-38s %9.1f %7.2f %6.1f %6d  %s" % (r["name"], r["passm"] / 1000, r["m"] / 1000,
                                               r["avg_n"], r["max_n"], r["at"]))
json.dump(rows, open(work("streets.json"), "w"))

tot = sum(r["passm"] for r in rows)
print()
print("total pass-km %.0f ; top20 share %.1f%%" % (tot / 1000, 100 * sum(r["passm"] for r in rows[:20]) / tot))
