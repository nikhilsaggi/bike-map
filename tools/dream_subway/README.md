# Dream subway

A hypothetical eight-line rapid-transit network fitted to the rides in `rides/`
and the published `docs/rides.geojson.gz`. Nothing here is part of the pipeline:
every script reads state and writes only to `cache/dream_subway/`.

Run from the repo root, in order:

```
python tools/dream_subway/od.py        # cluster ride endpoints -> candidate stations
python tools/dream_subway/odpairs.py   # which pairs of clusters rides actually connect
python tools/dream_subway/corridors.py # rank streets by pass-metres
python tools/dream_subway/mkway.py     # build each line's polyline from ridden geometry
python tools/dream_subway/final.py     # place stations, name them, score the network
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

The design decisions the numbers led to are written up in
`findings/dream-subway.md`.
