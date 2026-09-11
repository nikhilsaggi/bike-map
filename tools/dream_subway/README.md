# Desire Lines

A hypothetical rapid-transit network fitted to the rides in `rides/` and the
published `docs/rides.geojson.gz`, drawn on the map as the Desire Lines layer.
Nothing here is part of the pipeline: every script reads state and writes only
to `cache/dream_subway/`.

### Refitting the map layer

`python update.py` re-embeds `cache/dream_subway/od_network.json` on every run
but never refits it, so new rides change nothing until these three are rerun,
from the repo root, in order:

```
python tools/dream_subway/od.py        # cluster ride endpoints -> candidate stations
python tools/dream_subway/odpairs.py   # which pairs of clusters rides actually connect
python tools/dream_subway/odnet.py     # stations + lines -> cache/dream_subway/od_network.json
```

Then run the pipeline (`python update.py`, or `python -m bike_routes --no-png`
to rebuild the export alone) to put the new network into
`docs/rides.geojson.gz`. `od.py` needs the ride CSVs, so this only runs where
`rides/` is.

**Check the line names after a refit.** They are hand-written in `odnet.py`'s
`META` and handed out by build order, so a refit that reshapes or reorders the
lines leaves a name on a line that no longer runs there (bm-b5r).

### The standalone proposal page

Two more scripts turn the same network into the schematic proposal page; the
map does not need them:

```
python tools/dream_subway/oddiagram.py # octolinear layout
python tools/dream_subway/odbuild.py   # inject the data into odpage.html
```

### Superseded: the street-aligned cut

The first version placed stations along ridden corridors and aligned lines to
streets. It lost on its own test and is kept only so the comparison can be
re-run (`findings/dream-subway.md` has the numbers):

```
python tools/dream_subway/corridors.py # rank streets by pass-metres
python tools/dream_subway/mkway.py     # each line's polyline from ridden geometry
python tools/dream_subway/final.py     # place stations, name them, score
python tools/dream_subway/project.py   # rotate/stretch into diagram coordinates
python tools/dream_subway/build.py     # inject the data into page.html
```

`pathmix.py` and `profile.py` are diagnostics: the street mix of each
origin-destination pair, and the demand profile along one named street.
`finddock.py` looks up Citi Bike dock coordinates by name — the station
gazetteer.

Two conventions matter when reading the code:

- **A feature's pass count is `len(properties["rides"])`, not `properties["n"]`.**
  `n` is the neighborhood index (`neighborhoods.py:361`). Ranking on `n` produces
  a plausible-looking table of streets that means nothing.
- **Corridors are defined by street name plus a lat/lon window** (`mkway.py`),
  because street names repeat across boroughs — an unwindowed "Broadway" chains
  Manhattan to Bushwick.
- **`odnet.py` never opens the geometry.** It reads trip ends and the O-D
  matrix, and that is the point: street traffic measures how the bike gets
  somewhere, which a tunnel does not share.

The design decisions the numbers led to are written up in
`findings/dream-subway.md`.
