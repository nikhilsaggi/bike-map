# A subway fitted to the rides

An exercise, not a pipeline stage: what network of rapid-transit lines would
serve the trips these rides actually make? Everything below is measured from
`rides/*.csv` and `docs/rides.geojson.gz`. Reproduce it with
`tools/dream_subway/` (see its README for the running order).

## The two measurements the design rests on

**Where rides end.** The first and last fix of each of the 1,407 recordings,
clustered by density at a 300 m radius, gives 2,814 trip ends in 329 places.
The distribution is extremely top-heavy, which is the only reason a network is
possible at all:

| Place (nearest dock) | Trip ends |
| --- | ---: |
| Broadway & W 48 St (Times Square) | 435 |
| E 43 St & Madison Ave (Grand Central) | 285 |
| Clinton St & Grand St (Lower East Side) | 278 |
| Montrose Ave & Bushwick Ave (East Williamsburg) | 243 |
| W 70 St & Amsterdam Ave | 71 |
| Washington Pl & Broadway | 65 |

Four places take 44% of every trip end; twenty take 65%. 1,320 rides ended
somewhere other than where they began; the other 87 are loops.

**Which streets carry the passes.** Ranking named streets by pass-metres --
drawn length times recorded crossings -- over the export's features:

| Corridor | pass-km |
| --- | ---: |
| Broadway | 241 |
| Central Park drives (West + East) | 235 |
| Sixth Avenue | 196 |
| Second Avenue | 181 |
| Hudson River Greenway | 179 |
| Williamsburg Bridge path | 167 |
| Manhattan Bridge path | 167 |
| Eighth Avenue | 124 |
| Queensboro Bridge path | 92 |

That ranking *is* the line map. Eight lines cover it: 2 (Second Av), 6 (Sixth
Av, continuing on Broadway below W 8 St), 9 (Eighth Av / Columbus),
C (Midtown crosstown, W 49 St to the Queensboro), H (Hudson Greenway),
P (Park Loop), L (Williamsburg Bridge), M (Manhattan Bridge).

## What the design is scored on

45 stations, 52 km, 7 transfers. Taking each of the 1,320 A-to-B rides and
measuring the walk from each end to the nearest station:

| Walk at each end | Rides | Share |
| --- | ---: | ---: |
| within 400 m | 722 | 55% |
| within 600 m | 947 | 72% |
| within 800 m | 1,050 | 80% |

## Decisions worth recording

**A pass count is `len(properties["rides"])`, never `properties["n"]`.**
`n` is the neighborhood index (`neighborhoods.py:361`). An early ranking used
`n` and produced a confident, entirely meaningless table headed by the Staten
Island Expressway at "209 passes" on one ride. The tell was that the top
entries were single-ride features in places the rider went once -- the Five
Boro route -- and that `properties.max_count` said 174 while the ranking
claimed 209.

**Street names repeat across boroughs, so a corridor needs a lat/lon window.**
Unwindowed, "Broadway" chains Manhattan to Bushwick in one polyline and adds a
2.9 km hop across the East River.

**The four anchors are three lines, not four.** Grand Central to the Lower East
Side to East Williamsburg is one through-route -- Second Avenue, then the
Williamsburg Bridge -- because that is how the rides run. Times Square is the
odd one out: it is the largest anchor by a factor of 1.5 and it is served by
two lines crossing (9 and C) rather than by a terminal.

**Express is a rule, not a judgement.** A stop runs express if it is a transfer
or if it is one of the fourteen busiest trip ends (26 arrivals or more). The
cut at 26 is a real break in the cluster distribution -- 26 then 22 -- not a
round number. It gives 16 express stops of 45.

**Two lines are local-only.** The Park Loop, where every stop is a destination
rather than a way through, and the Manhattan Bridge, at four stops too short
for the distinction to buy anything.

**The bridge is the express run.** The Williamsburg Bridge path averages 74
passes over 2.2 km -- the heaviest single stretch in the whole export -- and
carries no intermediate stop. A station was placed there by the demand
sampler and removed: it was reading FDR service roads beside the approach,
not the path.

## What it cannot claim

- A trip end is where a recording started, which is not always where the rider
  did.
- Passes are counted per drawn feature, so a corridor of two parallel ways
  (a street and its bike lane) splits its traffic between them and both rank
  lower than the corridor deserves.
- The greenway and the park drives are credited with riding that is the point
  of the trip rather than a way through it. A subway line is the wrong shape
  for that traffic, and the Park Loop is the honest version of the concession.
- Second Avenue above E 63 St carries 4 to 8 passes a stretch against 90 at
  Grand Street. It is drawn because the rides are there, not because the
  traffic justifies a train.
