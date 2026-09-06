# Meeting the same Citibike twice

The stats panel used to end with a fact that felt like a small miracle:
**253 of 2,225 bikes ridden more than once**, over five years and 2,518
unlocks. New York runs tens of thousands of bikes. Meeting 253 of them again
sounds like the city is smaller than it looks.

It is not. Two hundred of those repeats are not meetings at all, and the
ninety-three that are happen at exactly the rate chance predicts.

Reproduce all of it with `python tools/bike_reencounters.py`, which reads
`cache/citibike_trips.json` and writes nothing.

## Two hundred of them are round trips

Sort every trip by time and walk it. When the same bike id comes up twice in
a row, ask a question the headline never asked: **where was that bike in
between?**

| | |
|---|---|
| trips carrying a bike id | 2,518 |
| distinct bikes | 2,225 |
| ridden more than once | 253 |
| consecutive repeat pairs | 293 |
| … where the earlier trip *ended at the dock the later one starts from* | **200** |
| … where the bike had moved | **93** |

The 200 are round trips. You rode somewhere, docked, did the thing, and took
the same bike back — or left it and picked it up again the next morning. The
bike is where it is because you put it there. Nothing was met.

The remaining 93 are the real question, and they look nothing like the 200:

| | q25 | median | q75 |
|---|---|---|---|
| days since I last rode it | 112 | **306** | 662 |
| km from where I left it | 0.89 | **3.17** | 5.43 |

Ten months and three kilometres. Seven of the 93 were picked up at the very
dock they were left at, months later — coincidence wearing the costume of the
other 200.

**The split is not a threshold that needed tuning.** "The bike was left at
this dock within 48 hours" is the rule, and 48 hours is only there so an
overnight park is not cut in half by midnight. Every setting from 2 hours to
30 days gives the same answer:

| window | round trips | re-encounters |
|---|---|---|
| 2h | 190 | 103 |
| 24h | 198 | 95 |
| **48h** | **200** | **93** |
| 168h | 200 | 93 |
| 720h | 202 | 91 |

Same-day would have worked too — all 193 same-day repeats are round trips,
without exception. Not one of them started anywhere but where the previous
trip ended.

## Is 93 more than chance?

