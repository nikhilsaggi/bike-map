"""Rank streets by pass-metres -- the first, street-aligned cut's input.

Sums every drawn feature's length times its pass count per street name.
Superseded: see the README. Writes ``streets.json``.
"""

from __future__ import annotations

from collections import defaultdict

from paths import load_geo, seglen, write_json

d = load_geo()
names = d["properties"]["street_names"]
feats = d["features"]


# per street: pass-metres, metres, features, lon*m, lat*m, most passes on one feature
agg: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0, 0.0, 0.0, 0])
for f in feats:
    p = f["properties"]
    sn = p.get("sn")
    nm = names[sn] if sn is not None else "(unnamed)"
    c = f["geometry"]["coordinates"]
    length = seglen(c)
    n = len(p["rides"])
    a = agg[nm]
    a[0] += n * length
    a[1] += length
    a[2] += 1
    mid = c[len(c) // 2]
    a[3] += mid[0] * length
    a[4] += mid[1] * length
    a[5] = max(a[5], n)

rows = [
    {
        "name": nm,
        "passm": a[0],
        "m": a[1],
        "feats": a[2],
        "avg_n": a[0] / a[1] if a[1] else 0,
        "max_n": a[5],
        "at": [round(a[3] / a[1], 5), round(a[4] / a[1], 5)] if a[1] else None,
    }
    for nm, a in agg.items()
]
rows.sort(key=lambda r: -r["passm"])

print(f"{'street':<38} {'pass-km':>9} {'km':>7} {'avg':>6} {'max':>6}  centroid")
for r in rows[:50]:
    print(
        f"{r['name']:<38} {r['passm'] / 1000:9.1f} {r['m'] / 1000:7.2f} "
        f"{r['avg_n']:6.1f} {r['max_n']:6d}  {r['at']}"
    )
write_json("streets.json", rows)

tot = sum(r["passm"] for r in rows)
print()
top20 = 100 * sum(r["passm"] for r in rows[:20]) / tot
print(f"total pass-km {tot / 1000:.0f} ; top20 share {top20:.1f}%")
