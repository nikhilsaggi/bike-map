# Rides by neighborhood: what one citywide percentage was hiding

The map had exactly one geographic statistic — `coverage.pct`, "5.1% of NYC" —
and one place name, the busiest segment. Neighborhood is the unit a reader of
a NYC bike map already thinks in, and nothing on the page spoke it. This is
what came out of cutting the ridden map into NYC's 2020 Neighborhood
Tabulation Areas (262 polygons, NYC Open Data `9nt8-h7nd`), and what shipped
as a result.

Everything below is `python tools/neighborhood_audit.py` on the real rides and
caches: 1,380 rides, 723,764 graph edges, 240 areas with rideable street in
the graph.

## Half the denominator was not New York City

| | km |
|---|---|
| rideable network in the graph (the shipped denominator) | 19,235 |
| of that, inside a NYC neighborhood | **10,063** |
| further than 55 m from every neighborhood | 9,172 |
| ridden, inside a NYC neighborhood | 916.1 of 978.2 |

**Coverage of New York City is 9.1%, not 5.1%.**

The gap is not a bug. `graph._compute_bbox` builds the street network from the
*rides'* own bounding box, clamped to `NYC_BBOX` — so riding to Long Island
once widens the box, pulls in every street in it, and lowers the percentage.
The figure went down for riding further, which is the opposite of what a
coverage number is for. A per-neighborhood denominator does not have the
problem at all, and summing the areas gives the citywide figure the label was
always claiming.

That is now what the hero tile shows. Both numbers ship — the streets panel
carries "Of rideable NYC 9.1%" over "Of the whole graph 5.1%, which reaches
past the city" — because the gap between them is itself the finding.

One caveat the page does not try to state: 10,063 km is the NYC street network
*within the graph's box*, not the whole city's. Staten Island contributes
25 km of network and no rides, because the box has never had a reason to reach
it. So 9.1% is a share of the city the map can see.

## Manhattan is a different story from the citywide number

| borough | ridden | network | covered | areas touched |
|---|---|---|---|---|
| Manhattan | 355.3 km | 1,097 km | **32.4%** | 38/38 |
| Brooklyn | 295.2 km | 2,800 km | 10.5% | 57/69 |
| Queens | 194.7 km | 4,427 km | 4.4% | 64/82 |
| Bronx | 70.9 km | 1,714 km | 4.1% | 33/48 |
| Staten Island | 0.0 km | 25 km | 0% | 0/3 |

Every one of Manhattan's 38 areas has ridden street in it, and a third of its
streets have been ridden. "32% of Manhattan" and "5.1% of the network" are not
the same sentence, and only one of them was on the page.

192 of the 240 areas have at least one ridden edge; the median touched area
holds 2.9 km and 17 hold under 500 m. It is also less concentrated than it
feels: the top 5 areas hold 14% of ridden km, the top 20 hold 39%, the top 50
hold 65%.

Passes below are counted over the same rideable edges as the coverage column,
so the two agree. **The map's popup does not show them** — see "Rides, not
passes" below.

| neighborhood | | ridden | of it | rides | passes |
|---|---|---|---|---|---|
| East Williamsburg | Bk | 36.3 km | 45.5% | 182 | 2,013 |
| Midtown-Times Square | Mn | 25.2 km | 55.9% | 302 | 1,319 |
| Williamsburg | Bk | 25.2 km | 46.5% | 111 | 1,491 |
| Downtown Brooklyn-DUMBO-Boerum Hill | Bk | 22.3 km | 39.8% | 62 | 1,052 |
| Chelsea-Hudson Yards | Mn | 19.0 km | 32.5% | 132 | 1,285 |
| Murray Hill-Kips Bay | Mn | 18.2 km | **60.8%** | 105 | 1,200 |

## "Rode through" and "explored" are different shapes, but not opposite ones

Rides-touching and coverage correlate strongly (Spearman 0.70), which is mildly
surprising — a commute corridor should score high on rides and low on
coverage. Passes per ridden kilometre is the measure that separates them, and
both ends of it are real:

- **Rode through.** Stuyvesant Town-Peter Cooper Village: 149 passes per ridden
  km over 3.2 km, 45 rides, 39% covered. Old Astoria-Hallets Point: 69/km at
  15.8% covered. Pelham Bay-Country Club-City Island: 62/km from **two** rides
   — a park loop ridden round and round, which is the same shape from a
  completely different cause.
- **Explored.** The parks and the far waterfront: Jamaica Bay (East), Alley
  Pond Park, Barren Island-Floyd Bennett Field, Canarsie Park & Pier, all at
  1–5 passes/km from one to six rides.

