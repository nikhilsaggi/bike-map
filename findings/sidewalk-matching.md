# Sidewalks in the Matching Map

For most of this project's life, 43% of every kilometre drawn on the map was
a sidewalk. Not a metaphor for GPS noise: `highway=footway` edges, the
separately-mapped pavements either side of the street, carrying 77,000
recorded passes between them. A single ride down Broadway from Times Square
matched to **123 edges, 120 of them footway**, 3.1 km of it unnamed — drawn
as two parallel dashed lines a street's width apart, joined by stubs at
every crossing.

The cause is not the GPS and not the renderer. `NETWORK_TYPES` composes the
OSM walk network in alongside bike and drive, which contributes **690k of
the graph's 1.4M directed edges**, and the Viterbi matcher has no notion of
rideability: an avenue and the pavement beside it are equally good
explanations of a fix. `HW_PENALTY` exists but only ever fed the older
heuristic matcher. With an 8-wide beam and two thirds of the candidate
edges belonging to the walk network, the beam fills with pavement.

## The fixes are not biased; the network is

The obvious suspicion is a lateral bias in the traces. It is not there.
Measured over 6,925 raw fixes on 24 Manhattan-grid rides, each signed
against the nearest roughly-parallel street centreline in the direction of
travel:

```
pooled mean signed offset      -1.3 m
median |offset|                 6.0 m
per-ride mean signed offset    -8.9 m .. +13.3 m
```

No fleet-wide offset. The per-ride spread is what riding one side of a
two-way street plus urban-canyon multipath looks like. The number that
decides the design is the next one: **52% of those fixes are closer to a
street than to any sidewalk** — a coin flip. At 6 m of noise, emission
distance cannot separate an avenue from its pavement, and no amount of
tuning `HMM_OBS_NOISE` will make it. What was missing was a prior, not
precision.

## What a sidewalk is

A sidewalk is defined by the roadway it accompanies: parallel to it, and
close to it. That is exactly what a greenway, an esplanade or a park path
is not, and it can be measured from geometry the pipeline already has —
which matters, because the cached graph carries no `footway=` or
`bicycle=` subtags at all (osmnx's `useful_tags_way` drops them, so
distinguishing a pavement from a shared path by asking OSM would cost a
full graph refetch).

A `footway` or `steps` edge is sidewalk-class when its median distance to a
parallel roadway is under `SIDEWALK_PARALLEL_M`. Sweeping that threshold
against the real rides, in kilometres of matched footway caught and metres
of named greenway wrongly caught:

```
 threshold   sidewalk caught   greenway lost
     10 m     515 km  (61%)          364 m
     12 m     636 km  (76%)          386 m
     14 m     727 km  (86%)         1612 m
     16 m     751 km  (89%)         1612 m
```

12 m is the knee: 14 m costs four times the collateral for ten more points.

**Only actual roadways may vote, and this is the part that took two tries.**
The first version let any non-footway edge count as the parallel road, and
classed the Hudson River Park Esplanade as a sidewalk — because unnamed pier
access ways and the Pier 57 and Pier 76 service roads run alongside it. A
parallel session hit the same bug from the other side, with a bridleway
alongside Central Park's West Drive and the Hudson River Greenway itself
0.0 m from Riverside Walk. Both were invisible in the aggregate and both
survived every threshold: the esplanade was flagged at 10 m as firmly as at
16 m, so the sweep above could not see them. Restricting the vote to
roadways — a positive list, since a negative one is what let two different
tags leak — takes the esplanade from 36 of its 159 edges flagged to **0 of
159**, with sidewalk recall unchanged to within 1 km. Motorways and trunks
are out for the same reason: a greenway beside a highway is not a sidewalk.

## What it costs

Sidewalk-class edges are dropped from the matching map only. The full graph
still supplies geometry, coverage, drawing and merge, so a ride that really
did happen on a footway still draws there — the filter changes what the
matcher may *choose*, not what the map may show.

Over 60 files / 63 segments, matched-length against GPS length:

```
                 median    p90    footway share   skips   time
 baseline         1.041   1.127       43.9%        33     183s
 no sidewalks     1.031   1.164        0.6%        56      76s
```

Total matched distance moves 287 -> 284 km: the pavement kilometres are
re-matched onto the roadway beside them, not lost. Matching also runs 2.4x
faster, because the beam is no longer spending itself on pavement.

