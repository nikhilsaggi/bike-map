import csv
import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO, RIDES, work

LAT = 111320.0


def lonm(lat):
    return 111320.0 * math.cos(math.radians(lat))


recs = json.load(open(work("od_clusters.json")))
docks = [d for d in json.load(gzip.open(GEO))["properties"]["citibike"]["docks"] if d.get("at")]


def nearest_dock(lo, la):
    best, bd = None, 1e18
    for d in docks:
        dd = ((d["at"][0] - lo) * lonm(la)) ** 2 + ((d["at"][1] - la) * LAT) ** 2
        if dd < bd:
            bd, best = dd, d
    return best["name"]


for r in recs:
    if "near" not in r:
        r["near"] = nearest_dock(*r["at"])


def cluster_of(lo, la):
    best, bd = None, 1e18
    for r in recs:
        dd = ((r["at"][0] - lo) * lonm(la)) ** 2 + ((r["at"][1] - la) * LAT) ** 2
        if dd < bd:
            bd, best = dd, r
    return best["id"] if bd <= 400.0 ** 2 else None


byid = {r["id"]: r for r in recs}
pairs = defaultdict(int)
directed = defaultdict(int)
loops = 0
unmatched = 0
trips = []
for f in sorted(glob.glob(os.path.join(RIDES, "*.csv"))):
    with open(f, newline="") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 3:
        continue
    body = rows[1:]
    try:
        a = (float(body[0][0]), float(body[0][1]))
        b = (float(body[-1][0]), float(body[-1][1]))
    except ValueError:
        continue
    ca, cb = cluster_of(*a), cluster_of(*b)
    if ca is None or cb is None:
        unmatched += 1
        continue
    if ca == cb:
        loops += 1
        continue
    pairs[(min(ca, cb), max(ca, cb))] += 1
    directed[(ca, cb)] += 1
    trips.append((os.path.basename(f), ca, cb))

print("loop rides (start=end cluster):", loops, " unmatched:", unmatched, " A->B rides:", len(trips))
print()
top = sorted(pairs.items(), key=lambda kv: -kv[1])[:35]
for (x, y), n in top:
    print("%4d  %-34s <-> %s" % (n, byid[x]["near"], byid[y]["near"]))

json.dump({"pairs": [[k[0], k[1], v] for k, v in pairs.items()],
           "trips": trips}, open(work("od_pairs.json"), "w"))
