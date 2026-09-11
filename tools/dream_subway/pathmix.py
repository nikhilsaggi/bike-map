"""Print which streets the rides between each busy pair of places actually use.

Joins ``od_pairs.json`` back to the export's ride index by start date and
time, then sums each pair's ridden metres per street. A diagnostic for the
superseded street-aligned cut: it showed no two pairs share much street.
"""

from __future__ import annotations

from collections import defaultdict

from paths import load_geo, read_json, seglen

d = load_geo()
P = d["properties"]
names = P["street_names"]
dates = P["dates"]
meta = P["rides"]

key2idx: dict[tuple[str, str], list[int]] = {}
for i, (di, hm, _mi, _src) in enumerate(meta):
    key2idx.setdefault((dates[di], hm), []).append(i)

od = read_json("od_pairs.json")
clusters = {r["id"]: r for r in read_json("od_clusters.json")}

# filename -> ride index
fn2ride = {}
miss = 0
for fn, _ca, _cb in od["trips"]:
    date = fn[:10]
    hm = fn[11:16].replace("-", ":")
    cand = key2idx.get((date, hm))
    if not cand:
        h, m = int(hm[:2]), int(hm[3:])
        for dm in (1, -1, 2, -2):
            mm = m + dm
            hh = h + (mm // 60)
            cand = key2idx.get((date, f"{hh % 24:02d}:{mm % 60:02d}"))
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

pairstreet: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: defaultdict(float))
for f in d["features"]:
    p = f["properties"]
    sn = p.get("sn")
    nm = names[sn] if sn is not None else "(unnamed)"
    length = seglen(f["geometry"]["coordinates"])
    for r in p["rides"]:
        pr = ride2pair.get(r)
        if pr:
            pairstreet[pr][nm] += length

counts: dict[tuple[int, int], int] = defaultdict(int)
for pr in ride2pair.values():
    counts[pr] += 1

for pr, n in sorted(counts.items(), key=lambda kv: -kv[1])[:12]:
    a, b = clusters[pr[0]]["near"], clusters[pr[1]]["near"]
    print(f"\n=== {n} rides  {a} <-> {b}")
    st = sorted(pairstreet[pr].items(), key=lambda kv: -kv[1])
    tot = sum(v for _, v in st)
    for nm, v in st[:14]:
        print(f"      {v / 1000 / n:6.1f} km/ride  {100 * v / tot:5.1f}%  {nm}")
