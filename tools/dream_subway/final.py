"""Curate the placed stations into the proposed network and score it."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import work

sys.argv = [sys.argv[0]]
import lines as L  # noqa: E402

SPEC = json.load(open(work("corridors_def.json")))

# line key -> (id, name, colour, terminals)
META = {
    "2  East Side": ("2", "Second Avenue", "#e0361f", "Yorkville – Delancey"),
    "L  Williamsburg Bridge": ("L", "Williamsburg Bridge", "#9b56d6", "Delancey – Bushwick"),
    "6  Sixth Avenue": ("6", "Sixth Avenue", "#f07f26", "Central Park S – Canal St"),
    "9  West Side": ("9", "Eighth Avenue", "#1f6fd0", "W 72 St – Chelsea"),
    "C  Crosstown": ("C", "Midtown Crosstown", "#00933c", "Hudson River – Long Island City"),
    "H  Hudson Greenway": ("H", "Hudson Greenway", "#0aa3a3", "W 59 St – Battery"),
    "P  Park Loop": ("P", "Park Loop", "#996633", "Central Park, both directions"),
    "M  Manhattan Bridge": ("M", "Manhattan Bridge", "#d6a800", "Houston St – Downtown Bklyn"),
}

# curated display names, keyed by the placer's nearest-dock label; None drops the stop
RENAME = {
    "E 91 St & 2 Ave": "E 91 St",
    "E 78 St & 2 Ave": "E 78 St",
    "E 63 St & 3 Ave": "E 63 St",
    "E 47 St & 2 Ave": "E 47 St–Second Av",
    "2 Ave & E 29 St": "E 29 St",
    "E 13 St & 2 Ave": "E 14 St",
    "E 2 St & 2 Ave": "Houston St",
    "Clinton St & Grand St": "Grand St–Clinton St",
    "Stanton St & Mangin St": None,
    "S 4 St & Wythe Ave": "South 4 St–Wythe Av",
    "Scholes St & Lorimer St": "Lorimer St",
    "Montrose Ave & Bushwick Ave": "Montrose Av",
    "Central Park S & 6 Ave": "Central Park South",
    "W 43 St & 6 Ave": "Bryant Park",
    "Broadway & W 36 St": "Herald Sq–W 36 St",
    "W 25 St & 6 Ave": "W 25 St",
    "W 11 St & 6 Ave": "W 11 St–Sixth Av",
    "Mercer St & Spring St": "Spring St",
    "4 Ave & E 12 St": "Union Sq–E 12 St",
    "Washington Pl & Broadway": "Washington Sq",
    "Lispenard St & Broadway": "Canal St–Broadway",
    "Amsterdam Ave & W 82 St": None,
    "Columbus Ave & W 72 St": "W 72 St–Columbus Av",
    "Broadway & W 58 St": "Columbus Circle",
    "W 56 St & 8 Ave": None,
    "8 Ave & W 49 St": "Times Sq–W 48 St",
    "8 Ave & W 27 St": "W 27 St",
    "W 22 St & 8 Ave": "W 23 St–Eighth Av",
    "12 Ave & W 40 St": None,
    "W 46 St & 11 Ave": "Hudson River–W 47 St",
    "W 50 St & 10 Ave": "W 50 St–Tenth Av",
    "Broadway & W 48 St": "Times Sq–W 48 St",
    "6 Ave & W 45 St": "Bryant Park",
    "E 43 St & Madison Ave": "Grand Central–E 43 St",
    "E 50 St & Park Ave": "E 50 St–Park Av",
    "E 58 St & 1 Ave (NE Corner)": "E 58 St–First Av",
    "Roosevelt Island Tramway": None,
    "21 St & 43 Ave": "Queens Plaza–21 St",
    "11 Ave & W 59 St": "W 59 St–Hudson River",
    "Pier 61 at Chelsea Piers": "Chelsea Piers–W 22 St",
    "10 Ave & W 14 St": "W 14 St–Hudson River",
    "Pier 40 - Hudson River Park": "Pier 40–Houston St",
    "West Thames St": "Battery–West Thames St",
    "7 Ave & Central Park South": "Columbus Circle",
    "Lenox Ave & W 111 St": "North Woods–W 110 St",
    "E 97 St & Madison Ave": "E 97 St–East Dr",
    "5 Ave & E 78 St": "E 79 St–East Dr",
    "5 Ave & E 72 St": "E 72 St–East Dr",
    "Central Park S & Grand Army Plaza": "Grand Army Plaza",
    "Stanton St & Chrystie St": "Houston St",
    "Forsyth St & Canal St": "Canal St–Chrystie St",
    "South St & Pike St": None,
    "Bridge St & Front St": "Dumbo–Front St",
    "Concord St & Bridge St": None,
    "Fulton St & Adams St": "Downtown Bklyn–Fulton St",
}
# Park Loop's W 86 St stop is labelled off a far dock
RENAME_BY_LINE = {("P  Park Loop", "Amsterdam Ave & W 82 St"): "W 86 St–West Dr"}

net = {}
for key, spec in SPEC.items():
    st, prof = L.place(spec["way"], min_gap=spec.get("gap", 550.0), force=spec.get("force", ()))
    stations = []
    for s, lo, la, v in st:
        dock, dm = L.nearest_dock(lo, la)
        label = RENAME_BY_LINE.get((key, dock), RENAME.get(dock, dock))
        if label is None:
            continue
        cl, cd = L.nearest_cluster(lo, la)
        stations.append({"s": round(s), "at": [round(lo, 5), round(la, 5)], "name": label,
                         "dock": dock, "od": cl["n"] if cd < 320 else 0,
                         "passes": round(v, 1)})
    segs = []
    for a, b in zip(stations, stations[1:]):
        vals = [p[3] for p in prof if a["s"] <= p[0] <= b["s"] and p[3] > 0]
        segs.append({"m": b["s"] - a["s"], "load": round(sum(vals) / len(vals), 1) if vals else 0.0})
    lid, lname, colour, term = META[key]
    net[lid] = {"key": key, "name": lname, "colour": colour, "terminals": term,
                "km": round(prof[-1][0] / 1000, 2), "stations": stations, "segments": segs,
                "way": spec["way"], "loop": lid == "P"}

# transfers between curated stations
allst = [(lid, i, s) for lid, v in net.items() for i, s in enumerate(v["stations"])]
tr = []
for i in range(len(allst)):
    for j in range(i + 1, len(allst)):
        a, b = allst[i], allst[j]
        if a[0] == b[0]:
            continue
        dd = L.m(a[2]["at"][0], a[2]["at"][1], b[2]["at"][0], b[2]["at"][1])
        if dd <= 360:
            tr.append({"a": [a[0], a[1]], "b": [b[0], b[1]], "m": round(dd),
                       "an": a[2]["name"], "bn": b[2]["name"]})
xfer = set()
for t in tr:
    xfer.add(tuple(t["a"]))
    xfer.add(tuple(t["b"]))

# express rule: a stop is express if it is a transfer or a top-20 trip end
clusters = sorted(L.clusters, key=lambda c: -c["n"])
cut = clusters[13]["n"]
for lid, v in net.items():
    for i, s in enumerate(v["stations"]):
        s["xfer"] = (lid, i) in xfer
        s["express"] = s["xfer"] or s["od"] >= cut
print("top-14 trip-end cut-off: %d endpoints" % cut)

# how many real rides would this network serve, end to end?
od = json.load(open(work("od_pairs.json")))
cl_by_id = {c["id"]: c for c in L.clusters}
stpts = [(s["at"][0], s["at"][1]) for v in net.values() for s in v["stations"]]


def walk(lo, la):
    return min(L.m(lo, la, x, y) for x, y in stpts)


served = {400: 0, 600: 0, 800: 0}
tot = 0
for fn, ca, cb in od["trips"]:
    tot += 1
    a, b = cl_by_id[ca]["at"], cl_by_id[cb]["at"]
    d = max(walk(*a), walk(*b))
    for r in served:
        if d <= r:
            served[r] += 1
print("A->B rides: %d" % tot)
for r in sorted(served):
    print("  both ends within %dm of a station: %d (%.0f%%)" % (r, served[r], 100 * served[r] / tot))

summary = {"rides": tot, "served": served,
           "endpoints_top4": sum(c["n"] for c in clusters[:4]),
           "endpoints": sum(c["n"] for c in L.clusters),
           "clusters": len(L.clusters), "cut": cut}
json.dump({"lines": net, "transfers": tr, "summary": summary,
           "anchors": clusters[:20]},
          open(work("dream_subway.json"), "w"), indent=1)

for lid, v in net.items():
    print("\n%s  %s  (%s)  %.2f km  %d stops" % (lid, v["name"], v["terminals"], v["km"],
                                                 len(v["stations"])))
    for i, s in enumerate(v["stations"]):
        f = ("X" if s["xfer"] else " ") + ("E" if s["express"] else " ")
        seg = " -- %.0f passes --" % v["segments"][i]["load"] if i < len(v["segments"]) else ""
        print("   [%s] %-26s od=%-4d%s" % (f, s["name"], s["od"], seg))
