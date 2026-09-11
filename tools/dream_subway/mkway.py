"""Build each corridor's polyline from the ridden geometry of named streets.

Superseded: see the README. Writes ``corridors_def.json``.
"""

from __future__ import annotations

import math
from collections import defaultdict

from paths import LAT, load_geo, write_json

Point = list[float]

d = load_geo()
names = d["properties"]["street_names"]

byname: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
for f in d["features"]:
    sn = f["properties"].get("sn")
    if sn is None:
        continue
    n = len(f["properties"]["rides"])
    for p in f["geometry"]["coordinates"]:
        byname[names[sn]].append((p[0], p[1], n))


def way(
    street: str,
    axis: str,
    lo: float,
    hi: float,
    olo: float = -180.0,
    ohi: float = 180.0,
    bin_: float = 0.0016,
) -> list[Point]:
    """Trace one street as pass-weighted centres of bins along its axis.

    ``lo``/``hi`` window the street along ``axis`` and ``olo``/``ohi`` across
    it -- street names repeat across boroughs, so an unwindowed Broadway
    chains Manhattan to Bushwick.
    """
    b: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for x, y, n in byname.get(street, []):
        k, o = (y, x) if axis == "ns" else (x, y)
        if not (lo <= k <= hi) or not (olo <= o <= ohi):
            continue
        key = round(k / bin_) * bin_
        b[key][0] += o * n
        b[key][1] += n
    out = []
    for k in sorted(b):
        s, w = b[k]
        if w <= 0:
            continue
        o = s / w
        out.append([round(o, 6), round(k, 6)] if axis == "ns" else [round(k, 6), round(o, 6)])
    return out


def chain(*segs: list[Point]) -> list[Point]:
    """Append segments, flipping each to start nearest the running end."""
    out: list[Point] = []
    for seg in segs:
        pts = [p for p in seg if p]
        if not pts:
            continue
        if out and len(pts) > 1 and math.dist(out[-1], pts[0]) > math.dist(out[-1], pts[-1]):
            pts.reverse()
        out.extend(pts)
    return out


NS = "ns"
EW = "ew"
SPEC = {
    "2  East Side": {
        "way": chain(
            way("2nd Avenue", NS, 40.7235, 40.7815, -73.99, -73.93)[::-1],
            [[-73.98505, 40.72285]],
            way("Clinton Street", NS, 40.7125, 40.7225, -73.99, -73.978)[::-1],
        ),
        "gap": 620,
    },
    "L  Williamsburg Bridge": {
        "way": chain(
            [[-73.98717, 40.71617]],
            way("Williamsburg Bridge Bike Path", EW, -73.9785, -73.9555),
            way("South 4th Street", EW, -73.9595, -73.9490),
            way("Montrose Avenue", EW, -73.9510, -73.9355),
        ),
        "gap": 550,
    },
    "6  Sixth Avenue": {
        "way": chain(
            way("6th Avenue", NS, 40.7325, 40.7675, -74.00, -73.97)[::-1],
            way("Broadway", NS, 40.7175, 40.7335, -74.005, -73.988)[::-1],
        ),
        "gap": 560,
    },
    "9  West Side": {
        "way": chain(
            way("Columbus Avenue", NS, 40.7695, 40.7845, -73.985, -73.965)[::-1],
            way("8th Avenue", NS, 40.7365, 40.7695, -74.00, -73.982)[::-1],
        ),
        "gap": 560,
    },
    "C  Crosstown": {
        "way": chain(
            [[-74.00025, 40.7635]],
            way("West 49th Street", EW, -74.0015, -73.9855, 40.75, 40.77),
            [[-73.98536, 40.76085], [-73.98737, 40.75688]],
            way("West 44th Street", EW, -73.9875, -73.9790, 40.75, 40.762),
            [[-73.97922, 40.75311]],
            way("East 48th Street", EW, -73.9770, -73.9690, 40.75, 40.762),
            way("Ed Koch Queensboro Bridge Path", EW, -73.9685, -73.9430),
        ),
        "gap": 520,
    },
    "H  Hudson Greenway": {
        "way": way("Hudson River Greenway", NS, 40.7080, 40.7775, -74.03, -73.99)[::-1],
        "gap": 720,
    },
    "P  Park Loop": {
        "way": chain(
            way("West Drive", NS, 40.7655, 40.7975, -73.99, -73.955),
            way("East Drive", NS, 40.7655, 40.7975, -73.99, -73.955)[::-1],
        ),
        "gap": 820,
    },
    "M  Manhattan Bridge": {
        "way": chain(
            way("Chrystie Street", NS, 40.7165, 40.7248, -73.996, -73.990)[::-1],
            way("Manhattan Bridge Bike Path", EW, -73.9955, -73.9845),
            way("Jay Street", NS, 40.6915, 40.7035, -73.995, -73.980)[::-1],
        ),
        "gap": 480,
    },
}

FORCE = {
    "H  Hudson Greenway": [[-74.00025, 40.7635]],
    "6  Sixth Avenue": [[-73.99374, 40.72964]],
    "2  East Side": [[-73.99035, 40.7248]],
    "M  Manhattan Bridge": [[-73.99175, 40.72355]],
}

for k, pts in FORCE.items():
    SPEC[k]["force"] = pts

write_json("corridors_def.json", SPEC, indent=1)
for k, v in SPEC.items():
    w = v["way"]
    length, jump = 0.0, 0.0
    for a, b in zip(w, w[1:]):
        la = (a[1] + b[1]) / 2
        s = math.hypot((b[0] - a[0]) * 111320 * math.cos(math.radians(la)), (b[1] - a[1]) * LAT)
        length += s
        jump = max(jump, s)
    print(f"{k:<26} {len(w):3d} pts  {length / 1000:5.2f} km   max hop {jump:4.0f}m")
