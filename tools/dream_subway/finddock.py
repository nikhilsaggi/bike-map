"""Look up Citi Bike dock coordinates by name -- the station gazetteer.

Each argument is a query; ``+`` joins words that must all appear, so
``python tools/dream_subway/finddock.py grand+central union`` lists the docks
matching each.
"""

from __future__ import annotations

import sys

from paths import geo_docks

docks = geo_docks()
for q in sys.argv[1:]:
    parts = [p.lower() for p in q.split("+")]
    print("--", q)
    for d in docks:
        n = d["name"].lower()
        if all(p in n for p in parts):
            print(f"   {d['name']:<40} {d['at']}")