So the distinction exists, but it does not line up with borough or with
density: it separates *where the commute is* from *where the day out was*, and
one park lap can look exactly like a commute. That is why the map ships the
number and not a label for it.

## Passes and unique kilometres rank almost the same areas

Ranking areas by `edge_traversals` instead of unique km moves things around
without changing the story (Spearman 0.87 between the two orderings). Lower
East Side climbs from 9th to 2nd, Greenpoint drops out of the top ten, and the
top and third are the same either way. Both are in the audit's output; the
export ships unique metres, because that is what a coverage percentage is made
of.

## Midpoint assignment is fine for streets and wrong for bridges

Each edge is assigned to the area containing its **midpoint** — cheap, and it
never splits a count across two areas. Splitting every ridden edge on the
polygons it crosses (`--boundaries`) gives the error:

> 750 of the ridden edges cross a boundary, and **44.9 km — 4.9% of ridden
> length — sits outside the neighborhood its midpoint fell in.**

That 4.9% is not spread thinly. It is a handful of long ways that span a
boundary by design:

| | misplaced | of |
|---|---|---|
| John Finley Walk | 2,419 m | 3,536 m |
| Belt Parkway Bike Path | 1,906 m | 3,554 m |
| North Walk (Washington Heights) | 1,849 m | 2,523 m |
| Williamsburg Bridge Bike Path | 1,541 m | 2,259 m |
| Manhattan Bridge Bike Path | 1,325 m | 2,163 m |

The visible artefact is Fort Hamilton, which reads as **91.9% covered from a
single ride**: the Belt Parkway Bike Path's midpoint lands in it, and 3.6 km of
path against a 3.9 km in-graph network is almost the whole area. Splitting
edges on boundaries would fix it, at the cost of a per-area geometry pass in
the export and a numerator that no longer matches `_coverage_summary`'s. It
was not worth it for 4.9% concentrated in ten named ways — but it is the first
thing to reach for if the per-area numbers are ever used for something
stronger than a fill colour.

## The Citibike claim does not survive at this resolution

That write-up says a reader of the dock layer can find "that Citibike trips
cluster where the ride heatmap is thin". Per neighborhood, they do the
opposite:

- **Every** neighborhood with a Citibike dock endpoint also has ridden street.
  Zero exceptions out of 64.
- Dock volume correlates *positively* with coverage (Spearman **+0.71**) and
  with ridden km (**+0.79**).
- The busiest dock neighborhoods are the best-covered ones: Midtown-Times
  Square (1,627 endpoints, 55.9% covered), Hell's Kitchen (414, 48.5%), Lower
  East Side (407, 59.5%).

The docks and the bike go to the same neighborhoods. If the effect the
write-up describes is real it is *within* a neighborhood — a dock on an
avenue the bike never turns down — and a 262-polygon cut cannot see it. The
claim has been corrected there rather than deleted, because the point it was
making (a layer lets a reader find things nobody ranked) still stands; it was
the example that was unchecked.

## Why it is a layer and not a table

A ranked table of neighborhoods states one finding. Per `CLAUDE.md` — and per
the dock layer's own [detour](citibike-trips.md#the-detour-worth-recording),
where a five-row list was chosen over a map and then reverted — that is the
wrong test for this project. So the export ships polygons, and the page draws
them.

Two decisions made it worth the payload:

- **Fill is coverage as of the date on screen, not all-time.** The export ships
  each area's `new` as `[date index, metres first ridden that day]` — 1,485
  entries across 192 areas, 6 KB gzipped — and the page takes a running total
  up to `filterHi`. So the slider and the time-lapse fill the city in, and 240
  polygons are not the one thing on the page that sits still while everything
  else moves. It follows the range's upper end only, because "how much had
  been ridden by then" is a running total.
- **Every drawn feature carries the area it is in** (`properties.n`), so a
  popup can count the rides through one neighborhood in the visible range.
  That is the number tying the polygon to the streets under it.

## Rides, not passes

The popup's third row counted passes at first, and it was wrong. **Forest
Hills read "104".** It holds 104 drawn segments, each ridden exactly once, by
the same **two** rides — the row was summing a per-street number across an
area, and 104 segment-crossings reads as having been there 104 times.

A pass is a property of one stretch of street. "4 passes" on a street popup
means that stretch was ridden four times, which is exactly right there and
meaningless once added up over a neighborhood. The row counts distinct rides
now: Forest Hills 2, East Williamsburg 254, Midtown-Times Square 726.

