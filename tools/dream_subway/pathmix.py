import gzip
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import GEO, work

LAT = 111320.0

d = json.load(gzip.open(GEO))
P = d["properties"]
names = P["street_names"]
dates = P["dates"]
meta = P["rides"]

key2idx = {}
for i, (di, hm, mi, src) in enumerate(meta):
    key2idx.setdefault((dates[di], hm), []).append(i)

od = json.load(open(work("od_pairs.json")))
clusters = {r["id"]: r for r in json.load(open(work("od_clusters.json")))}

# filename -> ride index
fn2ride = {}
miss = 0
for fn, ca, cb in od["trips"]:
    date = fn[:10]
    hm = fn[11:16].replace("-", ":")
    cand = key2idx.get((date, hm))
    if not cand:
        h, m = int(hm[:2]), int(hm[3:])
        for dm in (1, -1, 2, -2):
            mm = m + dm
            hh = h + (mm // 60)
            cand = key2idx.get((date, "%02d:%02d" % (hh % 24, mm % 60)))
            if cand:
                break
    if not cand:
        miss += 1
        continue
    fn2ride[fn] = cand[0]
print("rides matched to export index:", len(fn2ride), "missed:", miss)

ride2pair = {}
for fn, ca, cb in od["trips"]:
    if fn in fn2ride:
        ride2pair[fn2ride[fn]] = (min(ca, cb), max(ca, cb))


def seglen(c):
    t = 0.0
    for (x1, y1), (x2, y2) in zip(c, c[1:]):
        la = (y1 + y2) / 2
        t += math.hypot((x2 - x1) * 111320.0 * math.cos(math.radians(la)), (y2 - y1) * LAT)
    return t


pairstreet = defaultdict(lambda: defaultdict(float))
for f in d["features"]:
    p = f["properties"]
    sn = p.get("sn")
    nm = names[sn] if sn is not None else "(unnamed)"
    L = seglen(f["geometry"]["coordinates"])
    for r in p["rides"]:
        pr = ride2pair.get(r)
        if pr:
            pairstreet[pr][nm] += L

counts = defaultdict(int)
for pr in ride2pair.values():
    counts[pr] += 1

for pr, n in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
    a, b = clusters[pr[0]]["near"], clusters[pr[1]]["near"]
    print("\n=== %d rides  %s <-> %s" % (n, a, b))
    st = sorted(pairstreet[pr].items(), key=lambda kv: -kv[1])
    tot = sum(v for _, v in st)
    for nm, v in st[:14]:
        print("      %6.1f km/ride  %5.1f%%  %s" % (v / 1000 / n, 100 * v / tot, nm))
