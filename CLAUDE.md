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
  per-neighbourhood block. All read state; none writes it.

## Architecture

`bike_routes/` package, one stage per module:

1. `gps.py` -- load CSVs, filter to NYC, resample to 20m spacing
2. `graph.py` -- fetch/merge OSM networks (bike+drive+walk), cache to pickle
3. `hmm.py` / `matching.py` -- map-match rides to edges. `MATCHER = "hmm"`
   (leuvenmapmatching Viterbi) is the default; the "heuristic" snap+route
   matcher is kept for comparison. Parallel matching via worker processes
   in `matching.py`.
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

`bike_routes/ingest/` is the front of the pipeline (`garmin_sync`, `gpx_to_csv`,
`citibike`), run as `python -m bike_routes.ingest.<mod>`; it fills `rides/`
(and, for `citibike`, `cache/citibike_trips.json` -- dock trips are not GPS
traces and must never land in `rides/`) and is not imported by any pipeline
stage. `tools/` holds standalone analysis that is not
part of the pipeline at all (`hmm_matcher_eval.py`, `weather_correlation.py`,
`traversal_audit.py`, `neighborhood_audit.py`, `bike_reencounters.py` -- which
alone among them imports nothing from `bike_routes`, so it runs from the trips
JSON on a checkout with no pipeline deps), plus `render_readme_map.py`,
which crops the README's image out of the same caches; all are run from the
repo root. `findings/`
holds the write-ups of what that analysis found (moved out of the README to
keep it about running the pipeline).

The package `__init__` deliberately exports nothing -- import the stage you
need (`from bike_routes import edge_speed`). It used to re-export ~150 names
flat, which made every import pull in osmnx + matplotlib and hid which module
owned what; don't reintroduce that.

`docs/index.html` is a single self-contained Leaflet page (no build step); it
reads everything from `rides.geojson.gz` top-level `properties`.

## Invariants

- **Changing any parameter in `cache._processing_config()` triggers a full
  reprocess** of all rides (config hash mismatch discards cache/state.pkl). Don't
  add keys to it unless the change genuinely invalidates prior matches.
- Edge keys are canonical `(min(u,v), max(u,v))` node pairs everywhere.
- **The map's frequency is traversals, not rides**, and they come from
  `edge_speed`, never from the matcher (see below). `state["edge_counts"]` and
  `edge_rides` stay per-ride: `_apply_results` counts a file once even when a
  GPS gap split it into several segments.
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
  `properties.citibike.trips` row (`-1` where none), so a popup row can put
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
  pair deliberately leaves the popup open (`viewRide(ri, keepPopup)`): the
  rows are how a reader walks the network.
- **A GPS ride is matched to Citibike trips by clock overlap**
  (`citibike.ride_sources`), shipped as the 4th element of each row in the
  export's `rides` array: `-1` unknown, `0` own bike, `n>=1` the number of
  trips it overlaps. **`0` and `-1` are not the same claim** -- outside the
  export's window there is no evidence either way, so those rides are
  unknown, and the page's source filter hides them from both sides rather
  than counting them as own-bike. The 60s minimum overlap is not a tuned
  threshold: anything from 1s to 120s gives the same answer on the real
  rides.
- **The map's coverage number has two denominators and both ship.**
  `coverage.pct` is measured over every rideable edge in the graph, and the
  graph is the rides' own bounding box -- half of it is not in New York City,
  so riding further out *lowers* it. `properties.neighborhoods` carries the
  same measurement over the part inside a NYC neighbourhood (9.1% against
  5.1%), and that is what the "of NYC" tile shows, because that is what the
  label claims. Neither is a share of the whole city: the box has never
  reached Staten Island ([details](findings/neighborhoods.md)).
- **A neighbourhood is filled by coverage as of the date on screen**, not
  all-time, so the slider and the time-lapse move it the way they move the
  edges and the dock markers. The export ships `new` -- [date index, metres
  first ridden that day] -- and the page takes a running total up to
  `filterHi`; it follows the range's upper end alone, because "how much had I
  ridden by then" is a running total, and the popup says so. Areas are placed
  by edge midpoint, which misplaces 4.9% of ridden metres, nearly all of it
  on ten named bridges and waterfront paths -- fine for a fill colour, not
  for anything stronger.
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
  the other 93, as "unlocks on a bike ridden before";
  **never report the raw 253 as "bikes ridden more than
  once"**, which is the wording this replaced and which counts a person taking
  their own bike home. The export ships `resumes` beside `reencounters` and
  nothing draws it -- it is there so the 93 can be checked against what it was
  cut from. Never derive a *rate* from any of it without the exposure: an
  ebike can only re-meet an ebike already ridden, which is what made ebikes
  look 7x rarer than they are ([why](findings/bike-reencounters.md), rerun
  with `python tools/bike_reencounters.py`).
- **The panel's re-encounter list is a way into the map, not a readout.**
  `_met_rows` ships `properties.citibike.met` as `[id, trips, rides]` per bike
  met again, and a chip opens that bike's recordings in single-ride view --
  the same cycle a dock row opens, through the same `dockTrace`. That cycle
  now has two owners, so controls compare `dockTrace.key` (`d:<dock>` or
  `b:<id>`) rather than `.to`, and only a dock cycle sets `.to`, which is what
  narrows the drawn links. **A bike with no recording keeps its chip**, dimmed
  -- it was met, and dropping it to make the list all-clickable would shrink
  the count to suit the renderer.
- **A chance-test chart was built here, drawn four ways, and taken off.** The
  two permutation tests live in `tools/bike_reencounters.py` now, and a result
  needing a p-value belongs there rather than on the panel. Two of those four
  versions were wrong in ways only rendering revealed -- band histograms (93
  events over 4 bands is all noise) and a percentile axis (a middle-95% band
  covers 95% of the track by construction). If a chart is ever reinstated
  here, read [why each failed](findings/bike-reencounters.md) first, and note
  that the working one still lost to a list you can click.
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
  Each direction bucket is `[dist, time, moving, n]`, so it combines by
  addition and survives chunked folding like `edge_counts`.
- **`_top_corridors` splits a corridor wherever the faster direction flips**,
  and that is the point, not an implementation detail: a bridge's entire
  signal is the crest reversal, so it must be reported as its two descents.
  Runs are disjoint, so two rows for one street are always different
  stretches.
- Street names come from the render cache (`edge_name`, `RENDER_CACHE_FORMAT`
  = `hw-name-v1`). Bumping that format costs one graph load to rebuild; it
  does not touch the config hash.

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
