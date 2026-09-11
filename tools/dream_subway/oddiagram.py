"""Lay the O-D network out as an octolinear diagram.

Station positions are the real ones, rotated so Manhattan's grid stands upright
and stretched east-west; every route segment is then drawn as a 45-degree dogleg
rather than along any street. Line names and colours come from the network
file, so ``odnet.py`` stays their one source. Writes od_map_data.json.
"""

from __future__ import annotations

import math
from collections import defaultdict

from paths import read_json, write_json

Point = list[float]

LON0, LAT0 = -73.982, 40.748
THETA = math.radians(29.0)
MPD = 111320.0
KX, KY = 0.150, 0.082  # px per metre; x stretched so the avenues separate
GRID = 15.0  # octolinear grid pitch
TRACK = 7.4  # perpendicular gap between lines sharing a segment
EXPRESS_WEIGHT = 45  # trip ends that make a stop express on their own


def project(lon: float, lat: float) -> tuple[float, float]:
    """Rotate and stretch a lon/lat into diagram space."""
    x = (lon - LON0) * MPD * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * MPD
    xr = x * math.cos(THETA) - y * math.sin(THETA)
    yr = x * math.sin(THETA) + y * math.cos(THETA)
    return xr * KX, -yr * KY


def snap(points: list[tuple[float, float]]) -> list[Point]:
    """Put every station on the octolinear grid, nudging apart any collisions."""
    taken: set[tuple[int, int]] = set()
    out = []
    for x, y in points:
        gx, gy = round(x / GRID), round(y / GRID)
        while (gx, gy) in taken:
            gy += 1
        taken.add((gx, gy))
        out.append([gx * GRID, gy * GRID])
    return out


def dogleg(p: Point, q: Point) -> list[Point]:
    """Join p to q with two segments at 0, 45 or 90 degrees."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return [p, q]
    if abs(dx) >= abs(dy):
        knee = [p[0] + math.copysign(abs(dy), dx), p[1] + dy]
    else:
        knee = [p[0] + dx, p[1] + math.copysign(abs(dx), dy)]
    if abs(knee[0] - p[0]) < 1e-6 and abs(knee[1] - p[1]) < 1e-6:
        return [p, q]
    if abs(knee[0] - q[0]) < 1e-6 and abs(knee[1] - q[1]) < 1e-6:
        return [p, q]
    return [p, knee, q]


def offset(points: list[Point], d: float) -> list[Point]:
    """Shift a polyline sideways by d, perpendicular to its overall direction."""
    if d == 0:
        return points
    ax, ay = points[0]
    bx, by = points[-1]
    length = math.hypot(bx - ax, by - ay) or 1.0
    nx, ny = -(by - ay) / length, (bx - ax) / length
    return [[x + nx * d, y + ny * d] for x, y in points]


def path_d(points: list[Point]) -> str:
    """Return SVG path data for a polyline."""
    return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in points)


def main() -> None:
    """Read od_network.json, lay it out, and write the diagram payload."""
    net = read_json("od_network.json")
    all_st, all_lines, meta = net["stations"], net["lines"], net["meta"]

    # a station the line builder never used is not part of the diagram
    keep = sorted({s for path in all_lines for s in path})
    remap = {old: new for new, old in enumerate(keep)}
    st = [all_st[i] for i in keep]
    lines = [[remap[s] for s in path] for path in all_lines]
    dropped = len(all_st) - len(st)

    pos = snap([project(*s["at"]) for s in st])

    on: dict[int, list[int]] = defaultdict(list)
    for li, path in enumerate(lines):
        for s in path:
            on[s].append(li)

    # a segment carried by several lines gets one track each
    share: dict[tuple[int, int], list[int]] = defaultdict(list)
    for li, path in enumerate(lines):
        for a, b in zip(path, path[1:]):
            share[(min(a, b), max(a, b))].append(li)

    out_lines = []
    for li, path in enumerate(lines):
        segs = []
        for a, b in zip(path, path[1:]):
            members = share[(min(a, b), max(a, b))]
            shift = (members.index(li) - (len(members) - 1) / 2) * TRACK
            segs.append(path_d(offset(dogleg(pos[a], pos[b]), shift)))
        km = 0.0
        for a, b in zip(path, path[1:]):
            lat = (st[a]["at"][1] + st[b]["at"][1]) / 2
            km += (
                math.hypot(
                    (st[b]["at"][0] - st[a]["at"][0]) * MPD * math.cos(math.radians(lat)),
                    (st[b]["at"][1] - st[a]["at"][1]) * MPD,
                )
                / 1000
            )
        out_lines.append(
            {
                "id": meta[li]["id"],
                "name": meta[li]["name"],
                "colour": meta[li]["colour"],
                "segs": segs,
                "stops": path,
                "km": round(km, 1),
                "from": st[path[0]]["near"],
                "to": st[path[-1]]["near"],
            }
        )

    out_st = [
        {
            "n": s["near"],
            "p": [round(pos[i][0], 1), round(pos[i][1], 1)],
            "w": s["n"],
            "lines": [meta[li]["id"] for li in on.get(i, [])],
            "xf": len(on.get(i, [])) > 1,
            "exp": len(on.get(i, [])) > 1 or s["n"] >= EXPRESS_WEIGHT,
            "ll": s["at"],
        }
        for i, s in enumerate(st)
    ]

    # the demand the network is fitted to, drawn as straight chords behind it
    chords = []
    for a, b, v in sorted(net["demand"], key=lambda r: r[2]):
        if v < 2 or a not in remap or b not in remap:
            continue
        ra, rb = remap[a], remap[b]
        chords.append(
            [
                round(pos[ra][0], 1),
                round(pos[ra][1], 1),
                round(pos[rb][0], 1),
                round(pos[rb][1], 1),
                v,
            ]
        )

    xs = [p[0] for p in pos]
    ys = [p[1] for p in pos]
    print(f"diagram extent  x {min(xs):.0f}..{max(xs):.0f}  y {min(ys):.0f}..{max(ys):.0f}")

    payload = {
        "lines": out_lines,
        "stations": out_st,
        "chords": chords,
        "score": net["score"],
        "dropped": dropped,
    }
    p = write_json("od_map_data.json", payload, separators=(",", ":"))
    print(f"wrote {p} ({p.stat().st_size} bytes)")
    n_xf = sum(1 for s in out_st if s["xf"])
    n_exp = sum(1 for s in out_st if s["exp"])
    print(f"{len(out_st)} stations, {n_xf} interchanges, {n_exp} express")


if __name__ == "__main__":
    main()