**The skips are the one measure that gets worse, and they are benign.** Of
the 15.13 km the baseline skips, 12.0 km is a single stretch of one ride
that leaves the graph entirely (the South County Trailway, in Westchester)
and is skipped identically either way. The 17 extra skips cost 1.7 km over
284 km ridden — 0.6% — and each is bounded at about 100 m by
`HMM_FAIL_SKIP_POINTS`. Their character settles it: classified by what lies
nearest each skipped span, **49 of 50 have a non-sidewalk street within
40 m, and none are stretches where the removed pavement was the only
network nearby**. Nothing is stranded by the removal; these are ordinary
beam dead-ends that resume onto the street network a block later.

The p90 length ratio rising while the median falls is the metric noticing
that a road path is longer than the pavement shortcut it replaced. Measured
by fix-to-path distance instead, the same change improves at every
threshold.

## Rejected: penalising instead of removing

Keeping every edge reachable and adding a log-probability penalty to
pavement in `logprob_obs` is the gentler design, and it is worse. It gets
the footway share to 15% rather than 0.6%, and the length ratio to 1.061 —
worse than both the baseline and the removal. The reason is the beam:
penalised states still occupy the eight slots, so the road alternatives that
should have been explored are still crowded out. Removal is what frees the
lattice, which is also why it is 2.4x faster rather than 20% faster.

## If the graph is ever refetched

Adding `footway` and `bicycle` to `ox.settings.useful_tags_way` would let
the classifier ask OSM what a way is instead of inferring it from what
happens to run alongside — `footway=sidewalk`, `footway=crossing`,
`bicycle=designated`. That is the better signal, and after watching two
independent geometric classifiers fail in two different ways it is worth
taking whenever a refetch happens for other reasons. It is not worth a
refetch of its own: the geometric rule agrees with itself to 99.9% between
implementations and costs 63 seconds on a map-index rebuild.

## What it did

Rematching all 1,380 rides with the filter in place, against the same graph:

```
                     before                after
matched network      32,636 edges          19,567 edges
                      1,944 km              1,503 km
footway               838.6 km  (43.1%)     115.8 km  (7.7%)
residential           305.5 km              433.5 km
secondary             221.6 km              274.6 km
primary               166.3 km              209.2 km
tertiary              107.2 km              134.5 km
cycleway              116.1 km              130.4 km
coverage                 5.1%  (978 km)        6.5%  (1,253 km)
coverage of NYC          9.1%                 11.8%
```

The drawn network shrank by a quarter while the *counted* network grew by a
third, which is the whole point: a pass that was drawn on the pavement and
counted nowhere is now drawn on the street and counted there. Nothing was
ridden that had not been ridden before.

Per neighborhood, 156 areas improved, 77 were flat and 7 got worse:

```
Midtown-Times Square                 55.9% -> 89.0%   ridden 25.2 -> 40.1 km
Greenwich Village                    49.5% -> 80.0%
Midtown South-Flatiron-Union Square  52.9% -> 79.4%
Upper East Side-Lenox Hill           22.4% -> 47.1%
Brooklyn Heights                     15.9% -> 40.7%
```

Midtown-Times Square is the case that motivated the work: it draws the
fullest-looking blocks on the map, and read 56% covered. It is now credited
40.1 of its 45.1 km of counted street. A parallel measurement using the raw
fixes as arbiter had found that 95% of that area's counted-but-unridden
street metres — 18.8 of 19.9 km — had a ridden footway running within 15 m
and 20 degrees of them. That is what those metres were.

**The seven areas that got worse are the filter's real cost**, and they are
small: Chinatown-Two Bridges 56.2 -> 54.5%, Canarsie Park & Pier 47.9 ->
43.8%, Fordham Heights 11.3 -> 10.2%. Two different mechanisms, both visible
in the edge sets. Fordham Heights lost 186 m of Grand Concourse and gained
nothing; Canarsie lost 340 m of Seaview Avenue and gained nothing — these are
the ~100 m skip holes, landing somewhere rather than nowhere. Chinatown is
churn between parallel candidates: it lost 3.4 km including Chrystie Street
and gained 2.9 km including Pike Street's cycleway, for a net half kilometre.
Neither is a reason to widen the filter, and both are what the 0.6% skip cost
looks like from close up.

One measurement that did *not* move is the check on all of it. Distance
ridden — metres along the trace, from `edge_speed` — moved +1.5% per area
(median +0.3% across the 166 areas with more than a kilometre), while network
ridden moved +30%. That is the signature of re-attribution rather than
discovery: the same riding, credited to the street it happened on instead of
the pavement beside it. Had trace distance moved as much as network did, the
filter would have been inventing riding rather than relocating it.