There is no honest area-level pass count available. The real one — how many
times a ride entered and left the area — needs the order and the clock of a
ride's edges, and a feature's `rides` array carries neither. Distinct rides
is the question the data can answer.

The per-area ride counts run higher than the audit's (254 against 182 for
East Williamsburg) because they come from the drawn corridors: sidewalks and
service roads are included, and a merged corridor placed by its midpoint can
reach well past the boundary. The audit's column is over rideable graph edges
only. Both are stated; neither is claimed to be the other.

## Distance and time in a neighborhood

`dist_m` and `time_s` sum `edge_speed`'s metres and elapsed seconds over the
area's edges. Both are measurements, not estimates: they come from the ride
CSVs' own timestamps projected onto known geometry, and neither is derived
from the other through an assumed speed.

Three things they are not:

- **Not date-filterable.** `edge_speed` aggregates across rides with no
  per-ride breakdown, so unlike `new` neither can follow the slider. The
  panel that shows them says all-time, and the popup's rows say so on hover.
- **Not restricted to rideable tags.** They are the two figures in the block
  that count every measured edge whatever its highway tag — distance and time
  on a park path are still distance and time spent in the neighborhood. That
  is why Staten Island can show 0.0% covered and 10 minutes.
- **Not totals.** The pass detector places ~357 of the ~519 recorded hours;
  the rest is off-network, inside a recording gap, or on a pass too short to
  admit. Distance is short by the same passes. Both are floors.

What it turns out to be good for is separating the neighborhoods you ride
*through* from the ones you ride *in*. Central Park: 2.6 of 14.1 miles ridden,
131 rides, **20 hours** — a park ridden round and round. Forest Hills: 2 rides,
17 minutes. Midtown-Times Square, the busiest: 44 hours.

| borough | covered | time |
|---|---|---|
| Manhattan | 32.4% | 238 h |
| Brooklyn | 10.5% | 90 h |
| Queens | 4.4% | 22 h |
| Bronx | 4.1% | 7.1 h |
| Staten Island | 0.0% | 10 min |

That table is the Neighborhoods stats section. It is all-time, like every
other section of that panel — the layer is the part that follows the slider,
and a table that moved under the reader while they read it would be a
different and worse thing.

### Distance ridden is not network ridden

`ridden_m` counts a street once however often it was ridden — it is a length
of network, the Explored numerator and what the layer's fill runs on.
`dist_m` counts every pass over that street. The gap between them is the
whole difference between a place explored and a place commuted through, and
on these rides it is several-fold: the Ridden tab and the Explored tab
therefore rank the city in genuinely different orders, which is the reason
they are separate tabs rather than one column.

The obvious cheaper route to the same number is edge length × `edge_traversals`
— the pass counts the map already colours by. It was built first and rejected
on the numbers. Charging a full edge for every pass charges the whole
Manhattan Bridge deck to a trace that clipped one end of it, so the total runs
above the distance the timestamps actually record and whole neighborhoods come
out at average speeds the rides never rode. Distance out of the same chunks as
`time_s` does not have that failure, and it has the further property that the
two tabs are a distance and a duration of the same passes, so a reader can
hold them against each other.

The size of that overshoot is deliberately not quoted. It is a property of one
graph and one matching run rather than of the method, and it moves whenever
the matcher changes which edge a pass is charged to — which is the same
sensitivity that disqualified the method. Re-derive it with
`tools/neighborhood_audit.py` if a number is wanted for a particular run.

The whole block is 88 KB gzipped on a 704 KB payload: 240 simplified polygons
at ~11 m tolerance (the raw boundary file is 1.6 MB gzipped, more than the
rides), their totals, and the per-date arrays. Feature tags add another 20 KB.

The fill is one neutral tone at varying opacity rather than a second colour
scale: plasma already means passes and the docks already took the pale blue,
and a third ramp would need a third legend.

## What this does not do

- **No routing, no per-area speed, no per-area rides.** The block carries
  metres and dates. Everything else the popup shows is counted from the
  features already on the page.
- **It does not change `coverage`.** `coverage.pct` is still measured over
  every rideable edge in the graph; the neighborhood block adds the NYC-only
  totals beside it. Two denominators, both shipped, both labelled.
- **Boundaries are 2020 vintage and pinned.** NTA boundaries were redrawn for
  2020; mixing vintages would double-count the areas that changed, so
  `neighborhoods.NTA_VINTAGE` names the one in use. The file is fetched once
  into `cache/nta_boundaries.geojson` by `cli.main` and never refreshed.
