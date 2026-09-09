"""Lay the O-D network out as an octolinear diagram.

Station positions are the real ones, rotated so Manhattan's grid stands upright
and stretched east-west; every route segment is then drawn as a 45-degree dogleg
rather than along any street. Writes od_map_data.json.
"""
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import work  # noqa: E402

LON0, LAT0 = -73.982, 40.748
THETA = math.radians(29.0)
MPD = 111320.0
KX, KY = 0.150, 0.082     # px per metre; x stretched so the avenues separate
GRID = 15.0               # octolinear grid pitch
TRACK = 7.4               # perpendicular gap between lines sharing a segment
EXPRESS_WEIGHT = 45       # trip ends that make a stop express on their own

META = [
    ("1", "Broadway - Bushwick", "#d6262b"),
    ("2", "West Side - Yorkville", "#0a7bc2"),
    ("3", "East Side - Tribeca", "#159a4e"),
    ("4", "SoHo - Bushwick", "#e2801a"),
    ("5", "Broadway Local", "#7d4bb5"),
    ("6", "Sixth", "#0f9c9c"),
    ("7", "Seventh", "#b3457f"),
    ("8", "Eighth", "#6d7a1f"),
]


def project(lon, lat):
    """Rotate and stretch a lon/lat into diagram space."""
    x = (lon - LON0) * MPD * math.cos(math.radians(LAT0))
    y = (lat - LAT0) * MPD
    xr = x * math.cos(THETA) - y * math.sin(THETA)
    yr = x * math.sin(THETA) + y * math.cos(THETA)
    return xr * KX, -yr * KY


def snap(points):
    """Put every station on the octolinear grid, nudging apart any collisions."""
    taken, out = {}, []
    for x, y in points:
        gx, gy = round(x / GRID), round(y / GRID)
        while (gx, gy) in taken:
            gy += 1
        taken[(gx, gy)] = True
        out.append([gx * GRID, gy * GRID])
    return out


def dogleg(p, q):
    """Two segments at 0, 45 or 90 degrees joining p to q."""
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


def offset(points, d):
    """Shift a polyline sideways by d, perpendicular to its overall direction."""
    if d == 0:
        return points
    ax, ay = points[0]
    bx, by = points[-1]
    L = math.hypot(bx - ax, by - ay) or 1.0
    nx, ny = -(by - ay) / L, (bx - ax) / L
    return [[x + nx * d, y + ny * d] for x, y in points]


def path_d(points):
    """SVG path data for a polyline."""
    return "M" + "L".join("%.1f %.1f" % (x, y) for x, y in points)


def main():
    """Read od_network.json, lay it out, and write the diagram payload."""
    net = json.load(open(work("od_network.json")))
    all_st, all_lines = net["stations"], net["lines"]

    # a station the line builder never used is not part of the diagram
    keep = sorted({s for path in all_lines for s in path})
    remap = {old: new for new, old in enumerate(keep)}
    st = [all_st[i] for i in keep]
    lines = [[remap[s] for s in path] for path in all_lines]
    dropped = len(all_st) - len(st)

    pos = snap([project(*s["at"]) for s in st])

    on = defaultdict(list)
    for li, path in enumerate(lines):
        for s in path:
            on[s].append(li)

    # a segment carried by several lines gets one track each
    share = defaultdict(list)
    for li, path in enumerate(lines):
        for a, b in zip(path, path[1:]):
            share[(min(a, b), max(a, b))].append(li)

    out_lines = []
    for li, path in enumerate(lines):
        lid, name, colour = META[li]
        segs = []
        for a, b in zip(path, path[1:]):
            key = (min(a, b), max(a, b))
            members = share[key]
            k = members.index(li)
            shift = (k - (len(members) - 1) / 2) * TRACK
            segs.append(path_d(offset(dogleg(pos[a], pos[b]), shift)))
        km = 0.0
        for a, b in zip(path, path[1:]):
            lat = (st[a]["at"][1] + st[b]["at"][1]) / 2
            km += math.hypot((st[b]["at"][0] - st[a]["at"][0]) * MPD
                             * math.cos(math.radians(lat)),
                             (st[b]["at"][1] - st[a]["at"][1]) * MPD) / 1000
        out_lines.append({
            "id": lid, "name": name, "colour": colour, "segs": segs,
            "stops": path, "km": round(km, 1),
            "from": st[path[0]]["near"], "to": st[path[-1]]["near"],
        })

    out_st = []
    for i, s in enumerate(st):
        out_st.append({
            "n": s["near"], "p": [round(pos[i][0], 1), round(pos[i][1], 1)],
            "w": s["n"], "lines": [META[li][0] for li in on.get(i, [])],
            "xf": len(on.get(i, [])) > 1,
            "exp": len(on.get(i, [])) > 1 or s["n"] >= EXPRESS_WEIGHT,
            "ll": s["at"],
        })

    # the demand the network is fitted to, drawn as straight chords behind it
    chords = []
    for a, b, v in sorted(net["demand"], key=lambda r: r[2]):
        if v < 2 or a not in remap or b not in remap:
            continue
        ra, rb = remap[a], remap[b]
        chords.append([round(pos[ra][0], 1), round(pos[ra][1], 1),
                       round(pos[rb][0], 1), round(pos[rb][1], 1), v])

    xs = [p[0] for p in pos]
    ys = [p[1] for p in pos]
    print("diagram extent  x %.0f..%.0f  y %.0f..%.0f" % (min(xs), max(xs), min(ys), max(ys)))

    payload = {"lines": out_lines, "stations": out_st, "chords": chords,
               "score": net["score"], "dropped": dropped}
    p = work("od_map_data.json")
    json.dump(payload, open(p, "w"), separators=(",", ":"))
    print("wrote %s (%d bytes)" % (p, os.path.getsize(p)))
    print("%d stations, %d interchanges, %d express"
          % (len(out_st), sum(1 for s in out_st if s["xf"]),
             sum(1 for s in out_st if s["exp"])))


if __name__ == "__main__":
    main()
