"""Inject the projected network into page.html and write the publishable page."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import HERE, work

tpl = open(os.path.join(HERE, "page.html")).read()
data = open(work("map_data.json")).read()
assert "__DATA__" in tpl
out = tpl.replace("__DATA__", data)
p = work("dream-subway.html")
open(p, "w").write(out)
print("wrote", p, os.path.getsize(p), "bytes")
