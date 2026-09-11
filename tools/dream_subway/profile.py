"""Print the pass demand along named streets, binned along each street's axis.

Each argument is a street name, e.g.
``python tools/dream_subway/profile.py Broadway "1st Avenue"``.
A diagnostic for the superseded street-aligned cut.
"""

from __future__ import annotations

import sys
from collections import defaultdict

from paths import load_geo, seglen

d = load_geo()
names = d["properties"]["street_names"]
feats = d["features"]

want = sys.argv[1:]
by: dict[str, dict[tuple[float, float], float]] = defaultdict(lambda: defaultdict(float))
axis: dict[str, int] = {}
for f in feats:
    sn = f["properties"].get("sn")
    if sn is None:
        continue
    nm = names[sn]
    if nm not in want:
        continue
    c = f["geometry"]["coordinates"]
    n = len(f["properties"]["rides"])
    length = seglen(c)
    mid = c[len(c) // 2]
    xs = [p[0] for p in c]
    ys = [p[1] for p in c]
    ew = (max(xs) - min(xs)) * 0.76 > (max(ys) - min(ys))
    axis[nm] = axis.get(nm, 0) + (1 if ew else -1)
    by[nm][(round(mid[0], 3), round(mid[1], 3))] += n * length

for nm in want:
    cells = by[nm]
    if not cells:
        print(nm, "-- none")
        continue
    ew = axis[nm] > 0
    grouped: dict[float, float] = defaultdict(float)
    for (lo, la), v in cells.items():
        grouped[round(lo, 2) if ew else round(la, 2)] += v
    total = sum(cells.values()) / 1000
    print(f"\n== {nm} ({'E-W' if ew else 'N-S'}) total {total:.0f} pass-km")
    for k in sorted(grouped):
        v = grouped[k] / 1000
        if v < 1:
            continue
        print(f"   {k:8.3f}  {v:6.1f}  {'#' * min(60, int(v))}")
