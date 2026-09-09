"""Inject the diagram payload into odpage.html and write the publishable page."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HERE, work  # noqa: E402

tpl = open(os.path.join(HERE, "odpage.html")).read()
data = open(work("od_map_data.json")).read()
assert "__DATA__" in tpl
out = tpl.replace("__DATA__", data)
p = work("dream-subway-od.html")
open(p, "w").write(out)
print("wrote", p, os.path.getsize(p), "bytes")
