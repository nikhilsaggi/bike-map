"""Place stations on a hand-defined corridor from measured pass demand.

Run on its own it reports each corridor in ``corridors_def.json`` (or only
the ones named as arguments); ``final.py`` imports it for the placer.
Superseded: see the README.
"""

from __future__ import annotations

import math
import sys
from collections import defaultdict
from typing import Any

from paths import LAT, geo_docks, load_geo, read_json

# (metres along the corridor, lon, lat, demand)
Sample = tuple[float, float, float, float]

d = load_geo()
names = d["properties"]["street_names"]
docks = geo_docks(d)
clusters = read_json("od_clusters.json")


def m(lo1: float, la1: float, lo2: float, la2: float) -> float:
    """Return the distance in metres between two lon/lat points."""
    la = (la1 + la2) / 2
    return math.hypot((lo2 - lo1) * 111320.0 * math.cos(math.radians(la)), (la2 - la1) * LAT)


# feature midpoints with pass count
F = []
for f in d["features"]:
    c = f["geometry"]["coordinates"]
    length = sum(m(*c[i], *c[i + 1]) for i in range(len(c) - 1))
    mid = c[len(c) // 2]
    sn = f["properties"].get("sn")
    F.append(
        (mid[0], mid[1], len(f["properties"]["rides"]), length, names[sn] if sn is not None else "")
    )

CELL = 0.004
grid: dict[tuple[int, int], list[int]] = defaultdict(list)
for i, ft in enumerate(F):
    grid[(int(ft[0] / CELL), int(ft[1] / CELL))].append(i)


def near_feats(lo: float, la: float, r: float) -> list[int]:
    """Return the features whose midpoint lies within ``r`` metres."""
    gx, gy = int(lo / CELL), int(la / CELL)
    span = int(r / 300) + 1
    out: list[int] = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            out.extend(
                i for i in grid.get((gx + dx, gy + dy), ()) if m(lo, la, F[i][0], F[i][1]) <= r
            )
    return out


def nearest_dock(lo: float, la: float) -> tuple[str, float]:
    """Return the nearest dock's name and its distance in metres."""
    best, bd = None, 1e18
    for x in docks:
        dd = m(lo, la, x["at"][0], x["at"][1])
        if dd < bd:
            bd, best = dd, x
    return best["name"], bd


def nearest_cluster(lo: float, la: float) -> tuple[dict[str, Any], float]:
    """Return the nearest trip-end cluster and its distance in metres."""
    best, bd = None, 1e18
    for c in clusters:
        dd = m(lo, la, c["at"][0], c["at"][1])
        if dd < bd:
            bd, best = dd, c
    return best, bd


def resample(way: list[list[float]], step: float = 100.0) -> list[tuple[float, float, float]]:
    """Densify a [lon, lat] polyline to ``(lon, lat, metres along)`` every ``step``."""
    pts = [(way[0][0], way[0][1], 0.0)]
    acc = 0.0
    for a, b in zip(way, way[1:]):
        length = m(a[0], a[1], b[0], b[1])
        k = max(1, int(length / step))
        for i in range(1, k + 1):
            t = i / k
            acc_i = acc + length * t
            pts.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, acc_i))
        acc += length
    return pts


def demand(lo: float, la: float, r: float = 90.0) -> float:
    """Return the average passes on features within r metres, length-weighted."""
    idx = near_feats(lo, la, r)
    if not idx:
        return 0.0
    num = sum(F[i][2] * F[i][3] for i in idx)
    den = sum(F[i][3] for i in idx)
    return num / den if den else 0.0


def profile(way: list[list[float]], step: float = 100.0, r: float = 90.0) -> list[Sample]:
    """Sample demand along a corridor every ``step`` metres."""
    return [(p[2], p[0], p[1], demand(p[0], p[1], r)) for p in resample(way, step)]


def place(
    way: list[list[float]],
    min_gap: float = 550.0,
    step: float = 100.0,
    r: float = 90.0,
    force: tuple | list = (),
) -> tuple[list[Sample], list[Sample]]:
    """Choose stops along a corridor: forced points, then trip ends, then demand peaks."""
    prof = profile(way, step, r)
    forced = [min(prof, key=lambda p: m(fx, fy, p[1], p[2])) for fx, fy in force]
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
        if m(c["at"][0], c["at"][1], best[1], best[2]) <= 300 and all(
            abs(best[0] - o[0]) >= min_gap * 0.5 for o in out
        ):
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


def report(label: str, way: list[list[float]], **kw: float | list) -> list[Sample]:
    """Print a corridor's stops with the dock and trip-end cluster nearest each."""
    st, prof = place(way, **kw)
    total = prof[-1][0]
    print(f"\n=== {label}   {total / 1000:.2f} km, {len(st)} stops")
    prev = None
    for s, lo, la, v in st:
        dn, dd = nearest_dock(lo, la)
        cl, cd = nearest_cluster(lo, la)
        tag = ""
        if cd < 260 and cl["n"] >= 9:
            tag = f"  <<OD {cl['n']}>>"
        gap = "" if prev is None else f" (+{int(s - prev):4d}m)"
        print(f"  {s:6.0f}m{gap}  passes~{v:5.1f}  {dn:<34} d={int(dd):3d}m{tag}")
        prev = s
    return st


if __name__ == "__main__":
    for name, spec in read_json("corridors_def.json").items():
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        report(name, spec["way"], min_gap=spec.get("gap", 550.0), force=spec.get("force", ()))
