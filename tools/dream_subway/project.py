"""Project the street-aligned network, and a ghost of the ridden streets, into SVG space.

Superseded: see the README. Reads ``dream_subway.json``, writes ``map_data.json``.
"""

from __future__ import annotations

import math

from paths import load_geo, read_json, write_json

net = read_json("dream_subway.json")

LON0, LAT0 = -73.975, 40.745
TH = math.radians(29.0)
MPD = 111320.0
KX, KY = 0.115, 0.062  # px per metre, x stretched


def proj(lon: float, lat: float) -> tuple[float, float]:
    """Rotate and stretch a lon/lat into diagram space."""
    x = (lon - LON0) * MPD * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * MPD
    xr = x * math.cos(TH) - y * math.sin(TH)
    yr = x * math.sin(TH) + y * math.cos(TH)
    return round(xr * KX, 1), round(-yr * KY, 1)


BB = (-74.025, 40.688, -73.928, 40.802)

# ghost network
d = load_geo()
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


def dstr(pts: list[tuple[float, float]]) -> str:
    """Return SVG path data for a polyline."""
    return "M" + "L".join(f"{p[0]:g} {p[1]:g}" for p in pts)


ghost_d = ["".join(dstr(g[1]) for g in ghost if g[0] == k) for k in (0, 1, 2)]

out = {
    "lines": {},
    "transfers": net["transfers"],
    "summary": net["summary"],
    "anchors": [],
    "ghost": ghost_d,
}
for lid, v in net["lines"].items():
    out["lines"][lid] = {
        "name": v["name"],
        "colour": v["colour"],
        "terminals": v["terminals"],
        "km": v["km"],
        "loop": v["loop"],
        "path": [list(proj(*p)) for p in v["way"]],
        "d": dstr([proj(*p) for p in v["way"]]) + (" Z" if v["loop"] else ""),
        "segments": v["segments"],
        "stations": [
            {
                "n": s["name"],
                "p": list(proj(*s["at"])),
                "od": s["od"],
                "x": s["xfer"],
                "e": s["express"],
                "ll": s["at"],
            }
            for s in v["stations"]
        ],
    }
out["anchors"] = [
    {"n": a["near"], "c": a["n"], "p": list(proj(*a["at"]))} for a in net["anchors"][:12]
]

xs = [p[0] for v in out["lines"].values() for p in v["path"]]
ys = [p[1] for v in out["lines"].values() for p in v["path"]]
print(f"network extent x {min(xs):.0f}..{max(xs):.0f}  y {min(ys):.0f}..{max(ys):.0f}")
gx = [p[0] for g in ghost for p in g[1]]
gy = [p[1] for g in ghost for p in g[1]]
print(
    f"ghost extent   x {min(gx):.0f}..{max(gx):.0f}  y {min(gy):.0f}..{max(gy):.0f}  "
    f"({len(ghost)} segs)"
)

p = write_json("map_data.json", out, separators=(",", ":"))
print("bytes:", p.stat().st_size)
