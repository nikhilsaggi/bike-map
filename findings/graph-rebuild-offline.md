# Rebuilding the Graph Without Overpass

The graph cache is version-bound: `cache._graph_cache_valid()` discards it
when osmnx or networkx changes underneath it. Recovering from that needs a
refetch, and a refetch needs Overpass — one volunteer-run service, with no
second source wired in. On 2026-09-09 `overpass-api.de` refused every
connection for over five hours, which left a machine with an invalidated
graph, 1,379 matched rides in state, and no way to draw any of them.

The graph was rebuilt from disk instead, with no network at all. The
responses were already there.

## Why not an extract

The obvious answer is a Geofabrik `.osm.pbf` of New York State: no live API,
verifiable by published checksum, immune to anyone's outage. It was rejected
for a specific reason.

`state["edge_rides"]` is keyed on **post-simplification node ids**, and which
nodes survive `simplify_graph` depends on which ways the network filter let
in. The pipeline composes three separately-filtered networks
(`NETWORK_TYPES`), and osmnx's filters are Overpass regex negations:

```
bike:  ["highway"!~"...|footway|motor|...|steps"]["bicycle"!~"no"]
drive: ["highway"!~"...|cycleway|path|pedestrian|service|track"]["motor_vehicle"!~"no"]
walk:  ["highway"!~"...|cycleway|motor|..."]["foot"!~"no"]["sidewalk"!~"separate"]
```

There is no osmium equivalent of those, and `ox.graph_from_xml` takes no
`network_type` at all. Reproducing them by hand risks a graph whose ids do
not match the ones in state — which turns a graph rebuild into a rematch of
every ride. An extract is the right answer for a *first* fetch on a machine
that has never run the pipeline; it is the wrong answer for restoring a graph
that existing state is keyed against.

## What is on disk already

osmnx caches every Overpass response it receives under `cache/<40 hex>.json`,
and `graph_from_polygon` assembles its own chunked queries by handing a
**list** of responses to `_create_graph`. Cached responses go in the same way.

They are the output of osmnx's own filters, so they carry exactly the ways a
live fetch would have carried, and simplification lands on the same ids. The
matching in state survives.

Two properties make the rebuild better than the fetch it replaces rather than
merely equal to it:

- **The responses accumulate across every region ever fetched.** A corridor
  pulled for one long ride is still on disk after a later run narrowed the
  region. On the day, the union covered the whole city *and* a corridor north
  to Poughkeepsie that no single fetch had ever held together.
- **They cost nothing to reuse.** The rebuild is CPU and disk only.

The limits are real: it cannot see OSM edits newer than the newest cached
response, and it can only cover ground some past fetch asked for. It is a
recovery path, not a substitute for `python -m bike_routes`.

## Telling the responses apart

A response records no query, so the network type has to be read out of the
data. Each filter admits exactly one `highway` value the other two exclude,
which makes those values unambiguous markers:

| marker | admitted by | excluded by |
|---|---|---|
| `highway=footway` | walk | bike, drive |
| `highway=motorway` | drive | bike and walk, via the substring `motor` |
| `highway=cycleway` | bike | drive, walk |

Match the whole key/value pair. Counting bare `"cycleway"` reads
`cycleway:right=lane` — a tag on an ordinary road — as evidence of a bike
response, and misfiled most of the drive set on the first attempt. Counting
`path` and `pedestrian` as walk markers fails the same way in the other
direction: the bike filter admits both.

Every response in the real cache carries exactly one marker type, which is
the check that the reading is sound: a drive response *cannot* contain
`highway=cycleway`.

## Re-fetches are not distinct responses

The same query re-issued across several runs leaves several byte-identical
copies. Fingerprinting them by size plus the head of the file finds no
duplicates at all, because every response opens with an
`osm3s.timestamp_osm_base` that differs per fetch. Hash slices from the
**body** instead. On the real cache that collapsed 71 responses to 52.

## Result

Rebuilt offline, the three networks came out within 0.8% of the same day's
live fetch for the region they shared (bike 223,183 nodes against 221,497),
and the merged graph covered ground the live fetch could not reach. The
export returned to 16,481 drawn features from a broken 4,631, with every
borough within 0.7% of the last known-good version — the agreement that
confirms the node ids matched, since those counts only resolve if 1,379
rides' stored matches land on freshly rebuilt geometry.

Run it with `python tools/rebuild_graph_from_cache.py --write`, then delete
`cache/render_cache.pkl`: the no-new-rides path reads that cache before it
ever consults the graph, so a stale one silently suppresses the rebuild.
