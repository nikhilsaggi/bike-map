# CLAUDE.md

NYC bike route visualization: an incremental pipeline that map-matches personal
GPS rides (CSV in `rides/`) onto the OSM street network and publishes an
interactive Leaflet map (`docs/`, served via GitHub Pages) plus static PNGs.

## Commands

- Dev install: `pip install . pytest 'ruff==0.16.3'` (what CI does; the same
  pins live in `[dependency-groups] dev`)
- Run pipeline: `python -m bike_routes` (flags: `--sample N`, `--rides FILE...`,
  `--no-png`, `--workers N`)
- Tests: `pytest -q` (synthetic grid graphs only -- no network, no OSM data)
- Map E2E tests: `npx playwright test` (needs `npm install` +
  `npx playwright install chromium` once). Hermetic: `tests/e2e/fixture.js`
  builds a synthetic rides.geojson (exact schema of `export.py`), Leaflet is
  served from node_modules, tiles are stubbed -- no network, no real ride
  data. Keep fixture expectations hand-computable; when `docs/index.html`
  behavior changes, update/extend `tests/e2e/*.spec.js`.
- Lint: `ruff check .` (CI enforces this; config is `select = ["ALL"]` with a
  curated ignore list in pyproject.toml -- keep new code clean rather than
  adding ignores). **Run the pinned 0.16.3**: `select = ["ALL"]` opts into
  every rule ruff ships, so another version reports findings CI does not --
  0.15.8 flags S310 on the Open-Meteo `urlopen` calls, 0.16.3 does not.
  A lint error that CI is green on is a version mismatch, not a bug; check
  `ruff --version` before touching the code.
- Format: `ruff format .` -- run after every change; CI fails on
  unformatted code (`ruff format --check .`).
- `ty check` runs but is not enforced; a few dozen known diagnostics (mostly
  `not-subscriptable`, ~3/4 of them in tests). The count is not a gate --
  don't chase it, and don't quote it here.
- Analysis (real rides + caches, never the synthetic grids):
  `python tools/traversal_audit.py` before any `TRAVERSAL_*`/`SPEED_*`
  threshold change, `python tools/hmm_matcher_eval.py` before any matcher
  change, `python tools/neighborhood_audit.py` before touching the
  per-neighborhood block, `python tools/speed_consistency.py` for the
  fastest/slowest stretch ranking (it re-measures the passes instead of
  reading `edge_speed`, which stores no pass-to-pass spread). Those all read
  state and none writes it; `tools/rebuild_graph_from_cache.py` is the one
  exception and writes the graph cache, but only with `--write`.

## Architecture

`bike_routes/` package, one stage per module:

1. `gps.py` -- load CSVs, filter to NYC, resample to 20m spacing
2. `graph.py` -- fetch/merge OSM networks (bike+drive+walk), cache to pickle.
   The fetch is a **polygon**, not a box: `_fetch_region` is the city box as
   far as the rides reach it, unioned with a `CORRIDOR_BUFFER_M` corridor
   around whatever they ride outside it (see below). A `Connection refused`
   from Overpass is usually not the network: osmnx's `_config_dns` resolves
   the host **once** with `gethostbyname` and mutates `getaddrinfo` to pin
   the run to that one IP, so a hostname round-robining between a live
   server and a dead one fails every time while curl walks past it.
   `_overpass_diagnosis` prints which address answers and
   `config.OVERPASS_URL` names it. When Overpass is down outright rather than
   pinned to a dead server, `tools/rebuild_graph_from_cache.py` rebuilds the
   graph offline from the Overpass responses osmnx has already cached --
   those are the output of osmnx's own network filters, so a rebuild yields
   the same post-simplification node ids and does **not** invalidate the
   matching in `edge_rides`
3. `hmm.py` / `matching.py` -- map-match rides to edges. `MATCHER = "hmm"`
   (leuvenmapmatching Viterbi) is the default; the "heuristic" snap+route
   matcher is kept for comparison. Parallel matching via worker processes
   in `matching.py`. `hmm._sidewalk_edges` shapes the matcher's input: the
   map index is built without sidewalk-class edges (see below).
4. `cache.py` -- cache/state.pkl (processed files, edge counts, per-edge
   passes and speeds), config-hash invalidation
