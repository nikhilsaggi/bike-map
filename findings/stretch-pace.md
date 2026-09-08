# Stretches That Ride the Same Way Every Time

The panel's other speed ranking asks how far a stretch's two directions part
([direction-split speed](direction-split-speed.md)). That question needs
three passes *each* way, so it can only be asked of a two-way street, and on
these rides it is answered almost entirely by bridges — a gradient is what
makes the two directions differ.

A one-way street is invisible to it, and a one-way street is where a plain
speed says the most: every pass is the same direction, so the average is a
pace rather than a mixture of two. Of the 277 stretches this ranking can
speak about, **131 were never ridden the other way at all**.

## The unit

A stretch is a run of measured chunks along one named street, chained across
the edges OSM split it into, travelled one way. Chaining is what makes it a
ranking of streets rather than of bridges: the median edge is 63 m and the
floor is 250 m, so without it nothing but a bridge deck or a park drive is
long enough to qualify. Chunks join where one's last point is the next one's
first — the graph node they share — and where a name forks, the straightest
continuation wins, so a stretch never depends on dictionary order. Each chunk
is used once, so stretches are disjoint and no metre is ranked twice.

Coverage, from 1,380 rides: 18,955 chunk-directions measured, 2,792 of them
ridden 5+ times (259 km), which chain into 277 stretches of 250 m or more
(207 km).

## Ranked by the speed it beats on most passes

Ranking on the average alone puts a stretch that was fast once above one that
is fast every time. So each stretch is ranked by its average **less** its
pass-to-pass deviation, and by the average **plus** it at the slow end. The
median stretch varies by 16% of its own mean from pass to pass, so this is
not a tie-breaker: it moves the list.

That figure is what the stored record gained a slot for. `edge_speed`'s chunk
record used to hold distance, time, moving time and a pass count per
direction; totals cannot say whether 12 mph was 12 mph every time. It now
also sums the pass speeds and their squares, which is a mean and a standard
deviation over crossings, and combines by addition like everything else in
the record.

The panel puts all three rankings of stretches under one tab strip — Fastest,
Slowest, One way — because they differ in the question, not in the thing
ranked. The number each row prints is the bound it is ranked by, with the
average and the swing under it. A column that sorts by one number and prints another
reads as a ranking of the number on screen and is not one — the same mistake
the neighborhood list made with metres and percentages.

## What it found

```
Fastest                                     mph   average       length
  Northwest Central Park Loop     S        14.0   16.2 ±2.2       344 m
  Sands Street Bike Path          E        12.8   14.0 ±1.3       264 m
  West Drive (Prospect Park)      SW       11.9   14.1 ±2.3     1,463 m
  Johnson Avenue                  E        11.8   13.5 ±1.7       316 m
  Vernon Boulevard                SW       11.7   14.1 ±2.3       630 m
  West 39th Street                NW       11.5   13.1 ±1.6       250 m
  Manhattan Bridge                SE       11.0   11.6 ±0.5     1,661 m

Slowest
  Park Avenue                     NE        7.5    6.2 ±1.3       476 m
  Forsyth Street                  S         7.5    7.3 ±0.2       296 m
  East 33rd Street                NW        7.6    6.7 ±0.9       303 m
  East Houston Street             E         7.7    6.5 ±1.2       353 m
  1st Avenue                      NE        7.7    6.8 ±0.9       936 m
```

Nothing in the pipeline knows about hills, traffic or road surface, and the
list is legible anyway: park drives and bridge approaches at the top, Midtown
cross streets and the Lower East Side at the bottom. The avenues come out
pointing the way they actually run — 1st NE, 2nd SW, 6th NE, 7th SW — which
is a check on the orientation handling as much as a finding.

Time spent stopped is in these numbers, because a red light is part of what
it costs to ride a street. That is most of why Midtown reads 6–8 mph. The
same convention as the corridor list, which divides distance by elapsed time
rather than by moving time.

## The two ends are not the same kind of list

At the fast end the top row is 14.0 mph and the eighth is 11.0, and nothing
else comes within a mile an hour of the leader. At the slow end the top row
is 7.5 and the eighth is 7.8: **18 other stretches sit within 1 mph of the
top**. The fast tab is a podium; the slow tab is a pack, and which eight of
the pack appear is not a finding.

This shows up as a disagreement between the ranking the map ships and the
exact one. The map ranks from the stored record, whose deviation is per chunk
rather than per stretch (which over-states the spread, the conservative
direction) and which cannot ask that a ride covered the stretch it is timing.
`tools/speed_consistency.py` re-measures every ride and can. Over eight rows
they share six at the fast end and two at the slow end — the slow rows differ
because a few tenths of a mile an hour reorder that pack, not because either
is wrong about how fast those streets are.

## What moves the list

Run `python tools/speed_consistency.py --sweep`. Moving the length floor to
150 m or 400 m, or the coverage rule to 0.3 or 0.8, keeps 6 to 9 of each top
ten. The pass floor is different in kind: it decides which stretches are in
the pool at all. At three passes a lucky run outranks a street (Palisades
Boulevard, ridden three times, tops the list at 18.9 mph); at eight, only the
most-ridden streets are left to rank. Five is the compromise, and it is
`config.SPEED_STRETCH_PASSES`.

Two things a row inherits from elsewhere and cannot fix: a stretch is named
from the render cache, where a canonical edge pair takes its name from the
first of its OSM ways, so 524 pairs carry more than one name and a rebuild
can rename a stretch; and the point a row flies to is the midpoint of its
middle chunk, which is a place on the stretch rather than the stretch itself.
