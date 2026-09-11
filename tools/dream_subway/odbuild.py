"""Inject the diagram payload into odpage.html and write the publishable page."""

from __future__ import annotations

from paths import HERE, work

tpl = (HERE / "odpage.html").read_text()
data = work("od_map_data.json").read_text()
assert "__DATA__" in tpl
p = work("dream-subway-od.html")
p.write_text(tpl.replace("__DATA__", data))
print("wrote", p, p.stat().st_size, "bytes")
