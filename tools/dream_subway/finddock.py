import gzip
import json
import sys

GEO = "/root/bike-map/.claude/worktrees/dream-subway/docs/rides.geojson.gz"
docks = [d for d in json.load(gzip.open(GEO))["properties"]["citibike"]["docks"] if d.get("at")]
for q in sys.argv[1:]:
    parts = [p.lower() for p in q.split("+")]
    print("--", q)
    for d in docks:
        n = d["name"].lower()
        if all(p in n for p in parts):
            print("   %-40s %s" % (d["name"], d["at"]))
