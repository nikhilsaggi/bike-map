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

## The limitation this leaves

A protected bike path mapped in a pedestrian class, running beside the street
it belongs to, is a sidewalk as far as this filter can tell: parallel, close,
pedestrian-tagged. Geometry cannot separate the two, because there is nothing
geometric to separate — the difference is what the way is *for*, which lives
in tags the cached graph does not carry. Where such a path is one candidate
among several the result is usually right anyway, since the street beside it
is still reachable and the fixes decide. Where it is the only candidate a ride
has, the same mechanism that removes a pavement removes the thing the rider
was actually on.

That is the strongest argument for the subtag route below, and the reason the
threshold is set where the sweep says rather than where recall would like.

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

**The seven areas that got worse are small and mixed**: Chinatown-Two Bridges
56.2 -> 54.5%, Canarsie Park & Pier 47.9 -> 43.8%, Fordham Heights 11.3 ->
10.2%. Chinatown is churn between parallel candidates — 3.4 km lost including
Chrystie Street, 2.9 km gained including Pike Street's cycleway, a net half
kilometre. The other two lost a single short stretch each and gained nothing:
186 m of Grand Concourse, 340 m of Seaview Avenue.

Whether such a loss is a skip hole or a correction has to be asked of the
crediting ride's own fixes, and the answer is not uniform. Of the three Grand
Concourse edges, one is plainly a correction — that ride's fixes come within
1 m of the pedestrian edge the filter removed against 10 m of the carriageway,
so the credit it lost was never earned — while on the other two the fixes are
nearer the carriageway (8 m against 13, 10 m against 69). Small losses of both
kinds, then, and no case among them argues for widening the filter.

One measurement that did *not* move is the check on all of it. Distance
ridden — metres along the trace, from `edge_speed` — moved +1.5% per area
(median +0.3% across the 166 areas with more than a kilometre), while network
ridden moved +30%. That is the signature of re-attribution rather than
discovery: the same riding, credited to the street it happened on instead of
the pavement beside it. Had trace distance moved as much as network did, the
filter would have been inventing riding rather than relocating it.

## How right is it

Length ratios say a path is plausible, not that it is the right street. The
discriminating test is per-ride: for each (edge, ride) pair the filter newly
created, whether *that ride's own* fixes ever came within 25 m of the edge —
a pass moved to the avenue one block over passes a pooled test and fails this
one. Over the 2,754 km of newly attributed riding:

```
on a road that ride itself passed within 25 m   2,550.0 km   92.6%
its own ride never within 25 m                    204.2 km    7.4%
```

So about one part in fourteen lands on the wrong street. The residual is not
sidewalk-shaped: residential 76.2 km, secondary 35.7, primary 27.8, and a
cluster of Manhattan cross-streets — West 37th, West 15th, West 47th — sitting
150-250 m from the ride that credits them, which at ~80 m block spacing is the
avenue two or three over. That is a beam-width and emission problem rather
than a pavement one, and the worst cases predate this change: the
Queens-Midtown Tunnel at 1,265 m and the Hudson River Greenway at 502 m were
mismatched before the filter existed. 140 of the 204 km carries a counted
tag, so it inflates coverage rather than only the drawn map.

Coverage of 6.5% sits against 7.2-8.3% measured by treating the raw fixes as
arbiter — about half the remaining gap closed in one step.

**The classifier itself leaked nothing.** Of the ~118 km still matched on
`footway`/`steps` afterwards, none is below the threshold:

```
median distance to a parallel roadway
  <= 12 m  (would have been flagged)     0.0 km    0%
  12-20 m                               58.9 km   50%
  20-40 m                               10.9 km    9%
  >= 40 m  (no roadway near it)         48.4 km   41%
```

Two fifths of what survives has no roadway within 40 m — standalone path,
correctly kept, and it is named accordingly: Central Park Outer Loop, Hudson
River Park Esplanade, Flatbush Avenue Greenway, Shore Road Greenway, the East
River and Riverside esplanades. The half sitting at 12-20 m is the threshold
decision rather than an escape, and the sweep above is the argument for
leaving it there.

## What it also fixed: the merge audit's standing warning

Every pipeline run through 2026-08 ended on a warning nobody could act on:

```
Audit: 108 residual duplicate pairs (7.2 km), 18/42,256 dangling endpoints (0.0%)
WARNING: merge regression suspected -- metrics well above baseline
```

