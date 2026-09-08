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

## Ranked by the average, reported with the swing

Each stretch is ranked by its average speed, which is the number the panel
prints, and each row carries the pass-to-pass deviation under it. The median
stretch varies by 16% of its own mean between passes, so that second number
is not decoration: it says whether 12 mph was 12 mph every time or 6 and 18.

Ranking on the average **less** that deviation was built first — the literal
reading of "consistently fastest" — and rejected on the numbers. At the fast
end it reorders a list it agrees with anyway (seven of eight rows). At the
slow end it inverts it: Vanderbilt Avenue, 7.7 ±0.9 mph, outranked Warren
Street at 6.3 for being steady, in a list called Slowest. Steadiness is worth
seeing and not worth ranking on, so it breaks ties and nothing more. The
five-pass floor is what keeps a lucky run out; no row is one ride.

The deviation is what the stored record gained a slot for. `edge_speed`'s
chunk record used to hold distance, time, moving time and a pass count per
direction; totals cannot say whether 12 mph was 12 mph every time. It now
also sums the pass speeds and their squares, which is a mean and a standard
deviation over crossings, and combines by addition like everything else in
the record.

The panel puts all three rankings of stretches under one tab strip — Fastest,
Slowest, One way — because they differ in the question, not in the thing
ranked. Each row prints the number its list is ordered by. A column that
sorts by one number and prints another reads as a ranking of the number on
screen and is not one — the same mistake the neighborhood list made with
metres and percentages.

## What it found

```
Fastest                                    mph       over    rides
  Northwest Central Park Loop     S    16.2 ±2.2      344 m     21
  West Drive (Prospect Park)      SW   14.1 ±2.3    1,463 m      6
  Vernon Boulevard                SW   14.1 ±2.3      630 m      5
  Sands Street Bike Path          E    14.0 ±1.3      264 m      5
  Johnson Avenue                  E    13.5 ±1.7      316 m      8
  West 39th Street                NW   13.1 ±1.6      250 m     10

Slowest
  Park Avenue                     NE    6.2 ±1.3      476 m      6
  Delancey Street                 E     6.3 ±1.5      256 m      6
  West 43rd Street                NW    6.3 ±3.3      278 m      5
  Grand Street                    NW    6.4 ±1.5      333 m     19
  East Houston Street             E     6.5 ±1.2      353 m      6
  East 51st Street                NW    6.6 ±1.5      467 m     10
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

At the fast end the top row is 16.2 mph and the eighth is 12.7, and nothing
else comes within a mile an hour of the leader. At the slow end the top row
is 6.2 and the eighth is 6.6 — the whole tab inside half a mile an hour, with
**20 other stretches within 1 mph of the top**. The fast tab is a podium; the slow tab is a pack, and which eight of
the pack appear is not a finding.

This shows up as a disagreement between the ranking the map ships and the
exact one. The map ranks from the stored record, whose deviation is per chunk
rather than per stretch (which over-states the spread, the conservative
direction) and which cannot ask that a ride covered the stretch it is timing.
`tools/speed_consistency.py` re-measures every ride and can. Over eight rows
they share seven at the fast end and three at the slow — the slow rows differ
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
