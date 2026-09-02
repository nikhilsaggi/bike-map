# Traversal Counting

How the map's pass counts are measured, and the evidence behind each rule in
`edge_speed.py` and `merge.py`. The rules themselves are in CLAUDE.md under
"Edge passes"; this is why they are what they are.

A traversal (the code) and a pass (the map) are the same thing: one crossing
of one drawn stretch by one ride. An out-and-back is two.

## Why the counts cannot come from the matcher

The matcher returns a path, and a path is not a count. It collapses
consecutive repeats of an edge, and the repeats it does emit cannot be told
apart from lattice oscillation at an intersection -- `A->B->C->B->A` emits
`(A,B)` twice for a single pass. The raw timestamped fixes can tell those
apart, because they carry position over time; the decoded edge list has
already thrown that away.

## A crossing arrives in fragments

`_runs` ends a run at `SPEED_MAX_FIX_GAP_S` -- correct for speed, where a red
light is not riding time -- and again whenever the trace snaps to a
neighbouring way. One crossing of the 2.3 km Williamsburg Bridge path landed
as **23 fragments**.

`_merge_resumed` rejoins them by *progression*: a fragment that resumes at or
ahead of where the last one stopped, in that pass's own direction of travel,
is the same traversal, with `TRAVERSAL_RESUME_M` of slack on the backward
side for a stop letting the trace drift. The two things that must not be
absorbed both fail that test -- a second lap re-enters from the far end (far
behind, never ahead), and a turnaround reverses direction. Merging can only
lower a count, never raise one.

## A traversal has to sweep the edge, not clip it

Speed will average any stretch it can measure, so `SPEED_MIN_PASS_M` is an
absolute floor. Counting needs a *fraction* of the edge instead
(`TRAVERSAL_MIN_COVER`), or a 25 m wobble at one end of a 2.3 km bridge
outvotes the ride that actually crossed it.

Both rules were added after the first version shipped **10 traversals for a
ride that crossed that bridge twice**. `tools/traversal_audit.py`'s top-20
list is what caught it, and the fix took the whole network's inflation from
1.013x to 1.009x.

## Merging a corridor: max within a direction, sum across the two

`merge._merge_ride_counts` combines a corridor's members by taking the max
within each direction and summing the two. The two mistakes available here
differ in direction and nothing else:

- A pass drifting from a street to its parallel bike lane is **one direction
  recorded twice** -- max holds it at one.
- An out-and-back riding the lane north and the street south is **each
  direction recorded once** -- the sum is two.

With every pass running one way this reduces to the plain max rule that
shipped first, which read a 99%-retraced ride as 16% repeated.

Either feature's stored vertex order is arbitrary (~9.5% of geometries run
max->min), so every merge site resolves a flip with `_opposed` before taking
the max. Without it, one physical pass on two oppositely-stored members reads
as an out-and-back.

`tools/traversal_audit.py --merge` runs the whole merge under all three rules
and reports how far apart they land.

## Reading the audit

The audit prints an inflation ratio -- a few percent over 1.0 is what real
riding looks like -- and the highest-multiplicity (ride, edge) pairs.

**A long edge near the top of that list is the alarm.** Genuine repeats are
short: a block ridden three times, a park lap. Sweeping a 2 km edge four
times is a rare thing to do; fragmenting one is not. Check the suspects
against the raw CSV before believing them, and reproduce the ride's own edge
set (`state["edge_rides"]`) rather than indexing the whole graph -- index the
graph and the fragmentation changes under you.

## Measuring an under-count needs a corridor-aware oracle

"Fraction of a ride's fixes within 25 m of an earlier part of the same ride"
finds the retraced rides, but as a per-corridor truth it overstates: the two
ways of one street are ~15 m apart, so a leg on one counts as a visit to the
other.

Before calling a corridor under-counted, check whether a *different* drawn
feature within 25 m carries the same ride. If it does, the second leg is
already on the map as its own way ridden once, and nothing is missing. Every
residual on the five most-retraced rides turned out to be exactly that.

## Regression oracle

A synthetic grid cannot catch a direction bug -- grid orientation is uniform.
The real-data oracle is the Manhattan Bridge
(`edge_geom[(1371803831, 7480410407)]`), which must appear **twice** in the
speed ranking, SE and NW, at roughly 5 mph each. Collapsing to one row means
the sign-change split broke; a single direction means orientation broke.