The honest answer is that this question cannot be asked the way it is usually
phrased, because it needs a number nobody here has. "Chance" means a uniform
draw from the fleet, and Citi Bike's fleet grew from roughly 25,000 bikes to
around 40,000 across these five years, with a
[GBFS](https://gbfs.citibikenyc.com/gbfs/en/station_status.json) snapshot on
2026-09-05 showing 34,239 docked at one instant — itself a lower bound, since
bikes out on a trip are in neither count. Any single N is wrong, and picking
one decides the answer.

So invert it. Count the exposure the data actually earned — at every unlock,
how many bikes had already been ridden — and divide by the re-encounters. That
is the pool size the observations imply, with no assumption at all:

| bikes ridden within | exposure | met | implied pool | 95% CI |
|---|---|---|---|---|
| 60 days | 219,068 | 17 | 12,886 | 8,734 – 24,563 |
| 90 days | 321,924 | 18 | 17,885 | 12,233 – 33,241 |
| 180 days | 614,096 | 30 | **20,470** | 15,075 – 31,877 |
| 365 days | 1,180,227 | 52 | 22,697 | 17,846 – 31,168 |
| 730 days | 1,998,417 | 72 | 27,756 | 22,548 – 36,093 |
| any time | 2,614,650 | 93 | 28,115 | 23,366 – 35,286 |

Read the top rows. The bottom one counts every bike ever ridden as though the
system still ran it, and the steady climb down the column is that fiction
arriving: a bike retired in 2023 inflates the exposure without ever being
available to meet.

The short windows land on **13,000–20,000**. The GBFS snapshot puts 18,823
classic bikes in the system against 15,416 electric, and 93% of these trips
carry no ebike line item.

**So the pool is the classic fleet, and 93 re-encounters is what a uniform
draw from it produces.** The surplus in the headline was the 200 round trips.
What is left is coincidence, at the rate coincidence runs.

## What the bike remembers, and what it does not

Both results above lean on a model. These two do not. Hold the 93
re-encounters fixed and ask what was special about the bike that turned up,
against the bikes that could equally have turned up instead — every bike
ridden by that date, aged and placed as of that same moment. No fleet size
enters, and neither does any threshold.

**Space: nothing.** Is the bike you meet again nearer to where you left it
than a random bike you had ridden by then?

|  | median |
|---|---|
| observed | 3.17 km |
| null | 3.17 km (95% band 2.33 – 3.96) |
| one-sided p | **0.507** |

Not nearer. Not further. Identical. Rebalancing mixes the fleet so completely
that where you parked a bike tells you nothing whatever about where you will
find it again — which answers the question the issue opened with, that only
13% of re-encounters start at the dock the bike was left at. Among the genuine
93 it is 7, and the missing 86 come from nowhere in particular: **a bike has
no address.**

**Time: something.** Is it one you rode more recently than the others?

|  | median |
|---|---|
| observed | 306 days |
| null | 428 days (95% band 342 – 518) |
| one-sided p | **0.003** |

Yes. Bikes you rode long ago are underrepresented among the ones you meet
again, because they are gone — retired, rebuilt, or renumbered. That is the
same fleet turnover the id formats show from the other side, hyphenated ids
running 41% of trips in 2021 and 90% in 2026
([details](citibike-trips.md)). It is also the reason the implied-pool table
has to be read at the top: this test is the direct measurement of the bias
that inflates its bottom row.

A bike carries a lifetime. It does not carry a neighbourhood.

## The ebike trap

Only 1 of 173 ebike-flagged unlocks was a bike already ridden — 0.6%, against
4.3% for everything else. A seven-fold deficit, and it means nothing.

An ebike unlock can only re-encounter an ebike *already ridden*, and there
were an average of 90 of those, against 1,128 bikes of any kind. Do
the division: against the 15,416 electric bikes in the snapshot, 173 draws at
that exposure predict **1.0** re-encounters. Exactly one happened.

The deficit is the exposure. It would have appeared whether or not ebikes are
rebalanced differently, so it cannot be evidence that they are. (The flag is a
floor either way — a free ebike ride carries no line item naming one.)

## Where they happen

Sharing the 93 out in proportion to each dock's own exposure — which corrects
both for a busy dock and for a dock used late, when the list of bikes already
ridden was longer:

| dock | draws | met | expected | ratio |
|---|---|---|---|---|
| Broadway & W 48 St | 424 | 10 | 10.3 | 0.98 |
| E 43 St & Madison Ave | 255 | 8 | 11.5 | 0.70 |
| Clinton St & Grand St | 153 | 8 | 8.6 | 0.93 |
| Montrose Ave & Bushwick Ave | 115 | 12 | 8.2 | 1.47 |
| 8 Ave & W 49 St | 94 | 4 | 2.7 | 1.46 |

Bushwick runs half again its share and Midtown runs under. That is the shape
you would expect — a neighbourhood dock recirculating its own bikes, a
commuter dock flushed daily by the whole system — and 12 against 8.2 is not
enough events to claim it. It is the one thread here worth pulling if the
export ever doubles in size.

## What the panel says now

> 93 unlocks were on a bike ridden before

One number, and a different one. The old line was *253 of 2,225 bikes ridden
more than once*, which counted a person taking their own bike home as a
coincidence. This counts the coincidences.

The 200 round trips are not on the panel. They are the reason the number moved,
not a second finding — a reader does not need the subtraction shown to read the
result, and the line has a stats panel's worth of space, not a paragraph's. The
export still ships `resumes` next to `reencounters` with nothing drawing it, so
the 93 can be checked against what it was cut from.

Under it is the list the number opens up — one row per bike met again, 88 of
them, sorted by how many times the bike turned up:

```
Bike re-encounters  (?)
  420-4640   3×    300, 99 d
  878-6261   3×    969, 132 d
  405-4946   3×     40, 38 d
  266-5628   2×        648 d
  ...
```

Click a row and the GPS recordings made on that bike play through single-ride
view, newest first, stepped with ↑↓ — the same cycle a dock popup's route
link opens, and the same code. 78 of the 88 have at least one recording; 38
have two or more, so most of the list is a route you can actually walk.

**The count is encounters, not trips**, and getting that wrong was a real bug
in the first version. `266-5628` was ridden three times — but two of those
were one afternoon in February 2023, out to W 70 St and straight back, which
is a single occasion. The row claimed "ridden 3 times, 2 of them after it had
moved on"; the truth is two occasions 648 days apart. The list now folds a run
of round trips into the encounter that started it, dates that encounter by its
earliest trip, and shows the gaps between encounters rather than between legs.
The headline 93 was always right — it counts meetings — so the bug was in the
display alone, which is exactly the kind that survives a green test suite.

**A bike with no recording keeps its row.** Ten were met again on trips no
Garmin was running over, and they are dimmed with a tooltip saying why rather
than dropped — hiding them would shrink the list to suit the renderer, which
is the same mistake as dropping a dock GBFS cannot place. They keep their
place in the sort too: the list ranks on how often a bike turned up, not on
what happens to be clickable.

The **(?)** carries the definition, because "re-encounter" is a rule and not a
word a reader can be expected to infer: *bikes ridden more than once (picked
up either from a different dock or from the same dock 48+ hours later)*.
Putting the rule behind a hover was the only way to keep it on the page at
all; as a sentence it crowded out the list, and without it "re-encounter" is
just a word. The rows carry their own counts and click hints, so the tooltip
gives the definition and stops.

It states the rule as what *counts* rather than what is thrown out, which took
a rewrite to get to. Both branches stay in it even though **only one of them
ever fires**: no repeat in five years is "different dock, back within 48
hours". Every same-dock repeat is either inside 48h (200 of them) or 16 to 227
days later (7); every different-dock repeat is at least 41 days later (86). The
two populations do not overlap at all, which is why the split is so insensitive
to the window — there is nothing in the data between "same dock, same day" and
"somewhere else, over a month later". Describing only the branch that fires
would be describing a different rule, and would silently reclassify the first
bike that turns up across town the same afternoon.

This is a list where a chart used to be, and the swap is the point. The two
chance tests below were built, drawn, and taken off the panel:

### The chance-test chart, and why it came off

Three versions were built. The first two were wrong in ways that were
invisible until they were rendered, and worth recording for that alone.

**Band histograms.** The first attempt binned the 93 meetings by distance and
by age, observed share against null share, four bars each. It had to be thrown
away: 93 events over four bands is ~20 a band, and the sampling noise (±6
points) is larger than either effect. The distance chart would have shown a
lean the permutation test says is not there, and the age chart would have
understated one that is. **The median test has power the histogram does not.**

**A percentile axis.** The second put each dot at its rank among the shuffles
— elegant, since both rows then share one axis. But the middle 95% of a
percentile axis is 95% of the track *by construction*, so the band was the
whole chart and only a dot pinned to the very edge read as outside it. Worse,
inset the dot far enough to keep its ring on screen and a genuinely outside
result gets drawn *inside* the band.

**A zero-anchored axis.** The third plotted real units from zero. Honest, and
still unreadable: 306 days against a band starting at 338 is 6% of a 0–528
axis, four pixels on a 236px panel. Framing each row on its own band fixed it
— legitimate for a dot and not for a bar, because **a dot's position carries
its value, so the axis may start anywhere; a bar's length carries it, and a
cut baseline lies.**

The fourth version worked, and came off anyway. It stated a conclusion the
reader could only agree with; the chip list hands them 88 bikes and 119
recordings to look through. That is the same call the dock layer already
survived once — a ranked list "says it better" than a map encoding, and it is
still the wrong test for this project. The difference here is which artefact
is the ranked list: this time the *chart* was the thing that stated one
finding, and the list is the thing that can be explored.

Both tests still run in `tools/bike_reencounters.py`, which is where a result
that needs a p-value belongs.

## What was left alone

- **A published fleet size per year is not in the repo, and should not be.**
  It would let the tool print a p-value, and that p-value would be a
  restatement of whichever number was pasted in. The implied-pool table says
  the same thing and shows its working.
- **No re-encounter layer.** A re-encounter is a bike id matching across two
  dock-to-dock trips: it has no trace and no route. Drawing the 93 as lines
  from where a bike was left to where it turned up would be
  [the rejected routed layer](citibike-trips.md) with a smaller n — and worse
  than that, it would be drawing noise, because the space test says those
  displacements are indistinguishable from random. What a bike chip puts on
  the map is a recording that exists, which is the rule the dock rows already
  follow.
- **`bike` is assumed to be one physical bike for its whole life.** Nothing in
  the export confirms that, and a renumbered bike reads here as a retirement.
  It is part of what the time test measured.

## A defect this turned up

`ingest.citibike` normalises dock names only when asking GBFS for
coordinates; the trips cache keeps Lyft's raw spelling. Lyft writes the
busiest dock in this history two ways — `Broadway & W 48 St` and
`Broadway\t& W 48 St` — so `properties.citibike.docks` carries it as two
entries, 633 touches and 137, drawn as two markers stacked on one coordinate.
`W 34 St & Hudson Blvd E` is split the same way, 2 touches deep. Filed as
[issue #26](https://github.com/nikhilsaggi/bike-map/issues/26);
`tools/bike_reencounters.py` normalises on load in the meantime, because a
bike left under one spelling and unlocked under the other would count as a
re-encounter rather than the round trip it is.
