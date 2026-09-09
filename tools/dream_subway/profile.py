import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO

LAT = 111320.0
d = json.load(gzip.open(GEO))
names = d["properties"]["street_names"]
feats = d["features"]


def seglen(c):
    t = 0.0
    for (x1, y1), (x2, y2) in zip(c, c[1:]):
        la = (y1 + y2) / 2
        t += math.hypot((x2 - x1) * 111320.0 * math.cos(math.radians(la)), (y2 - y1) * LAT)
    return t


want = sys.argv[1:]
by = defaultdict(lambda: defaultdict(float))
axis = {}
for f in feats:
    sn = f["properties"].get("sn")
    if sn is None:
        continue
    nm = names[sn]
    if nm not in want:
        continue
    c = f["geometry"]["coordinates"]
    n = len(f["properties"]["rides"])
    L = seglen(c)
    mid = c[len(c) // 2]
    xs = [p[0] for p in c]
    ys = [p[1] for p in c]
    ew = (max(xs) - min(xs)) * 0.76 > (max(ys) - min(ys))
    axis[nm] = axis.get(nm, 0) + (1 if ew else -1)
    by[nm][(round(mid[0], 3), round(mid[1], 3))] += n * L

for nm in want:
    cells = by[nm]
    if not cells:
        print(nm, "-- none")
        continue
    ew = axis[nm] > 0
    grouped = defaultdict(float)
    for (lo, la), v in cells.items():
        grouped[round(lo, 2) if ew else round(la, 2)] += v
    print("\n== %s (%s) total %.0f pass-km" % (nm, "E-W" if ew else "N-S", sum(cells.values()) / 1000))
    for k in sorted(grouped):
        v = grouped[k] / 1000
        if v < 1:
            continue
        print("   %8.3f  %6.1f  %s" % (k, v, "#" * min(60, int(v))))