`merge._audit_merge` counts features of 30 m or more that still mutually cover
each other after merging, and it fired at 100 against a healthy baseline
recorded as ~50. The obvious readings were that the merge had regressed or
that the baseline had gone stale. It was neither: the pairs were sidewalks.

The audit reads only the finished feature list, which is exactly what
`docs/rides.geojson.gz` holds, so committed exports can be re-audited without
the pipeline. Across the four exports before the filter and the one after:

```
                        features   pairs      km   dangling
before the filter         21,131     107     7.1   18 (0.0%)
after                     15,449      22     7.3   12 (0.0%)
```

That commit changed `hmm.py`, `config.py` and `cache.py` and left `merge.py`
and `export.py` alone, so nothing about how features merge moved. What moved
was which features existed to merge.

The pairs say the same thing more directly. Measure each pair's median lateral
separation — the shorter feature's samples to the nearest point on the longer:

```
median separation of a residual pair
  before   11.0 m   (p25 8.8, p75 12.8)   65 of 107 at 10 m or more
  after     3.9 m   (p25 3.0, p75  7.3)    3 of  22 at 10 m or more
```

Ten to thirteen metres is a street and its pavement, against a
`SIDEWALK_PARALLEL_M` of 12. Under 4 m is not two ways at all: it is one
cluster's kept siblings after `_average_parallel_geometry` has pulled them onto
a shared centerline, which is what the original ~50 baseline described. The two
populations barely overlap, which makes separation the first thing to measure
when this warns — a residual at pavement spacing is a matching problem and no
merge threshold will reach it.

The count itself was still wrong as an alarm. Duplicates arise per
parallel-way opportunity, so the count rises with the drawn map, and a rider
covering more ground walks it into any fixed threshold. As a share of features
the healthy and regressed states are 0.14% and 0.51% — far enough apart to sit
a threshold between, and stable as the map grows. `MERGE_AUDIT_DUP_SHARE` is
0.003.

Duplicate km did not follow the pairs down, and it is not meant to: three
multi-kilometre bridge and greenway pairs carried 6.1 of the remaining 7.3 km,
so one long corridor drawn twice outweighs eighty short ones. Those three were
a separate fault, and the section below is what they turned out to be.

## Three long corridors the merge kept twice

The three sat on two bridge crossings: the Manhattan Bridge approach (2154 m
beside 2191 m), and the Queensboro / Roosevelt Island crossing, where 1808 m,
2133 m and 2163 m were three drawings of one corridor. They were nothing like the rest of the
residual: equal pass counts on every member, and 3 to 13 m of separation, so a
reader saw one corridor as two or three near-coincident cyan lines rather than
a road and the path beside it.

The separation is the tell. These were **cluster siblings** — Phase 1 formed
one cluster and then kept two or three of its members, after which
`_average_parallel_geometry` pulled all of them onto the same centreline. The
merge's own output made the duplicate harder to see, not easier.

Phase 1 kept them because its greedy set-cover ran to `MERGE_KEEP_COV` = 0.97
of the cluster's sampled extent, and dropped a candidate only when it added
nothing at all. Two long parallel ways stagger at their ends: each covers about
85% of the other, so neither alone reaches 97% of the union. The greedy then
bought the missing few per cent — a couple of hundred metres at the end of a
bridge deck — by drawing the whole 2 km deck a second time. Every kept member
carries the cluster's full merged pass count, so the deck's 40 passes were
printed on both lines.

The fix is a bar on the candidate rather than on the extent: a member already
covered at `MERGE_MUTUAL_COV` (0.75) by the geometries kept so far is skipped
whatever extent it would add. That reuses the constant that decided the two
were one corridor in the first place — if 75% mutual coverage means "the same
corridor", it also means "already drawn".

Testing against the *accumulated* kept set, not against the previous member, is
what keeps this from collapsing the case the multi-keep exists for. A staggered
fragment chain at a junction reaches the same cluster through union-find, link
by link; its far members are only half covered by the near ones, so they clear
the bar and are still kept, and the chain keeps its extent.

Replaying the three real corridors through the merge collapses them to one line
each. The trade is 584 m of parallel-way extent no longer drawn against 6.1 km
of duplicate line removed — and that 584 m is an upper bound, measured with the
corridors in isolation: on the real map their staggered ends run into the
neighbouring clusters' features. No pass is lost either way, because a cluster
accumulates its rides over every member before any of them is kept.
