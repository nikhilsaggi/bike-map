# Citibike trips: a second data source that is not a trace

The map is built from GPS traces of one bike. A Lyft/Citibike account export
adds a year of riding the map could not see, but it is a different kind of
evidence and most of this write-up is about keeping it from pretending
otherwise.

Source: `citibikenyc_history_2026-09-05.json`, downloaded by hand.
2,804 records, **2,524 unique rides**, 2021-09-04 to 2026-09-04.

### The first export was silently truncated

An earlier download covered only 2025-08-27 onward — 372 days ending on the
export date — and looked at a glance like a complete history that simply
started in 2025. It was not. The tell was in the duplicates: they sat at
index 10, 20, 30 … 380, each repeating the record before it, which is a
cursor-paginated fetch with a page size of 10 re-sending its cursor. 39 pages
of 10, and **the last page was full** — a fetch that has run out of history
stops on a short page, so this one had stopped early. The complete export
ends on a page of 4.

The cause turned out to be in the fetch script rather than the API: the
[baywheels console script](https://github.com/fhoffa/code_snippets/blob/master/baywheels/readme.md)
pages backwards from today and **stops at a one-year cutoff**. So the two
supporting signs were exactly what that produces — a rolling ~1 year ending
on the export date, and an oldest month holding 2 rides against 29 and 47 in
the months after it, a boundary slicing through a month rather than a
beginning.

Because `ingest.citibike` replaced the cache rather than merging it, a
default one-year pull would have silently discarded the five years already
there. It now merges on `rideId` and prints what each file added, so a
one-year pull is a top-up (the first item in
[issue #23](https://github.com/nikhilsaggi/bike-map/issues/23); the rest of
that issue is about automating the pull itself).

The cache that already existed predates the id, so its 2,524 trips carry
none. `_merge` matches those by start time instead — no two of them share
one — and folding a re-derived one-year window back into that cache adds 0
trips and loses 0, which is the check that mattered before shipping it.

## What is actually in the export

Each record is `rideId`, `startTimeMs`, `endTimeMs`, `duration`,
`rideableName`, `startAddress`, `endAddress`, `price` and `lineItems`. Dock
names and timestamps. No coordinates, no trace, no route.

Three properties of the raw file drove the design:

- **38 records are exact duplicates.** Whole records, repeated. `rideId` is
  the identity, so `ingest.citibike` dedupes on it and reports the count.
- **`endTimeMs == startTimeMs + duration`, and every `duration` is a whole
  number of minutes.** The end time is derived, not measured.
- **`rideableName` is not a bike type.** Ids come in two shapes, hyphenated
  (`812-7417`) and five-digit (`16825`), and it is tempting to read that as
  ebike against classic. It is not: **26 ebike line items sit on
  five-digit bikes**, and the hyphenated share climbs steadily by year —
  41% of trips in 2021, 69%, 86%, 89%, 93%, 90% — which is a fleet being
  replaced, not a change in what was ridden. Ebike is knowable only from a
  line item naming one: **189 of 2,524 trips, 7%, and a floor**, because a
  free ebike ride can carry no such item. The panel says "at least".

## Why there is no speed

The obvious thing to compute from a start dock, an end dock and a duration is
speed, and it is wrong to.

Duration is quantised to whole minutes and the median trip is 8 minutes, so
the timing alone is ±6%. The distance is worse: the actual path is unknown,
so any distance is an assumption about routing. Multiplying an assumed
distance by a quantised time produces a number that would sit in the same
panel as `edge_speed`'s direction-split corridors, which are measured from
1 Hz GPS fixes on known geometry. They would read as the same kind of number
and are not, and there is no honest label short enough to fix that.

## Where the dock coordinates come from

The export has no coordinates. Citibike's public GBFS feed
(`https://gbfs.citibikenyc.com/gbfs/en/station_information.json`, keyless,
~2,506 stations) publishes a `name` field that is the same string Lyft writes
into `startAddress`. The match is exact, not fuzzy:

**552 of 591 distinct dock names** over five years. The 39 misses are docks
that have genuinely been removed since 2021, which is what a five-year window
should look like.

No geocoder and no fuzzy matching. There is one normalisation step, added
only because the fuller history produced the evidence for it: Lyft's own
strings are inconsistent in two specific ways, and five docks were being lost
to formatting rather than to history.

- Three had stray whitespace around the ampersand — a literal tab in
  `Broadway\t& W 48 St` and `W 34 St &\tHudson Blvd E`, a double space in
  `W 48 St &  Rockefeller Plaza`.
- Two abbreviated the street type: `Madison Av & E 51 St`,
  `Manhattan Av & Leonard St`.

So `_norm_name` collapses whitespace and expands a standalone `Av` to `Ave`,
and matching stays exact against the normalised key. This was checked before
being added rather than after: across all 2,506 GBFS stations **no two names
normalise to the same key**, so the rule cannot attach a dock to the wrong
coordinates. The ingest still prints every name it could not place, because a
match rate that drops is a real signal about the feed.

**A dock GBFS cannot place keeps its trips and its counts.** It simply gets
`at: null` and is not drawn. Dropping it would quietly shrink the dock count
and the totals in order to tidy the map — and over five years that would be
39 docks' worth of history erased for the convenience of the renderer.

## Why none of it reaches the drawn edges

`edge_counts`, `edge_traversals`, `edge_rides` and `coverage.pct` all mean
"a GPS trace was matched here". A dock-to-dock trip cannot honour that. So
the Citibike data lives entirely in a top-level `properties.citibike` block:
no state key, no key in `cache._processing_config()`, nothing in `features[]`,
and no pipeline stage. It is the same shape `weather.py` already uses.

The regression check after any change to this is:

```
python -c "import gzip,json; p=json.load(gzip.open('docs/rides.geojson.gz'))['properties']; \
  print(p['total_rides'], p['total_km'], p['coverage']['pct'])"
```

which must still print `1380 1429.8 5.1`.

## What is drawn, and what is not

**Routed corridors: built, measured, rejected.** The tempting version is to
route each dock pair over the OSM bike network and draw the result. All 214
placeable docks snapped to the cached graph and all 253 OD pairs routed by
shortest path in **2.3 seconds**:

| | |
|---|---|
| distinct edges routed | 3,571 |
| unique km | 277.4 |
| traversal km | 743.0 |
| edges already on the map | 2,158 (60.4%) |
| edges never ridden on the own bike | 1,413 (39.6%), 117.3 km |

117 km of streets the GPS map has never covered is a genuinely interesting
number — about +12% on the 978 km ridden. Rejected anyway, and this one is
not a judgement call: the shortest path is weakest exactly where the number
is most interesting. A street that appears only in that layer appears because
an algorithm chose it, not because anyone rode it. Drawing it in the same
style as a measured edge makes a guess look like a trace; drawing it in a
different style still puts 117 km of invented geometry on a map whose whole
claim is that every line was ridden.

**Dock markers: shipped.** Each placeable dock is a marker sized by how much
it was used *within the date range currently on screen*, so the slider and the
time-lapse move the docks the way they move the streets. Clicking one draws
straight lines to the docks its trips actually reached, in range, and lists
them with per-direction counts.

Everything drawn is measured. A straight line drawn only for the dock a reader
clicked is answering a question they asked, not asserting a shape over the
city — which is what made the same lines wrong as a permanent all-pairs layer.

**The recorded route behind a row: shipped.** A trip whose clock a GPS ride
runs over cites that ride, and its popup row offers it -- the page's own
single-ride view, in cyan, over the straight line that still stands in for
the trip. This is the opposite of the rejected layer above rather than a
softer version of it: nothing is routed, the drawn line is a trace that was
recorded, and it appears only for the pair a reader asked about.

The link was already computed and thrown away. `citibike.ride_sources` matches
every ride to the trips it overlaps in time, and only the count survived into
the export. `citibike.trip_rides` asks the same question from the trip's side,
and the answer ships as the fourth element of each row in
`properties.citibike.trips`. Measured on the 2026-09-05 export (2,524 trips,
1,380 rides):

| | |
|---|---|
| trips with at least one GPS ride over them | 1,435 (57%) |
| popup rows (unordered dock pairs) | 1,312 |
| rows with at least one recorded trip | 977 (74%) |
| rows with more than one | 168 (max 31) |
| GPS rides covering >1 trip | 153 of 1,265 matched |
| recorded trips inside such a recording | 327 (23%) |

Two consequences the row has to carry rather than hide. Nearly a quarter of
recorded trips sit inside a recording that covers several of them, and ride
view draws the whole recording -- so the row says so ("whole recording -- it
also covers 1 other Citibike trip") instead of letting the extra legs read as
part of the clicked hop. Clipping the trace to the trip's own clock window is
the only real fix and it is a different project: nothing in `state` carries a
timestamp per (edge, ride), `edge_speed` folds each chunk to
`[dist, time, moving, n]` with no absolute clock, and `edge_rides` is a set.

Where a pair has several recordings the row cycles through them, newest first,
and one more click puts the map back. That is why showing one does not close
the popup the way a street popup's ride row does: the rows are how a reader
walks the network, and losing them to see a route would trade the layer's
whole point for one answer.

Colour is deliberately doing no work: docks are a cool pale tone outside the
plasma ramp, size carries volume, and cyan is reserved for selection, the way
the rest of the page already uses it. An earlier attempt put a diverging ramp
on each dock's one-way share; it competed with the plasma ramp for a variable
that needed a legend to decode.

### The detour worth recording

The dock layer was removed once, on the reasoning that a ranked five-row list
of the most one-way docks "says it better" than a map encoding. That was the
wrong test, and it is worth writing down because it is an easy mistake to
repeat.

This map exists to be explored. A list states one finding its author already
picked; a layer that filters with the slider and expands on click lets a
reader find things nobody ranked — that the docks only appear in the last year
of the time-lapse, that one dock reaches 87 others, that a dock's partners
change completely between 2025 and 2026. Efficiency of communication is the
criterion for a report. It is the wrong criterion here.

This paragraph used to lead with "that Citibike trips cluster where the ride
heatmap is thin", and that example was never checked. Cut into
neighborhoods it is false: every one of the 64 neighborhoods with a dock
endpoint also has ridden street, and dock volume correlates *positively* with
coverage (Spearman +0.71) and with ridden kilometres (+0.79) — the busiest
dock neighborhoods are the best-covered ones
([details](neighborhoods.md#the-citibike-claim-does-not-survive-at-this-resolution)).
If the effect is real it is within a neighborhood rather than between them,
and nothing here has measured it. The point about layers stands; the example
was an assertion dressed as one.

What survived from that detour is the honesty rule, which is separate: no
routes between docks, nothing in `edge_counts` or `coverage`, and no speed.

## Which GPS rides were Citibike rides

The two datasets share nothing but the clock: the matcher never saw a dock and
the export never saw a fix. That turns out to be enough, because a Garmin
activity running while a Citibike is unlocked *is* that Citibike ride.

`citibike.ride_sources` overlaps each ride's `[start, start + duration]`
against every trip span. On the real data:

| | |
|---|---|
| GPS rides inside the export's window | 289 of 1,380 |
| matched to at least one Citibike trip | **225 (78%)** |
| in the window with no trip over them | 64 |
| rides spanning 2 trips | 21 |
| rides spanning 3 trips | 3 |
| Citibike trips with no GPS ride over them | 98 of 352 |

**The threshold is not tuned, and that is the point.** Requiring 1s, 30s, 60s
or 120s of overlap gives the identical 225/64 split; only at 300s does it
break, and that is because the median trip is 8 minutes long, not because
anything is marginal. A match covers a median 92% of the trip it hits, and
just 2% of matches cover under half. 60s is the shipped figure — enough that a
ride merely abutting a trip cannot claim it, and provably insensitive.
Nothing here needs a `traversal_audit`-style calibration.

Alignment is what you would expect from a person: the Citibike unlock lands a
median 18 seconds before the watch starts recording (p10 −80s, p90 −1s). You
unlock, then start the watch.

### Unknown is a third state, not a synonym for "own bike"

A ride with no matching trip means something only *inside* the window. Before
the export begins there is no evidence either way, so those rides are
`-1`, not `0`. With the current truncated export that is 1,091 of 1,380
rides — and it corrects itself when a fuller export lands. The page's source
filter hides unknown rides from both sides and says how many it is hiding,
rather than quietly filing them under one.

### What the split shows

The two filtered maps are not the same map with different density. Citibike
rides draw 5,448 edges; the 64 own-bike rides draw 8,804. Fewer rides, more
street: the Citibike trips are short dock-to-dock hops that saturate the local
grid, while the own-bike rides are long and reach out to Queens and across the
bridges. Neither of those is a fact the stats panel states — it is what the
filter is for.

## What the panel shows

Two columns, Citibike against own bike, over the whole history:

| | Citibike | Own bike |
|---|---|---|
| Trips | 2,524 | 115 |
| Time | 510 h | 160 h |
| Days out | 989 | 73 |
| Typical | 8 min | 32 min |

The Citibike column is the export's own totals — every trip, including the
1,259 no GPS ride was recorded over. The own-bike column is the rides
`ride_sources` found no trip under. Both are complete records of their own
kind, and "trips" counts honestly in both: one GPS recording can hold several
Citibike trips, but never several own-bike ones.

The four-times-longer typical ride is the shape of the whole thing. Citibike
is a way of getting somewhere; the own bike is the ride itself.

### The one-way dock ranking, measured and dropped

The panel used to lead with the most one-way docks — the docks only ever
departed from or arrived at. Two rounds of work went into ranking them
correctly, and the whole thing came out anyway because the numbers were not
worth the space.

Worth recording, because the ranking bug is a general one. Ranked on the raw
difference between departures and arrivals, the top "one-way" dock over five
years was `Broadway & W 48 St` at +93 — which sounds decisive until you see
it is 363 out against 270 in, a 15% lean that only led because the dock is
busy. Difference is scale-dependent; **share** is the statistic, above a
floor that stops a dock used twice from claiming a perfect score. On that
basis the real answers were `Madison Av & E 51 St` at 28/2 and
`Lispenard St & Broadway` at 1/14.

The same trap had already been sprung once on the dock markers' colour,
which encodes the same quantity. Fixing one and not the other is how the two
drifted apart until five years of data made it obvious.

## What the numbers say

- **Docks near the Midtown terminals are one-way.** `Madison Av & E 51 St`
  is 28 departures against 2 arrivals, `Park Ave & E 42 St` 22 against 3.
  A matched edge has a direction but no origin, so this is a fact about
  trips the ride map cannot express — interesting enough to have measured,
  not interesting enough to have kept in the panel.
- **It is a supplement, not a substitute.** 737 of 989 Citibike days were
  days with an own-bike ride too.
- 118 of 216 docks were used exactly once, against `Montrose Ave & Bushwick
  Ave` at 189 endpoint touches — home, and reaching 87 other docks.
- 253 of 2,225 bikes were ridden more than once — but 198 of the 293
  re-encounters happen on the same day, which is a round trip on a bike that
  was still where it was left rather than meeting one again. Whether the
  remainder beats chance is [issue #21](https://github.com/nikhilsaggi/bike-map/issues/21).

## What was measured and deliberately dropped

**Fast facts that did not earn their line.** The panel briefly carried two
more: 88 unlocks re-docked within two minutes, and $438.21 paid against
$588.91 charged. The first is a fact about bad bikes, not about riding; the
second needs a paragraph about credits and memberships before it means
anything. Both were cut. The per-trip fare fields stay in
`cache/citibike_trips.json` — that file is the normalised copy of an export
that lives outside the repo, so throwing away what it recorded is not the
same as declining to render it.


**Hour and weekday profiles.** Both were computed against the own-bike
equivalents in `properties.riding` and both are near-identical: peak hour 18
in each (15.1% of own-bike rides, 14.5% of Citibike trips), weekend-heavy in
each (Saturday highest in both). There is no contrast to draw, and a chart
whose message is "these are the same" costs a reader more than it tells them.
The geography is where the two sources differ; the clock is not.
