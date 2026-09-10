# A subway fitted to the rides

An exercise, not a pipeline stage: what network of rapid-transit lines would
serve the trips these rides actually make? Everything below is measured from
`rides/*.csv` and `docs/rides.geojson.gz`. Reproduce it with
`tools/dream_subway/` (see its README for the running order).

## Where the rides end

The first and last fix of each of the 1,407 recordings, clustered by density at
a 300 m radius, gives 2,814 trip ends in 329 places. The distribution is
extremely top-heavy, which is the only reason a network is possible at all:

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

The *flows* between them are far flatter than that: 697 distinct pairs, of
which the five heaviest carry only 15% of rides. The demand is a star with a
very long tail, so a network is judged on the tail.

## The first cut was wrong, and how that was caught

The first version placed stations at pass-count maxima along ridden corridors
and aligned every line to a street. Scored on the same test -- walk from each
ride end to the nearest station -- trip-end clusters alone beat it at equal
station count:

| Station set | <=400 m | <=600 m | <=800 m |
| --- | ---: | ---: | ---: |
| corridor + anchor stations, 52 | 55% | 72% | 80% |
| top-52 trip-end clusters, 52 | 68% | 76% | 81% |

26 of those 52 stations sat somewhere that was not a significant trip end. The
street data was spending station budget rather than earning it, because street
passes measure *how the bike gets there*, which is dominated by bike-lane
geography a tunnel does not share. Two whole lines were artefacts of it: a
Hudson River Greenway line (179 pass-km, 29 trip ends) and a Central Park loop
(235 pass-km, almost no trip ends). Both are how this rider travels, not where.

The kept version consults the origin-destination matrix and nothing else, and
is scored on changes rather than on walk access alone.

## What the O-D matrix asks for

44 places clear the nine-trip-end bar; 36 of them end up on a line. Five lines,
44.9 km:

| | Line | Stops | Route |
| --- | --- | ---: | --- |
| 1 | Broadway - Bushwick | 12 | W 78 St - Montrose Av |
| 2 | West Side - Yorkville | 13 | Division St - E 89 St |
| 3 | East Side - Tribeca | 10 | E 47 St - Murray St |
| 4 | SoHo - Bushwick | 9 | Pier 40 - McKibbin St |
| 5 | Broadway Local | 9 | W 54 St - Murray St |

Line 1 is the whole finding in one route: all four of the busiest places in
five years sit on it, so any pair of them is reachable without a change. It
follows no single street -- it runs diagonally from the Upper West Side through
Times Square and Grand Central to the Lower East Side and out to Bushwick.

Outcomes over the 1,320 point-to-point rides:

| Outcome | Rides | Share |
| --- | ---: | ---: |
| no change at all | 773 | 59% |
| one change | 176 | 13% |
| two or more changes | 133 | 10% |
| no station within 800 m | 238 | 18% |

## Decisions worth recording

**Five lines is the knee, not a preference.** Four reach 68% of rides within
one change, five 72%, six 74% for another 7 km of route. The sweep is in
`odnet.py`'s output.

**Terminals have to earn their length, and seeds bypass that.** Each line is
seeded on a demand pair before any density test, so `trim_terminals` runs
afterwards: an end stop stays only if it keeps at least 3.5 rides per km of
route it adds. Without it a line ran to W 204 St -- nine trip ends, ten
kilometres -- because the seed put it there and nothing took it back off.

**Insertion beats extension.** Growing lines only at their ends made them
wander into the tail chasing small gains. Allowing a station to be inserted at
any position, gated on demand per kilometre added, keeps lines straight and
lets them pick up an intermediate place without distorting their shape.

**A pass count is `len(properties["rides"])`, never `properties["n"]`.**
`n` is the neighborhood index (`neighborhoods.py:361`). An early ranking used
`n` and produced a confident, entirely meaningless table headed by the Staten
Island Expressway at "209 passes" on one ride. The tell was that the top
entries were single-ride features in places the rider went once -- the Five
Boro route -- and that `properties.max_count` said 174 while the ranking
claimed 209.

**Street names repeat across boroughs, so a corridor needs a lat/lon window.**
This mattered to the superseded first cut and still matters to `corridors.py`,
which the diagnostics use: unwindowed, "Broadway" chains Manhattan to Bushwick
in one polyline and adds a 2.9 km hop across the East River.

## On the map

The network ships as `properties.subway` and draws as a fourth layer on
`docs/index.html`, off until asked for. Five things about it are
load-bearing.

**The chords are straight.** A station's position is real -- the centroid of a
cluster of ride endpoints -- but the line between two stations is drawn as a
straight chord, because `odnet.py` never opens the street geometry. Routing it
along roads for display would put a plausible-looking path on the map that no
measurement backs, which is the same reason a Citibike dock's links stay
straight.

**The streets go to outline underneath it.** Five of the line colours sit
inside the plasma ramp the network is drawn in, so at full brightness the
overlay and the heatmap lose against each other. The layer ghosts the network
the way a focused dock already did -- the streets stay on screen, and the
slider still moves them, in outline. The predicate is shared
(`networkIsContext`), because it is the same judgement twice.

**Two lines on one stretch each get a track.** 5 of the 43 segments are
carried by two lines; on one centreline the second hides the first. Each chord
is a multi-polyline, and a shared segment tapers out to its own track and back
so both lines still meet at the stop they share. The offset is in screen
pixels, recomputed on zoom: a fixed offset on the ground collapses to one line
at city scale, which is the scale this layer is read at.

**Bends are corners, not points.** Each interior vertex is replaced by a
12px-radius arc -- a quadratic Bezier whose control point is the vertex,
sampled into points because Leaflet's canvas renderer strokes polylines and
nothing else. The station stays exactly where it is; the line pulls off the
vertex by at most 3.6px, which its own marker covers. Splining *through* the
stations would keep them dead centre but bow the chord between them, and a
curved chord claims a route that was never measured.

**It cannot follow the slider**, and that is the one thing a reader would
otherwise assume. Station weights and the lines themselves are fitted to the
whole history; a date-filtered version would resize the markers while leaving
the network under them unchanged, which is a filter that looks like it works.
The toggle's tooltip says so.

## What it cannot claim

- A trip end is where a recording started, which is not always where the rider
  did.
- Frequency, capacity and interchange time are not modelled. "One change"
  counts a change; it does not price one.
- The 238 unreachable rides are spread thin -- no single missing place accounts
  for more than a handful -- so no sixth line recovers them. They are the cost
  of a 36-station network in a city this size, not a fixable gap.
- It is fitted to one rider's five years. It is a portrait, not a plan.