5. `edge_speed.py` -- per-edge passes, backfilled from the ride CSVs'
   timestamps (see below): direction-split speed, and how many times each
   ride traversed each edge (the map's frequency). Needs `edge_geom`, so it
   runs from `cli._finalize` rather than the matching checkpoint
6. `render.py` / `export.py` -- PNGs and `docs/rides.geojson.gz`
7. `merge.py` -- collapse parallel/duplicate edge geometries into corridors.
   Not a stage of its own: `export.py` calls it on the built features, so it
   runs *after* the speed/pass backfill and sees its counts
8. `weather.py` -- Open-Meteo ride-weather stats embedded in the GeoJSON
9. `citibike.py` -- Citibike dock-trip stats, same shape as `weather.py`:
   a top-level `properties` block computed inline in `export.py`, no stage,
   no state key, `None` when its cache is absent
10. `neighborhoods.py` -- per-NTA coverage, same shape again. It also tags
   each drawn feature with the area it sits in, so the block is built after
   the merge. The boundary file is fetched once by `cli.main`, never by the
   export: keeping the network out of `_export_geojson` is what keeps the
   export tests offline
11. `subway.py` -- the dream-subway overlay, same shape once more. It reshapes
   `cache/dream_subway/od_network.json`, which `tools/dream_subway/` writes by
   hand like the Citibike export; `None` when that file is absent

`bike_routes/ingest/` is the front of the pipeline (`garmin_sync`, `gpx_to_csv`,
`citibike`), run as `python -m bike_routes.ingest.<mod>`; it fills `rides/`
(and, for `citibike`, `cache/citibike_trips.json` -- dock trips are not GPS
traces and must never land in `rides/`) and is not imported by any pipeline
stage. `tools/` holds standalone analysis that is not
part of the pipeline at all (`hmm_matcher_eval.py`, `weather_correlation.py`,
`traversal_audit.py`, `neighborhood_audit.py`, `bike_reencounters.py` -- which
alone among them imports nothing from `bike_routes`, so it runs from the trips
JSON on a checkout with no pipeline deps), plus `render_readme_map.py`,
which crops the README's image out of the same caches, and
`rebuild_graph_from_cache.py`, which is not analysis at all but a recovery
path for a graph that is gone or wrong (it reproduces `graph_from_polygon`
step for step against cached responses, so it is coupled to osmnx internals
and needs rechecking when that function changes shape); all are run from the
repo root. `findings/`
holds the write-ups of what that analysis found (moved out of the README to
keep it about running the pipeline).

The package `__init__` deliberately exports nothing -- import the stage you
need (`from bike_routes import edge_speed`). It used to re-export ~150 names
flat, which made every import pull in osmnx + matplotlib and hid which module
owned what; don't reintroduce that.

`docs/index.html` is a single self-contained Leaflet page (no build step); it
reads everything from `rides.geojson.gz` top-level `properties`.

**The four drawn layers share one switcher (`#layers`), and only the network
starts on.** Switching the network off is the same lever the date filter
already pulls -- `routesOn` gates `applyFilter`'s add/remove, so the children
leave the map and keep their counts, and the switch always renormalizes so
every layer passes through on the way out and back. **Ride view outlives the
switch**: it is an explicit request for one recording and already overrides
the date filter for that reason, so `viewRide` draws the ride and drops the
ghosts rather than refusing. The rule above the group belongs to `#layers`,
never to the first toggle -- the dock and neighborhood rows are hidden until
their payload arrives, so a border hung on a row would come and go with the
data. `--rail-fixed` is what the open stats section has to leave behind for
the rest of the right rail, the legend included; it has to grow when the
legend does. It is a desktop measurement only -- a phone has no rails (see
the sheet, below).

**A click answers in the docked inspector, never a popup.** A popup opens over
the feature it describes, which is the one thing a reader clicked it to look
at; the page carried ~70 lines of drag machinery to work around that. The panel
shares `#left-rail` with the ride-view bar; `#stats` and `#legend` share
`#right-rail`. Both rails are flex columns rather than sets of
absolutely-positioned boxes, so their contents cannot overlap however tall they
grow -- the panel's height depends on what was clicked and the stats panel's on
which section is open, and the open section gives up height rather than running
into the legend. **The legend is pinned by `margin-top: auto`, never
`justify-content: space-between`**: a rail whose other box is hidden has one
item in flow, and space-between puts a lone item at the *top* -- which had the
legend riding at the top of the window until something was clicked. **The
ride-view bar is in the left rail so that the phone breakpoint can lay it down
there**, on top of the sheet: centred over the map it would sit in the middle
of a screen the sheet already owns the bottom of, and the ride it names was
almost always opened from a row inside the sheet. Rejoining the rail's flow is
the same lever as everything else here -- a flex column cannot overlap itself
whatever the label wraps to -- so on a desktop the bar is `position: fixed`
rather than `absolute`, or it would centre on the rail's width instead of the
map's. `map.panInside` moves the map
only when the clicked feature would fall behind the panel (`showArea` frames
the whole polygon itself instead, so `selectArea` is told not to pan on top of
the flight). One panel serves every layer: a source is `{ kind, latlng,
render }`, and `render()` returns `{ title, body }`. Because it covers nothing
it can also outlive the click: `applyFilter` re-renders it, gated on
`renormalize` so playback frames do not rebuild a 141-row dock, and
`selectedEdge` is re-painted by every bulk restyle that would otherwise wipe
it. Escape unwinds one layer at a time -- the ride on screen, then the panel.

**A street's panel is headed by its name, and not every street has one.** The
export ships `properties.street_names` with an `sn` index per feature -- a
table, because the same few hundred names repeat over ~15k features. Roughly
an eighth of drawn features are unnamed, nearly all of them footway, service
road and ramp, and they carry no `sn` at all; the pass count is still the
heading there, so the panel has to read both ways. Where there is a name the
count moves under it (`.edge-sub`), because the count is what the filters move
and the name is not: an emptied street keeps its heading and says "No passes
in range" below it. The hover tooltip carries the name ahead of the count on
one line, and returns a node rather than a string: Leaflet sets tooltip
content as HTML, and the name is OSM data. The name is a *drawn feature's*,
after the merge, so a corridor carries one of its cluster's names -- fine for
a heading, and not the same unit as the speed rankings' chained stretches.

**The panel is sized to its content (`width: fit-content`), the stats panel
deliberately is not.** A street's rows measure ~248px, a dock's ~227 and a
neighborhood's ~208, so a fixed column spends the difference covering map.
`#stats` is the opposite case and its 236px is a squeeze, not slack: its
content wants 434px (705 with the streets section open), and everything in it
is width-driven -- the hero grid, the right-justified rows, the `flex: 1`
histogram bars -- so sizing it to content would widen it and sizing it to the
viewport would stretch those to ~1.7x. Both of those are desktop rules: on a
phone neither box is on screen at all, only its body, inside the sheet.

**A phone gets one sheet, not two rails.** The rails work because there is
room beside the map to put them; under 640px there is no beside, and the two
of them stacked down the right of a 390px screen took the top half of it
before anything was clicked -- with the busiest street on the map running
underneath, so a tap on it hit the legend. `#sheet` replaces the pair: one
surface along the bottom, showing exactly one thing -- the stats, the filters,
or whatever was last clicked -- and nothing else over the map. The zoom
buttons go too (pinch is the gesture, and they sat in the corner a reader
reaches the map through) and `.leaflet-bottom` is lifted by `--sheet-h`,
because attribution is not optional.

- **The panels are lent to it, never rebuilt for it.** `applyLayout` moves
  `#stats-body`, `#legend-title`, `#legend-body` and `#inspector-body` between
  the rails and the sheet's panes as the breakpoint is crossed, so there is
  one stats panel and one legend on the page rather than a phone copy and a
  desktop copy, and every listener, open section and scroll position survives
  the trip. The cost is that a rule hung on the box a body left behind stops
  matching it: `#stats strong`, `#legend .bar` and their like are scoped to
  the *body* (`#stats-body strong`, `#legend-body .bar`) for that reason, and
  the legend's caption had to be given an id to be addressed at all. Anything
  new inside those bodies has to be scoped the same way.
- **What is in the sheet is derived, never toggled.** The pane is a function
  of `inspector` and the standing tab (`syncSheet`), so a click, a second
  click on another street, Back, Escape and a tap on the map all land in the
  same place. Its predecessor collapsed `#stats` when a detail opened and
  reopened it on close, which put two owners on one piece of state: street to
  street runs a close and then an open, so a reader who reopened the panel had
  it shut again by their next click, and closing a detail reopened a panel
  they had deliberately shut. That reads as panels closing and reopening at
  random, and it is a state bug -- there is no threshold that fixes it, only
  having one owner. Never store "was it open" here.
- **How much screen it takes is the reader's.** Three stops -- the grab strip
  and nav row alone (measured, not written down), 46% and 88% -- dragged
  between or tapped through. It is the desktop collapse button made
  continuous and moved to where the thumb is. A tap is show-and-hide and never
  a third height. The one thing that moves the sheet on the page's own account
  is a detail opening while it is out of the way, and then only up to the
  middle stop: opening a detail is a reason to show the sheet, never to take
  away a map the reader had asked for or shrink one they had dragged up.
- **The breakpoint asks about height too, and is written once.** `max-width:
  640px` **or** `max-height: 480px`: a landscape phone is 844x390, which
  passes any width test comfortably and then has nowhere to put a panel that
  is most of the screen tall. Both halves are the same question -- is there
  room beside the map. The media query sets `--layout: phone` and
  `layoutIsPhone()` reads it back, so the numbers live in the stylesheet --
  which is what decides there is no room for two rails -- and not also in the
  script. `railPadding()` measures the sheet's own rect for the same reason,
  capped at 55% of the view so a sheet dragged to the top cannot ask
  `panInside` for more room than the map has.
- The sheet is full-bleed and its *content* is capped (`.sheet-pane`,
  520px, centred). An earlier attempt at a phone layout made the inspector
  itself full-bleed, which stretched a box whose widest kind measures ~250px
  across the screen; capping the column rather than the surface is what
  answers that without giving a 390px screen a 236px panel again.

## Invariants

- **Changing any parameter in `cache._processing_config()` triggers a full
  reprocess** of all rides (config hash mismatch discards cache/state.pkl). Don't
  add keys to it unless the change genuinely invalidates prior matches.
- Edge keys are canonical `(min(u,v), max(u,v))` node pairs everywhere.
- **The map's frequency is traversals, not rides**, and they come from
  `edge_speed`, never from the matcher (see below). `state["edge_counts"]` and
  `edge_rides` stay per-ride: `_apply_results` counts a file once even when a
  GPS gap split it into several segments.
- **The matcher chooses from less than the whole graph, and only the
  matcher does.** `hmm._sidewalk_edges` keeps `footway`/`steps` edges with a
  roadway running parallel within `SIDEWALK_PARALLEL_M` out of the map
  index; the full graph still supplies geometry, coverage, drawing and
  merge, so a footway that really was ridden still draws. Only roadways vote
  (`SIDEWALK_STREET_TAGS`, a **positive** list): a negative one let service
  ways beside the Hudson River Park Esplanade and a bridleway beside Central
  Park's West Drive each class a greenway as sidewalk at *every* threshold,
  which is a class of error a threshold sweep cannot see -- check named
  greenways, not just the aggregate. Removal beats a penalty because the
  8-wide beam is the scarce resource: penalised sidewalk states still occupy
  slots ([why](findings/sidewalk-matching.md)).
- The sidewalk filter's parameters are in **both** `_processing_config()`
  and the `hmm_map_cache.pkl` payload. The map cache is otherwise
  invalidated only by its format and the graph's mtime, so a changed
  threshold would rematch every ride against a map still filtered the old
  way.
- Matching results must be deterministic and independent of scheduling.
  Results are folded into state chunk by chunk as they arrive (`cli.py`),
  which is safe because `edge_counts` accumulates by addition and
  `edge_rides` is only ever read via `min()` and `set()` (`export.py`) --
  never by list order. Chunk order/composition is free to change.
- State is checkpointed every `config.CHECKPOINT_EVERY_RIDES` rides during
  matching, so an interrupted run resumes from the last checkpoint rather
  than rematching everything. A file split at GPS gaps spans several entries
  in `new_rides` but one entry in `processed_files`, so `_ready_results`
  holds a file back until all its segments land -- checkpointing mid-file
  would mark it done and silently drop the rest on resume.
- Workers on Windows use spawn: anything they need must be importable or on
  disk (graph cache / cache/hmm_map_cache.pkl), never closure state.
- Every generated cache lives in `config.CACHE_DIR` (`cache/`); each writer
  mkdirs its own parent, so a monkeypatched path never creates a stray dir.
  `cache._migrate_legacy_caches()` moves pre-`cache/` files in from the repo
  root on startup, and must keep using `shutil.move`: it is a rename rather
  than a copy of a 260 MB graph, and it preserves the mtimes the check below
  depends on.
- Caches are mtime/version-invalidated: cache/hmm_map_cache.pkl must be newer
  than cache/osm_graph_cache.pkl; graph cache is bound to osmnx/networkx
  versions.
- **Citibike trips are dock-to-dock with no GPS trace**, so they never enter
  `edge_counts`, `edge_traversals`, `edge_rides`, `coverage`, or `features[]`
  -- those all mean "a trace was matched here". They live only in
  `properties.citibike`. The dock layer draws **markers, and lines only for
  the one dock a reader clicked**; a routed path between docks was built,
  measured and rejected, because it makes a guess look like a trace
  ([why](findings/citibike-trips.md)). No speed is derived either -- the
  export's durations are whole minutes and its end times are the start plus
  that duration.
- **A dock in focus ghosts the drawn network**, using ride view's own
  `EDGE_GHOST` style, because a busy dock's straight lines are the same cyan
  as 21k plasma edges and lose against them. The network stays on screen in
  outline -- reading the docks against where the bike goes is the point of
  the layer -- and the slider still moves it, in outline (`dockFocus()` gates
  `applyFilter`'s restyle).
- **The one route a dock row can draw is a recorded one.** `trip_rides` names
  the GPS ride running over each trip and ships it as the 4th element of each
  `properties.citibike.trips` row (`-1` where none), so a dock row can put
  that ride on the map in the page's own single-ride view. It is not the
  rejected routed layer: the dock-to-dock line stays straight, and what is
  drawn over it was measured. While a pair's route is up, that pair's straight
  line is the **only** link drawn (`dockTraceTo`): a dock reaching 141 others
  buries the route under its own starburst otherwise. One cycle at a time
  lives in `dockTrace`, out of the row that started it, because the row's
  chip, the up/down arrows and the ride-view bar's `route 4/31` all address
  it; the arrows wrap rather than exiting, and are captured before Leaflet's
  own listener so a step does not also pan the map. Ride view draws the
  **whole** recording, and 23% of recorded trips sit inside one that holds
  several, so the row says
  when it covers others -- clipping the trace to a trip's clock window would
  need a per-(edge, ride) timestamp nothing in `state` carries. Tracing a
  pair leaves the panel exactly where it was: it covers no map, so there is
  nothing to gain by closing it, and the rows are how a reader walks the
  network. `viewRide(ri, fromTrace)` only says whether the cycle in
  `dockTrace` survives; every other way into ride view ends it.
- **A GPS ride is matched to Citibike trips by clock overlap**
  (`citibike.ride_sources`), shipped as the 4th element of each row in the
  export's `rides` array: `-1` unknown, `0` own bike, `n>=1` the number of
  trips it overlaps. **`0` and `-1` are not the same claim** -- outside the
  export's window there is no evidence either way, so those rides are
  unknown, and the page's source filter hides them from both sides rather
  than counting them as own-bike. The 60s minimum overlap is not a tuned
  threshold: anything from 1s to 120s gives the same answer on the real
  rides.
- **The graph reaches past `NYC_BBOX` and the coverage number does not.**
  A ride counts as a NYC ride if any of it is in the box and is then kept
  whole, so the graph has to cover the whole of a ride up 9W or out to Jones
  Beach or its far end matches against no edges at all. It follows those
  rides as *corridors* rather than as a bigger box: `graph._fetch_region`
  unions the city box with a `CORRIDOR_BUFFER_M`-wide buffer around the
  out-of-box track, which is why the fetch is `graph_from_polygon`. The
  region is stored in `state["graph_region"]` as WKT and only ever grows;
  `graph_bbox` is its bounding box, kept so a state written before it still
  reads back. Build the corridor per track and union **last** -- unioning
  the tracks first nodes every self-crossing of an out-and-back and turns a
  tenth of a second into minutes.
- **What the graph draws outside the box is counted on neither side of
  coverage.** `export._in_city_box` drops an edge whose midpoint is outside
  `NYC_BBOX` before the `COVERAGE_EXCLUDE` test, so Route 9W is drawn and
  ridden and lands in `riding.total_km`, but neither raises the numerator
  nor dilutes the denominator. `excluded_km` is that denominator's own
  footnote, so it is filtered the same way. `edge_speed`, the stretch
  rankings and the drawn features are all unfiltered -- a stretch of the
  Empire State Trail can rank, and should.
- **The map's coverage number has two denominators and both ship.**
  `coverage.pct` is measured over every rideable edge inside `NYC_BBOX`, and
  half of that box is not in New York City -- it runs from Newark to Nassau
  -- so riding further out *inside the box* lowers it.
  `properties.neighborhoods` carries the same measurement over the part
  inside a NYC neighborhood (11.8% against 6.6%), and that is what the "of
  NYC" tile shows, because that is what the label claims. Neither is a share
  of the whole city: the box has never reached Staten Island
  ([details](findings/neighborhoods.md)).
- **A neighborhood is filled by coverage as of the date on screen**, not
  all-time, so the slider and the time-lapse move it the way they move the
  edges and the dock markers. The export ships `new` -- [date index, metres
  first ridden that day] -- and the page takes a running total up to
  `filterHi`; it follows the range's upper end alone, because "how much had
  been ridden by then" is a running total. Areas are placed by edge midpoint,
  which misplaces 4.7% of ridden metres, nearly all of it on ten named
  bridges and waterfront paths -- fine for a fill colour, not for anything
  stronger.
- **The selected area's border is a layer of its own** (`nbOutline`), and its
  fill is not. Every vector layer shares one canvas, so a polygon is a single
  object in the draw order: the fill has to stay under the edges -- the
  streets are the subject and a neighborhood is ground for them -- while the
  white border has to sit over them, or 21k frequency lines paint across it
  until the outline reads as dashes. `syncNbOutline` rebuilds a stroke-only
  copy of the selected rings above the network, and `restackLayers` lifts it
  last, the same way it lifts the dock markers back over a filter change.
  `nbStyle` therefore no longer varies with `nbSelected`.
- **An area's panel counts rides, never passes.** A pass belongs to one
  stretch of street: "4 passes" on a street means that stretch was ridden four
  times. Summed over an area it counts segment-crossings instead, and Forest
  Hills -- 104 drawn segments each ridden once, by the same two rides -- read
  "104", which is what being there 104 times would read like. `nbRidesIn`
  counts distinct rides in range. There is no honest area-level pass count:
  how many times a ride entered and left would be the real one, and a
  feature's `rides` carry no order and no clock.
- **Per-area distance and time are measured, all-time, and floors.**
  `dist_m` and `time_s` sum `edge_speed`'s metres and elapsed seconds over
  the area's edges -- real timestamps on known geometry, not one of the two
  derived from the other through an assumed speed. They count every measured
  edge whatever its highway tag, because distance and time on a park path
  are still distance and time spent there, which makes them the two figures
  in the block not restricted to `COVERAGE_EXCLUDE`-filtered edges. Neither
  can follow the slider: `edge_speed` has no per-ride breakdown. And both are
  floors, by a similar margin -- something like a quarter of what was
  recorded never lands on an edge at all, being off-network, inside a gap, or
  on a pass too short to admit.
- **`dist_m` is riding and `ridden_m` is network, and the block ships both.**
  `dist_m` counts a street again on every pass over it; `ridden_m` counts it
  once however often it was ridden, and is the Explored numerator and the
  layer's fill. On these rides `dist_m` is several times `ridden_m`, so the
  two are never interchangeable: the panel prints them one above the other
  precisely because a reader who has just seen "of its network" needs to know
  the larger figure is the same streets again, not more of them. Deriving
  `dist_m` from `edge_traversals` instead -- edge length times pass count --
  was tried and rejected: it charges a whole edge for a pass that only
  clipped it, so it overstates the total and puts areas at average speeds
  `time_s` contradicts ([why](findings/neighborhoods.md)). By how much is a
  measurement of one graph and one state, and a matcher change moves it --
  don't quote a figure here.
- **The Neighborhoods stats section is all-time**, like every other section
  of that panel; the layer is the part that moves with the slider. It rolls
  the areas up per borough (Manhattan 44.0% against Queens 5.2% -- the spread
  the one citywide number hides) and ranks the areas below that, each row
  opening its own polygon on the map.
- **That ranking has three tabs and each one sorts by the number it prints.**
  Ridden (`dist_m`) and Time (`time_s`) are the riding, out of the same
  `edge_speed` chunks, so the floors above apply to both and they part only
  where the riding was fast; Explored (`ridden_m / net_m`) is the network,
  and it is the order that genuinely differs -- how much of a place was seen
  rather than how far the bike went in it. The single list they replaced
  sorted by metres while printing a percentage, which reads as a ranking of
  the number on screen and is not one. A row carries its index into `areas`,
  never its place in the list, because the click opens a polygon.
- **Explored prints the network it is a share of, in the row.** That ranking
  is the one a small denominator wins, and the denominator is neither the
  neighborhood nor the drawn map but `COVERAGE_EXCLUDE`-filtered graph edges:
  Fort Hamilton comes top at 92% because 130 edges inside it reduce to 3.9 km
  counted, 3.6 of it one cycleway -- the army base's own grid is 85 `service`
  edges and the Belt Parkway is `motorway`, so neither is in the question the
  percentage answers. Shipping the denominator beside the share is the same
  move `coverage` makes with its two. Don't answer it with a minimum-network
  floor instead -- that silently drops the edge of the box, which is a real
  place the bike went.
- **A cluster member the kept ones already draw is never kept for the extent
  it adds.** Phase 1's greedy set-cover runs to `MERGE_KEEP_COV`, but a
  candidate covered at `MERGE_MUTUAL_COV` by the geometries kept so far is
  skipped first: two long parallel ways stagger at their ends, so neither
  alone reaches 97% of the cluster extent, and buying the difference draws a
  2 km bridge deck twice -- a few metres apart, once
  `_average_parallel_geometry` has pulled both onto the centreline, each line
  printing the cluster's whole pass count. The bar is against the
  *accumulated* kept set, never the previous member, which is what leaves a
  staggered junction chain its extent
  ([why](findings/sidewalk-matching.md)). No pass rides on this: a cluster
  sums its rides over every member before any is kept.
- **What is drawn and what is counted are two different sets.**
  `_export_geojson` draws every matched edge with no highway filter, so ridden
  footways, service roads and motorways are all on screen in the same cyan as
  the counted streets; coverage then measures only the filtered ones. The gap
  used to be enormous and concentrated exactly where the map looked fullest,
  because the matcher was putting the ride on the sidewalk beside the street
  it was credited against; the sidewalk filter closed most of it. It did not
  close all of it -- drawn-but-uncounted footway is still the largest excluded
  class -- so before calling such a number wrong, check both sets.
  `tools/neighborhood_audit.py` splits an area by highway tag.
- **The Citibike panel is a two-column comparison**, Citibike against own
  bike, on trips / time / days / typical length. The Citibike column is the
  export's own totals (every trip, including ones no GPS ride covers); the
  own-bike column is the rides `ride_sources` found no trip under. Both are
  complete records of their own kind, and the units line up because one GPS
  recording can hold several Citibike trips but never several own-bike ones.
  A one-way-dock ranking used to live here and was dropped as not saying
  enough ([details](findings/citibike-trips.md)).
- **A repeated bike id is two different events, and only one of them is a
  bike met again.** `_reencounters` calls it a round trip when that bike's own
  last trip ended at the dock this one starts from (`RESUME_MAX_GAP_S`,
  insensitive from 2h to 30 days) -- 200 of the real export's 293 repeats, and
  every same-day one. **Both halves of that condition are load-bearing even
  though only one ever fires**: no repeat in five years is "different dock,
  back within 48h", because same-dock repeats are all inside 48h or 16+ days
  out while different-dock ones are all 41+ days out. Do not simplify the
  predicate to the clock alone on the evidence that the other branch is empty
  -- that reclassifies the first bike to turn up across town the same
  afternoon, which is the case the rule exists for. The panel reports only
  the other 93, as "unlocks on a bike ridden before" -- under the
  re-encounter list's own heading, because it is what that list is a list of;
  **never report the raw 253 as "bikes ridden more than
  once"**, which is the wording this replaced and which counts a person taking
  their own bike home. The list's own tooltip does open with that phrase, and
  it is not the same claim: it is immediately qualified by the rule ("picked
  up either from a different dock or from the same dock 48+ hours later") and
  it labels the 88 bikes, not the 253. Do not reconcile the two by loosening
  either. The export ships `resumes` beside `reencounters` and
  nothing draws it -- it is there so the 93 can be checked against what it was
  cut from. Never derive a *rate* from any of it without the exposure: an
  ebike can only re-meet an ebike already ridden, which is what made ebikes
  look 7x rarer than they are ([why](findings/bike-reencounters.md), rerun
  with `python tools/bike_reencounters.py`).
- **The panel's re-encounter list counts encounters, not trips.** A bike
  ridden to lunch and back on one afternoon was unlocked twice and *met* once;
  `_reencounters` folds a run of round trips into the encounter that started
  it and dates that encounter by its earliest trip, so the row's `n` and its
  day-gaps are both between occasions rather than between legs. The first
  version derived "times met" as `trips - 1` and overstated every bike whose
  repeat was a round trip -- the headline 93 was right throughout, so nothing
  but the row caught it. Never reintroduce a trip count here.
- **That list is a way into the map, not a readout.**
  `_met_rows` ships `properties.citibike.met` as `[id, encounters, gaps,
  rides]` per bike met again, sorted by encounters, and a row opens that
  bike's recordings in single-ride view --
  the same cycle a dock row opens, through the same `dockTrace`. That cycle
  now has two owners, so controls compare `dockTrace.key` (`d:<dock>` or
  `b:<id>`) rather than `.to`, and only a dock cycle sets `.to`, which is what
  narrows the drawn links. **A bike with no recording keeps its row**, dimmed,
  and keeps its place in the sort -- it was met, and ranking on what happens
  to be clickable would shrink the list to suit the renderer.
- **The calendar is the date index, and it is a grid rather than a search
  box.** `renderCalendar` draws one row of twelve month cells per year out of
  `properties.rides` and `properties.dates` alone -- no export field of its
  own -- shaded on the map's own square-root plasma ramp, lifted off its dark
  end so one ride in a month still reads as a ride. A cell opens that month's
  rides, one row each, and a row opens the ride. The shape is half the answer:
  the dense summers and the empty Februaries are there before a question has
  been formed, which is what a text box cannot do. Years with no riding keep
  an empty row -- the gap is the thing the grid is for. Cells count rides,
  never passes.
- **The calendar is all-time, like every other section of that panel**, and
  the reason is sharper here than elsewhere: ride view draws its ride whole
  whatever the slider and the source buttons say, so an index that hid what
  they hide would offer a way in that the way out contradicts.
- **Ride view's highlight now has three owners and still no holder.** The
  calendar joins `dockTrace` and `syncYearLinks`'s year links in addressing
  "which ride is shown", and like them it reads that state back from
  `rideView` rather than setting one of its own: its rows carry `.yd-link`
  (so `syncYearLinks` selects on the class, not on `#stat-years`), and the
  cell of the month holding the shown ride is marked from the same place --
  which is what puts a ride reached from a street's rows back on the date axis.
- **The fleet-generation chart is two buckets and may never be more.**
  `_generations` splits trips per year on the id shape alone -- five digits is
  the older fleet, hyphenated sevens the newer -- and that reading is the
  owner's, from the physical bikes, since Citi Bike publishes no
  number-to-model mapping. Structure *within* either shape was tested for and
  is not there (prefix against first-seen date is r = -0.06; the prefix space
  is flat and dense, a lot number rather than a serial), so a finer split
  would be invented. The share is of one rider's unlocks -- the racks that
  rider used, not the fleet -- and the panel no longer says so: the caption
  went, then the sentence in the (?). That reading now lives only in
  [findings](findings/citibike-trips.md).
- **The two stacked bars are one layout with two palettes.** Fleet generation
  and bike type share `stackRow`/`stackKey`, so their geometry cannot drift
  apart; each brings only its own two colours. Generation takes two steps of
  the map's own plasma ramp (the histograms' purple, then the ramp's magenta),
  because older and newer are a sequence. **Bike type breaks that palette
  deliberately**: Citi Bike's blue for the classic bike and the ebike's grey
  are what a reader has already seen on the street, and a colour that names
  the thing beats one that matches the panel. The ebike share is a floor -- a
  free ebike ride carries no charge to count -- and the (?) says so; the bound
  was tried on the numbers themselves (`>=7%` against `<=93%`) and read as
  clutter at 10px.
- **A chance-test chart was built here, drawn four ways, and taken off.** The
  two permutation tests live in `tools/bike_reencounters.py` now, and a result
  needing a p-value belongs there rather than on the panel. Two of those four
  versions were wrong in ways only rendering revealed -- band histograms (93
  events over 4 bands is all noise) and a percentile axis (a middle-95% band
  covers 95% of the track by construction). If a chart is ever reinstated
  here, read [why each failed](findings/bike-reencounters.md) first, and note
  that the working one still lost to a list you can click.
- **The dream subway measures nothing, and the page must never let it
  look as though it does.** `properties.subway` is a hypothetical fitted to
  where rides begin and end, so it stays out of `edge_counts`, `coverage` and
  `features[]` the same way Citibike trips do. A station's position is real --
  the centroid of a cluster of ride endpoints -- but **the chord between two
  stations is drawn straight**, because `odnet.py` never opens the geometry:
  routing it along streets would make a guess look like a trace, which is the
  argument that keeps a dock's links straight too. It is also the one drawn
  layer the slider cannot move: the weights and the lines are both fitted to
  the whole history, so a date-filtered version would resize the markers while
  leaving the network under them unchanged ([why](findings/dream-subway.md)).
- **Two lines sharing a stretch each get their own track, and the track is
  measured in pixels.** 5 of the network's 43 segments are carried by two
  lines, and drawn on one centreline the second simply hides the first. A
  chord is therefore a *multi*-polyline: an unshared segment is the plain pair
  of stations, a shared one tapers out to its own track and back so both lines
  still meet at the stop they share. The offset is screen pixels recomputed on
  `zoomend`, never a fixed distance on the ground -- a ground offset collapses
  to a single line at city scale, which is the scale this layer is read at.
- **Every bend is a corner of fixed radius, because a subway map has no
  pointy ones.** `swRoundCorners` replaces each interior vertex with a
  quadratic Bezier whose control point *is* that vertex, sampled into points
  because the canvas renderer strokes polylines and nothing else. The radius
  radius is the *smallest* of three bounds -- a 20px cap, half the shorter leg,
  and `2R / sin(deflection / 2)`, where R is the station marker's radius -- so
  a corner is as generous as the marker covering it allows. A fillet pulls the
  line off the vertex by `t * sin(deflection / 2) / 2`, and the vertex stays
  where it is. **Size that bound on the bends the lines actually make, not the
  ones the builder permits**: a flat 12px set by the 62-degree limit read as a
  mitre, because the sharpest bend in the real network is 57 degrees and the
  legs are 38-80px, which affords 19. **Don't spline through the stations instead** -- that bows the chord
  between two of them, and a curved chord claims a route this network has
  never measured. Sampling is capped by *angle* (`SW_ARC_DEG`), not by a fixed
  step count: a fixed count cuts a gentle bend into sub-pixel steps, which is
  how a test measuring angles off `latLngToLayerPoint` came to read 45 degrees
  on a smooth curve -- that method rounds to whole pixels, so use
  `map.project` whenever geometry is being measured rather than drawn.
- **`networkIsContext()` is why the streets go to outline, and it has two
  owners.** A dock in focus and the subway overlay both lay thin bright lines
  over 21k plasma ones, which is a haystack rather than a comparison; the
  subway is the worse case, because five of its line colours sit inside the
  plasma ramp itself. Both ghost the network with ride view's own `EDGE_GHOST`
  and leave it on screen in outline -- reading the overlay against where the
  bike goes is the point of both layers -- and the slider still moves it, in
  outline. Ride view outranks both: it owns the drawn edges whenever it is up.
- **The dock layer is meant to be explored, not read.** Markers resize with
  the same `filterLo`/`filterHi` range that filters the edges (`applyFilter`
  calls `applyDockFilter`), so the slider and time-lapse move them too. The
  two sources share dates but not days, so they are joined on the ISO date
  string rather than a shared index. An earlier version replaced the layer
  with a ranked text list on the grounds that the list "says it better";
  that is the wrong test for this project -- the map is a medium to explore,
  and a layer that can be filtered and drilled into beats a row that states
  one finding.

### Edge passes (`edge_speed.py`)

One pass detector, two outputs: direction-split speed, and per-ride traversal
counts. Both are backfilled from the ride CSVs' timestamps, which the matcher
never saw. Full derivation of every rule below, with the numbers that
produced it, is in [findings/traversal-counting.md](findings/traversal-counting.md).

**"Passes" and "traversals" are the same thing** -- the state keys and this
file say traversals, `docs/index.html` and the README say passes, because
that is what reads clearly in a tooltip. Neither is a ride count.

**Speed is a stats-panel ranking, not a map layer.** Only ~6% of drawn km has
enough passes in both directions to compare them, so a speed layer was
94% empty; the export ships a top-10 corridor list in `properties.speed`
instead. Features carry no speed key and `merge.py` never sees it
([why](findings/direction-split-speed.md)).

**There are two speed rankings in `properties.speed` and they ask different
questions.** `corridors` compares a stretch with its own opposite direction,
which takes passes both ways -- so it can only speak about two-way streets,
and on these rides it answers in bridges. `fastest`/`slowest`
(`_top_stretches`) compare a stretch with the rest of the network on the
passes it has, which is the only way a one-way street can be ranked at all:
about half of what ranks there was never ridden the other way
([details](findings/stretch-pace.md)). Both are pipeline-side, because a
ranked stretch is chained speed chunks: the export ships a name per *drawn
feature* (below), which is a different unit and cannot rebuild one.

**All three rank stretches of street, so they share one list and one tab
strip** (`#speed-tabs`, Fastest / Slowest / Faster one way), not a block each: two
stacked lists outgrew the section between them, and a reader had to scroll
past one ranking to learn the next existed. A tab reads its own array and
never re-ranks; a tab with an empty array is not offered; and the (?) on the
heading carries the *active* tab's rule, because one text describing three
questions would describe the two a reader is not looking at.

- **A stretch is chained across edges, a corridor is not.** The median edge
  is 63 m and the floor is 250 m, so without chaining the second ranking
  would be the first one's bridges again. `_chain_units` joins chunk-
  directions that share a node and a street name, taking the straightest
  continuation at a fork so a stretch never depends on dict order, and using
  each chunk once so no metre is ranked twice. `tools/speed_consistency.py`
  chains through the same function.
- **Ranked by the average, which is the number the row prints**; the
  pass-to-pass deviation (the record's `speed_sum`/`speed_sq_sum` slots) is
  shown beside it and breaks ties, nothing more. Ranking on the average less
  that deviation was built and rejected: at the slow end it put a steady 7.7
  mph street above a 6.3 mph one in a list called Slowest, and at the fast
  end it reordered a list it agreed with anyway. What keeps a lucky run out
  is the five-pass floor, not the deviation. Whatever the rule becomes, the
  ranked number and the printed number stay the same number -- the trap the
  neighborhood list fell into.
- **The fast end is a podium and the slow end is a pack.** The whole slow
  tab sits inside half an mph, with 20 more stretches within 1 mph of it, so
  the order there is not a finding and the panel's approximation reproduces
  little of it (the exact ranking is the tool's, which re-measures every
  ride). Don't tune anything to make the two slow lists agree.

- `state["edge_speed"]` / `state["edge_traversals"]` / `state["speed_rides"]`
  are deliberately **outside** `_processing_config()`, so changing them never
  triggers a rematch. Their invalidation lever is `config.SPEED_VERSION` --
  bump it and the next run discards and recomputes. Editing the algorithm
  without bumping it leaves stale records in place, silently.
  (`_records_well_formed` is a fail-closed shape guard for records that
  predate a layout change.)
- **Forward means "along the stored vertex order of `edge_geom[key]`", not
  `min(u,v)` -> `max(u,v)`.** `render.py` builds `edge_geom[canon]` from
  whichever *directed* edge won the shortest-edge tie-break, so ~9.5% of
  geometries run max->min; anchoring to the node key would invert a scattered
  subset. `_oriented_chunks` re-expresses a record against the current
  geometry, so a rebuilt render cache cannot silently flip it.
- Records are per-chunk (~`SPEED_CHUNK_M`), not per-edge: the Manhattan
  Bridge bike path is a single 2163 m edge whose climb cancels its descent.
  Each direction bucket is `[dist, time, moving, n, speed_sum,
  speed_sq_sum]`, so it combines by addition and survives chunked folding
  like `edge_counts`. The last two are over passes, not metres -- they are a
  mean and a deviation across crossings, which totals cannot give. Slot
  offsets are `_FWD`/`_REV`; `neighborhoods.py` imports them rather than
  writing 0 and 4 again, because the record has grown once already.
- **`_top_corridors` splits a corridor wherever the faster direction flips**,
  and that is the point, not an implementation detail: a bridge's entire
  signal is the crest reversal, so it must be reported as its two descents.
  Runs are disjoint, so two rows for one street are always different
  stretches.
- Street names come from the render cache (`edge_name`, `RENDER_CACHE_FORMAT`
  = `hw-name-v1`). Bumping that format costs one graph load to rebuild; it
  does not touch the config hash. `_build_render_cache` takes the name and
  the highway tag from the **first** edge of a canonical pair but the
  geometry from the **shortest**, so all three can come from different OSM
  ways, and which one is first depends on graph iteration order. 524 pairs
  carry more than one name, so a corridor can be ranked under either of two
  street names across rebuilds; 2,585 carry more than one tag with no bike
  class to decide between them. Don't read an exact figure derived from
  either as reproducible across a graph rebuild.

#### Traversal counts

`state["edge_traversals"][key][ride]` is `[forward, reverse]` -- how many
times one ride crossed one edge, in the stored vertex order of
`edge_geom[key]`. It is what the map colours by.

- **Never derive this from the matcher's edge list.** It collapses
  consecutive repeats, and its non-consecutive repeats cannot be told apart
  from lattice oscillation at an intersection. The raw fixes can.
- **Every measured pass is stored, singles included**, because `merge.py`
  combines a corridor per direction and one pass on each of two members is
  the out-and-back it most needs to see.
- **A missing entry means nothing was measured, which is not the same as one
  pass.** Readers go through `ride_traversals()` (total, floored at 1) or
  `ride_pass_dirs()` (the raw pair, `(0, 0)` when unmeasured), so a ride the
  detector could not measure still draws its edges exactly as before:
  measurement can raise a count, never take an edge off the map. The floor
  lives in `merge._ride_passes`, per corridor rather than per member -- an
  unmeasured pass has no direction, and giving it one would let two members
  whose geometries run opposite ways sum to two out of nothing. Never reach
  that floor through a `dict.get` default: an absent ride must read 0, or an
  empty neighbour set appears to cover everything and
  `_drop_redundant_rings` drops every ring.
- **A crossing arrives in fragments, and neither rule below is optional.**
  `_runs` ends a run at `SPEED_MAX_FIX_GAP_S` and again whenever the trace
  snaps to a neighbouring way. `_merge_resumed` rejoins the pieces by
  progression -- resuming at or ahead of where the last stopped, in that
  pass's own direction, with `TRAVERSAL_RESUME_M` of backward slack -- so a
  second lap (re-enters from the far end) and a turnaround (reverses) are not
  absorbed. Merging can only lower a count.
- **`TRAVERSAL_MIN_COVER`: a traversal has to sweep the edge, not clip it.**
  Speed's `SPEED_MIN_PASS_M` is an absolute floor; counting needs a fraction,
  or a wobble at one end of a long edge outvotes the ride that crossed it.
  Under the bar the pass is ignored and the floor puts the edge back at 1.
- **`merge.py` combines a corridor's members by max *within* a direction and
  sum *across* the two** (`_merge_ride_counts`): a pass drifting from a
  street to its bike lane is one direction twice, so max holds it at one; an
  out-and-back on the two is each direction once, so the sum is two. Stored
  vertex order is arbitrary, so every merge site resolves a flip with
  `_opposed` first -- without it one physical pass on two oppositely-stored
  members reads as an out-and-back.
- **`_drop_redundant_rings` reads directions too, for the same reason.** A
  ring is dropped only where its neighbours already carry each of its passes
  *in the direction it recorded them*; comparing the floored totals let a
  corridor that only ever went one way account for the ring's return leg,
  and the leg went off the map with the ring. `_opposed` cannot help here --
  a ring's chord is ~zero, so neither side is ever flipped onto the other
  and each is read in its own stored order. That can only make coverage
  harder to satisfy, so the error it leaves is a boxy notch drawn, never a
  pass lost.
- The export ships `properties.rides` with one entry per traversal, so the
  page's count is array length. Equal filenames map to equal indices, so the
  array stays sorted for `hasRide`'s binary search.
- **Before changing any threshold here, run `tools/traversal_audit.py`** on
  the real rides, and read its top-20 list as the audit intends: a long edge
  near the top is the alarm, not a discovery. A synthetic grid cannot tell
  you whether a threshold over-fires on real GPS, and it cannot catch a
  direction bug either -- the oracle for that is the Manhattan Bridge
  appearing twice in the speed ranking, SE and NW.


## Performance notes

- Matching is the hot path; leuvenmapmatching is pure Python. Profile before
  optimizing and benchmark against real rides + caches (repo root of a full
  checkout with `rides/` and `cache/*.pkl` present), not the synthetic test
  grids.
- Build the HMM InMemMap by passing a prebuilt graph dict to the constructor
  (bulk rtree load); never via add_node/add_edge in a loop (~15x slower).
- XY-projected matching (use_latlon=False) was benchmarked and is NOT faster;
  don't revisit. A windowed wide-beam retry (keep the narrow prefix,
  re-decode only around the dead end) was also evaluated and REJECTED: the
  narrow-beam path is measurably worse over the whole ride when it
  dead-ends (p90 length ratio 1.129 -> 1.167) for only ~1.25x speedup --
  the full-track wide retry is what rescues ride quality. Any future retry
  change needs the same eval (matched/GPS length-ratio on 50+ real rides).

## Workflow

- Commit directly to `main` and push after each self-contained change (no PR
  branches unless explicitly requested).
- CI (`tests.yml`) runs ruff + pytest on every push. There is no scheduled
  map-update workflow: the map is regenerated locally with `python update.py`
  (Garmin fetch -> GPX -> CSV -> pipeline -> commit) and pushed by hand.
  It is Python, not a shell script, because the owner's machine is Windows --
  a committed `.sh` gets CRLF endings on checkout there and bash refuses it.
- Ride ingest is Garmin Connect (`bike_routes/ingest/garmin_sync.py`),
  authenticated from a token in `~/.garminconnect` (override with
  `GARMINTOKENS`). It stays local -- the ride CSVs and the graph cache only
  exist on the owner's machine, and Garmin's login blocks datacenter IPs
  ([details](findings/garmin-access.md)).
- **`rides/*.csv` on that machine is the only copy of the 2021-2025 rides**
  (gitignored, never uploaded anywhere). Only `docs/rides.geojson.gz` is
  committed, and it holds edge counts, not traces -- losing `rides/` loses
  the history irrecoverably.
- Personal data (`rides/*.csv` and everything in `cache/`) is gitignored --
  never commit ride files or force-add ignored paths.
- Citibike trips come from a manual export (a JavaScript payload pasted into
  the browser console on `account.citibikenyc.com`, per
  [fhoffa/code_snippets](https://github.com/fhoffa/code_snippets/blob/master/baywheels/readme.md)),
  then `python -m bike_routes.ingest.citibike <file>`. Not from `update.py`.
  There *is* an API behind it -- a GraphQL endpoint authenticated by the
  browser session cookie -- but nothing automates it today
  ([issue #23](https://github.com/nikhilsaggi/bike-map/issues/23)). The
  ingest is by hand; the summary is automatic on every later run.
- **`ingest.citibike` merges into the trips cache** on Lyft's `rideId`,
  because the export script defaults to a **one-year** window and replacing
  the cache with one silently discarded everything older -- that is what
  truncated the first export. `--replace` still exists for a cache that is
  itself wrong, and an unreadable cache raises rather than being overwritten
  from empty. A cache written before the merge carries no `id`, so `_merge`
  also matches such a record by start time, and only when that start names
  exactly one of them. Check the printed date span before trusting a
  re-ingest, and read `already known` as the overlap check: zero of it means
  the export and the cache do not meet, so a top-up has left a gap.
- **`cache/citibike_trips.json` accumulates what no one export holds.** An
  ordinary pull covers a year, so every year before that survives only in
  that (gitignored) cache and in the original full export -- the same
  only-copy situation as `rides/*.csv`.
- **Check a fresh Citibike export for silent truncation before trusting it.**
  The fetch is cursor-paginated in tens and stops at a one-year cutoff, so an
  export can look like a plausible complete history that merely starts a year
  ago. A pull that ran to exhaustion ends on a short page
  ([details](findings/citibike-trips.md)).

## Documentation

**No first person.** Nothing in this project says "I" or "my" -- not the page,
not the README, not `findings/`, not a tool's printed output. It is one
rider's data, and the prose says "one rider" or nothing at all. **And keep it
short**: a tooltip states the rule and stops, a caption is one line, and a
caveat is written once in the place it belongs rather than everywhere it
could go.

`README.md` is for someone running the pipeline: what the map shows, how to
install it, how to run it, what the knobs are, and where the outputs land.
`findings/` is for the reasoning behind the numbers -- why a design was
chosen, what an experiment showed, what broke and how it was diagnosed.
Keeping those apart is the reason `findings/` exists; direction-split speed,
weather correlation, and Garmin auth all lived in the README first and buried
the instructions.

- **A rationale longer than a paragraph belongs in `findings/`**, linked from
  the README in one line. The exception is a rationale a reader needs in
  order to *use* the thing correctly (why `MATCHER` defaults to `hmm`, why
  `update.py` runs locally) -- that stays inline, trimmed.
- **Changing a default in `config.py` means checking the README's config
  table**, which lists literal values (`SPEED_VERSION` was stale at 2 for
  four bumps). Same for `cli.py` flags, the `cache/` file list, and the
  repository layout block. Prefer adding a row only for parameters a user
  would plausibly change; the file itself is commented for the rest.
- **The map counts passes, not rides** (see Edge passes above). Any README or
  UI wording that says "ride count" or "ride frequency" for a drawn feature
  is wrong -- an out-and-back is two passes on one ride.
- **The README's image is `sample_output/pass_frequency.png`**: the frequency
  render cropped to Manhattan/north Brooklyn with the colorbar and legend
  dropped, written by `tools/render_readme_map.py` and captioned instead. The
  full-graph PNGs a run writes are no longer committed -- rides now reach
  Westchester and eastern Long Island, so that frame is mostly empty black.
- Each README section should be answerable in one place: don't describe the
  matcher in "How It Works" *and* "Map-Matching" with different words, which
  is how the README ended up claiming the default matcher was heading-aware
  snapping.
- `docs/` is the published GitHub Pages site, not a documentation folder --
  prose goes in `findings/`, never there.
- **This file quotes the code too**, and drifts the same way: symbol names,
  the stage order, `RENDER_CACHE_FORMAT`, the ride count in "rematch of 1380
  rides". Verify a number here before repeating it, and prefer a claim that
  stays true (a rule, an invariant, a filename) over one that decays (a
  diagnostic count, a percentage measured once).
