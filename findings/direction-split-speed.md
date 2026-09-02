# Direction-Split Speed

Every ride CSV carries a per-fix timestamp, so the same traces that produce
the frequency map also say how fast each stretch was ridden — and, because
the trace is time-ordered, which way you were going. The stats panel ranks
the corridors where that gap is largest, over all rides back to 2021; no new
data source is involved.

Direction matters more than it sounds. Averaging a street's speed without it
blends the climb into the descent and produces a plausible number that means
nothing. Split by direction, the Manhattan Bridge bike path reconstructs its
own elevation profile from timestamps alone:

```
                          southbound   northbound
Brooklyn approach              16.3         7.9 mph
                               16.0         8.1
mid-span                       10.7         9.2
                                9.4        10.9   <- crest
                                7.8        14.2
Manhattan approach              8.6        15.5
```

The gap swings from -8.5 mph to +6.8 mph across the span, while the
*whole-span* average shows nothing at all (10.6 vs 10.8 mph). Nothing in the
pipeline knows about elevation, or that this edge is a bridge: the split
point was found purely from where the sign of the timing asymmetry changed.

**Why a list and not a map layer.** Colouring the network by this was tried
and dropped. Only 6.5% of features are ridden often enough in *both*
directions to compare them, so the map was 94% grey, and the half that was
coloured differed by under 2 mph — invisible on any ramp. The signal is
real (adjacent stretches agree on sign 82% of the time, and 100% once the
gap exceeds 2 mph), but it is concentrated on about eight places, which is a
list rather than a map. The ranking costs ~0.5 KB of payload; the layer cost
88 KB.

Implementation notes: speeds are `distance / time` accumulated per direction
(never a mean of per-point speeds), so stops are handled correctly. Long ways
are measured in ~150 m chunks — the bridge is a single 2163 m OSM edge, and
measured whole it averages its own climb against its descent. A corridor is
split wherever the faster direction flips, so a bridge is listed as its two
descents; ranked stretches need 250 m and three passes each way.

This is backfilled outside the map-matching config hash, so it can be
recomputed (bump `SPEED_VERSION`) without reprocessing any rides.

The implementation lives in `bike_routes/edge_speed.py`; its parameters
(`SPEED_VERSION`, `SPEED_CHUNK_M`, `SPEED_SNAP_M`, `SPEED_SPLIT_PASSES`,
`SPEED_CORRIDOR_N`) are in `bike_routes/config.py`.
