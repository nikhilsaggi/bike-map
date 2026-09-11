"""Find which pairs of clusters the rides actually connect.

Snaps each ride's first and last fix to the nearest cluster within 400 m and
counts the rides between each pair. A ride that starts and ends in one
cluster is a loop and connects nothing. Writes ``od_pairs.json``.
"""

from __future__ import annotations

from collections import defaultdict

from paths import LAT, geo_docks, lonm, read_json, ride_ends, write_json

recs = read_json("od_clusters.json")
docks = geo_docks()


def nearest_dock(lo: float, la: float) -> str:
    """Return the name of the dock nearest ``(lo, la)``."""
    best, bd = None, 1e18
    for d in docks:
        dd = ((d["at"][0] - lo) * lonm(la)) ** 2 + ((d["at"][1] - la) * LAT) ** 2
        if dd < bd:
            bd, best = dd, d
    return best["name"]


for r in recs:
    if "near" not in r:
        r["near"] = nearest_dock(*r["at"])


def cluster_of(lo: float, la: float) -> int | None:
    """Return the id of the cluster within 400 m of ``(lo, la)``, if any."""
    best, bd = None, 1e18
    for r in recs:
        dd = ((r["at"][0] - lo) * lonm(la)) ** 2 + ((r["at"][1] - la) * LAT) ** 2
        if dd < bd:
            bd, best = dd, r
    return best["id"] if bd <= 400.0**2 else None


byid = {r["id"]: r for r in recs}
pairs: dict[tuple[int, int], int] = defaultdict(int)
directed: dict[tuple[int, int], int] = defaultdict(int)
loops = 0
unmatched = 0
trips = []
for name, a, b in ride_ends():
    ca, cb = cluster_of(*a), cluster_of(*b)
    if ca is None or cb is None:
        unmatched += 1
        continue
    if ca == cb:
        loops += 1
        continue
    pairs[(min(ca, cb), max(ca, cb))] += 1
    directed[(ca, cb)] += 1
    trips.append((name, ca, cb))

print(
    "loop rides (start=end cluster):",
    loops,
    " unmatched:",
    unmatched,
    " A->B rides:",
    len(trips),
)
print()
top = sorted(pairs.items(), key=lambda kv: -kv[1])[:35]
for (x, y), n in top:
    print(f"{n:4d}  {byid[x]['near']:<34} <-> {byid[y]['near']}")

write_json("od_pairs.json", {"pairs": [[k[0], k[1], v] for k, v in pairs.items()], "trips": trips})
