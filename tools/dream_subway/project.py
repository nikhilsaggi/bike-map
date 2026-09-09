"""Project the network + a ghost of the ridden street network into SVG space."""
import gzip
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO, work

net = json.load(open(work("dream_subway.json")))

LON0, LAT0 = -73.975, 40.745
TH = math.radians(29.0)
MPD = 111320.0
KX, KY = 0.115, 0.062   # px per metre, x stretched


def proj(lon, lat):
    x = (lon - LON0) * MPD * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * MPD
    X = x * math.cos(TH) - y * math.sin(TH)
    Y = x * math.sin(TH) + y * math.cos(TH)
    return round(X * KX, 1), round(-Y * KY, 1)


BB = (-74.025, 40.688, -73.928, 40.802)

# ghost network
d = json.load(gzip.open(GEO))
ghost = []
for f in d["features"]:
    c = f["geometry"]["coordinates"]
    mid = c[len(c) // 2]
    if not (BB[0] <= mid[0] <= BB[2] and BB[1] <= mid[1] <= BB[3]):
        continue
    n = len(f["properties"]["rides"])
    if n < 2:
        continue
    pts = c if len(c) <= 4 else [c[0], c[len(c) // 2], c[-1]]
    ghost.append([0 if n < 8 else (1 if n < 25 else 2), [proj(*p) for p in pts]])

def dstr(pts):
    return "M" + "L".join("%g %g" % (p[0], p[1]) for p in pts)


ghost_d = ["".join(dstr(g[1]) for g in ghost if g[0] == k) for k in (0, 1, 2)]

out = {"lines": {}, "transfers": net["transfers"], "summary": net["summary"],
       "anchors": [], "ghost": ghost_d}
for lid, v in net["lines"].items():
    out["lines"][lid] = {
        "name": v["name"], "colour": v["colour"], "terminals": v["terminals"],
        "km": v["km"], "loop": v["loop"],
        "path": [list(proj(*p)) for p in v["way"]],
        "d": dstr([proj(*p) for p in v["way"]]) + (" Z" if v["loop"] else ""),
        "segments": v["segments"],
        "stations": [{"n": s["name"], "p": list(proj(*s["at"])), "od": s["od"],
                      "x": s["xfer"], "e": s["express"], "ll": s["at"]} for s in v["stations"]],
    }
for a in net["anchors"][:12]:
    out["anchors"].append({"n": a["near"], "c": a["n"], "p": list(proj(*a["at"]))})

xs = [p[0] for v in out["lines"].values() for p in v["path"]]
ys = [p[1] for v in out["lines"].values() for p in v["path"]]
print("network extent x %.0f..%.0f  y %.0f..%.0f" % (min(xs), max(xs), min(ys), max(ys)))
gx = [p[0] for g in ghost for p in g[1]]
gy = [p[1] for g in ghost for p in g[1]]
print("ghost extent   x %.0f..%.0f  y %.0f..%.0f  (%d segs)"
      % (min(gx), max(gx), min(gy), max(gy), len(ghost)))

p = work("map_data.json")
json.dump(out, open(p, "w"), separators=(",", ":"))
print("bytes:", os.path.getsize(p))
