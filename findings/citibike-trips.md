# Citibike trips: a second data source that is not a trace

The map is built from GPS traces of one bike. A Lyft/Citibike account export
adds a year of riding the map could not see, but it is a different kind of
evidence and most of this write-up is about keeping it from pretending
otherwise.

Source: `citibikenyc_history_2026-09-04.json`, downloaded by hand from
`account.lyft.com/privacy/data`. 390 records, **352 unique rides**,
2025-08-27 to 2026-09-04.

## What is actually in the export

Each record is `rideId`, `startTimeMs`, `endTimeMs`, `duration`,
`rideableName`, `startAddress`, `endAddress`, `price` and `lineItems`. Dock
names and timestamps. No coordinates, no trace, no route.

Three properties of the raw file drove the design:

- **38 records are exact duplicates.** Whole records, repeated. `rideId` is
  the identity, so `ingest.citibike` dedupes on it and reports the count.
- **`endTimeMs == startTimeMs + duration`, and every `duration` is a whole
  number of minutes.** The end time is derived, not measured.
- **`rideableName` is not a bike type.** 323 of 352 are hyphenated
  (`812-7417`) and 29 are five-digit (`16825`), but the five-digit ones are
  spread evenly across all 13 months, so the split is fleet generation, not
  propulsion. Ebike is only knowable from a line item naming one, which gives
  **27 rides as a floor** — a free ebike ride can carry no such item. The
  block reports it as "at least".

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

- **214 of 216 distinct dock names**, 702 of 704 endpoint mentions.
- The two misses are `Melrose St & Broadway` and `9 Ave & W 39 St`, one trip
  each — renamed or removed docks the live feed no longer lists.

No geocoder, no string normalisation, no nearest-neighbour fallback. If that
match rate ever drops it is a real signal about the feed, not something to
paper over, so the ingest prints every name it could not place.

**A dock GBFS cannot place keeps its trips and its counts.** It simply gets
`at: null` and is not drawn. Dropping it would quietly shrink the dock count
and the totals in order to tidy the map.

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
reader find things nobody ranked — that Citibike trips cluster where the
ride heatmap is thin, that the docks only appear in the last year of the
time-lapse, that one dock reaches 87 others. Efficiency of communication is
the criterion for a report. It is the wrong criterion here.

What survived from that detour is the honesty rule, which is separate: no
routes between docks, nothing in `edge_counts` or `coverage`, and no speed.

## What the numbers say

- **Grand Central is a one-way valve.** `E 43 St & Madison Ave` is 33
  departures against 9 arrivals; `Park Ave & E 42 St` is 10 and 0. 43 out, 9
  in. This is the finding that justified the feature: a matched edge has a
  direction but no origin, so the ride map has no way to express "a place I
  only ever leave from".
- **It is a supplement, not a substitute.** 140 of 172 Citibike days were
  days with an own-bike ride too. These are the one-way legs of days that
  otherwise went on the owner's own bike.
- 118 of 216 docks were used exactly once, against `Montrose Ave & Bushwick
  Ave` at 189 endpoint touches — home, and reaching 87 other docks.
- 8 trips started and ended at the same dock within one minute: a bad bike
  unlocked and immediately re-docked.
- 32 of 318 bikes were ridden more than once. Three were ridden three times,
  each within a single day. `870-0494` came round again on 2026-08-18, 357
  days after 2025-08-27.
- $41.31 charged, $26.68 credited, $14.63 actually paid across 4 trips.

## What was measured and deliberately dropped

**Hour and weekday profiles.** Both were computed against the own-bike
equivalents in `properties.riding` and both are near-identical: peak hour 18
in each (15.1% of own-bike rides, 14.5% of Citibike trips), weekend-heavy in
each (Saturday highest in both). There is no contrast to draw, and a chart
whose message is "these are the same" costs a reader more than it tells them.
The geography is where the two sources differ; the clock is not.
