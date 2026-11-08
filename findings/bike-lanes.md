# Bike lanes

The bike lane layer draws NYC DOT's
[New York City Bike Routes](https://data.cityofnewyork.us/dataset/New-York-City-Bike-Routes/mzxg-pwib)
under the rides. This is how it is built, and what was measured and left out.

## The dataset keeps its own history

Each row is one bike facility on one segment of the city's street map, LION.
An upgrade does not edit the row: it sets the old row's `ret_date` and starts
a new row whose `prevbikeid` names the old one. 93% of retired rows were
superseded that way; the rest were removed outright. So the network on any day
is every row installed by then and not yet retired, and no archive of past
snapshots is needed.

Two cautions:

- Install dates before 2021 are often placeholders (45% fall on the 1st of a
  month). From 2021, when these rides start, that drops to 6%.
- Only about half of replacement rows start on the exact day their
  predecessor ended. The rest leave a gap or an overlap, so the page decides
  what is standing on a date from each row's own dates, never by walking the
  chain.

The export keeps only rows standing at some point during the rides. Older rows
are kept, but not drawn, when a later row descends from them, so a lane's panel
can say what it replaced.

## Matching a lane to the ridden streets

DOT draws lanes on the street centreline. OSM often draws a protected lane or
a greenway as its own way beside the street. A lane point counts as ridden by
a ridden edge within **12 m** of it that runs within **25°** of parallel, in
either direction. The distance reaches a cycleway drawn beside the centreline,
and the angle keeps a cross street from claiming the avenue it meets.

The match has to be made per point, not per edge. An earlier version required
each DOT row to cover half of an OSM edge. Long single edges, like the 2 km
Williamsburg Bridge path or stretches of the Hudson River Greenway, are covered
by several short DOT rows, none of which reached that bar. Those paths, the
most-ridden lanes there are, read as unlaned.

## What "ridden" means for a lane

A lane is ridden from the date by which **half its length** had been ridden
**while the lane existed**. Half, because clipping a corner on a turn is not
riding a lane. While it existed, because a street ridden before its lane was
painted was not ridden on that lane. The page applies this as a running total
up to the slider's later handle, the same way it fills the neighborhoods.

At the time of writing, this put about 44% of the current lane network as
ridden: about half of protected miles, and fewer painted and shared ones.

## How much of the riding is on a lane

The Years table and the Riding section give the share of riding inside NYC
that was on a lane standing on the day of the ride. This takes the opposite
match: for each ridden edge, the lane records running along it. An edge counts
as on a lane when the records standing on the ride's date cover half of it
between them. It counts as protected when class I records alone do. Each pass
is charged the edge's length. That overstates a total, which is why the
neighborhood distances do not use it, but the error mostly cancels in a share.
Riding outside every NYC neighborhood counts on neither side, because DOT maps
nothing there.

At the time of writing it was about two thirds on a lane and 44% protected
over all years. The protected share falls from about two thirds in 2021 to
under a third in 2026. That is the riding moving off the Hudson River
Greenway, the Central Park drives and the Midtown avenues onto Brooklyn
streets, not lanes disappearing.

## Rejected: "new lanes changed the routes"

Before any lane was drawn, the obvious question was whether a new lane pulled
the riding onto its street. For each lane built or upgraded from 2021 to 2025,
the street's share of all riding was compared in the year before and the year
after its install date. The answer was no:

- **Real lanes:** the median share rose 1.4×, and 60% of lanes grew.
- **Placebo:** unchanged lanes at random dates gave 1.22× and 56%.

A handful of streets did jump sharply after their lanes went in: Grand St
went from 2 rides to 56, and McGuinness Blvd from 2 to 10. The rider's own
reading is that each of these reflects a change of home or routine, not the
lane, with 7th Ave the only plausible exception. One rider's routes move for reasons a
lane dataset cannot see, so the layer shows where the lanes were and which of
them were ridden, and makes no claim about cause.
