# CLAUDE.md

NYC bike route visualization: an incremental pipeline that map-matches personal
GPS rides (CSV in `rides/`) onto the OSM street network and publishes an
interactive Leaflet map (`docs/`, served via GitHub Pages) plus static PNGs.

## Commands

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
  adding ignores)
- Format: `ruff format .` -- run after every change; CI fails on
  unformatted code (`ruff format --check .`).
- `ty check` runs but is not enforced; ~28 known diagnostics, mostly
  Optional-narrowing in tests.

## Architecture

`bike_routes/` package, one stage per module:

1. `gps.py` -- load CSVs, filter to NYC, resample to 20m spacing
2. `graph.py` -- fetch/merge OSM networks (bike+drive+walk), cache to pickle
3. `hmm.py` / `matching.py` -- map-match rides to edges. `MATCHER = "hmm"`
   (leuvenmapmatching Viterbi) is the default; the "heuristic" snap+route
   matcher is kept for comparison. Parallel matching via worker processes
   in `matching.py`.
4. `cache.py` -- cache/state.pkl (processed files, edge counts), config-hash
   invalidation
5. `merge.py` -- collapse parallel/duplicate edge geometries into corridors
6. `edge_speed.py` -- per-edge passes, backfilled from the ride CSVs'
   timestamps (see below): direction-split speed, and how many times each
   ride traversed each edge (the map's frequency). Needs `edge_geom`, so it
   runs from `cli._finalize` rather than the matching checkpoint
7. `render.py` / `export.py` -- PNGs and `docs/rides.geojson.gz`
8. `weather.py` -- Open-Meteo ride-weather stats embedded in the GeoJSON

`bike_routes/ingest/` is the front of the pipeline (`garmin_sync`, `gpx_to_csv`),
run as `python -m bike_routes.ingest.<mod>`; it fills `rides/` and is not
imported by any pipeline stage. `tools/` holds standalone analysis that is not
part of the pipeline at all (`hmm_matcher_eval.py`, `weather_correlation.py`,
`traversal_audit.py`),
run from the repo root.

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
  root on startup -- a rename, because the graph cache is ~260 MB and only
  exists on the owner's machine, and because `shutil.move` keeps the mtimes
  the check below depends on.
- Caches are mtime/version-invalidated: cache/hmm_map_cache.pkl must be newer
  than cache/osm_graph_cache.pkl; graph cache is bound to osmnx/networkx
  versions.

### Edge passes (`edge_speed.py`)

One pass detector, two outputs: direction-split speed, and per-ride traversal
counts. Both are backfilled from the ride CSVs' timestamps.

**Speed** is a **stats-panel ranking, not a map layer**: measured across the
whole network, only ~6% of drawn km ever has enough passes in both directions
to compare them, so colouring features by it produced a 94%-empty map. The
export ships a top-10 corridor list in `properties.speed` instead
(+0.5 KB); features carry no speed key and `merge.py` never sees it.

- `state["edge_speed"]` / `state["edge_traversals"]` / `state["speed_rides"]`
  are deliberately **outside**
  `_processing_config()`: both come from timestamps the matcher never saw,
  so changing them must never trigger a rematch of 1380 rides. Their
  invalidation lever is `config.SPEED_VERSION` -- bump it and the next run
  discards and recomputes (`_records_well_formed` is a fail-closed shape
  guard for records that predate a layout change). Editing the algorithm
  without bumping it leaves stale records in place, silently.
- **Forward means "along the stored vertex order of `edge_geom[key]`", not
  `min(u,v)` -> `max(u,v)`.** `render.py` builds `edge_geom[canon]` from
  whichever *directed* edge won the shortest-edge tie-break, so ~9.5% of
  geometries run max->min. Anchoring direction to the node key would invert
  a scattered subset. `_oriented_chunks` re-expresses a record against the
  current geometry, so a rebuilt render cache cannot silently flip it.
- Records are per-chunk (~`SPEED_CHUNK_M`), not per-edge: the Manhattan
  Bridge bike path is a single 2163 m edge whose climb cancels its descent
  (10.37 vs 10.23 mph -- no signal), and the street grid's median 63 m edge
  stays one chunk regardless.
- Each direction bucket is `[dist, time, moving, n]`, so it combines by
  addition and survives chunked folding like `edge_counts`.
- **`_top_corridors` splits a corridor wherever the faster direction flips**,
  and that is the point rather than an implementation detail: a bridge's
  entire signal is the crest reversal, so it must be reported as its two
  descents. Runs are disjoint by construction, so one stretch can never
  appear twice; two rows for one street are always different stretches.
- Street names come from the render cache (`edge_name`, `RENDER_CACHE_FORMAT`
  = `hw-name-v1`). Bumping that format costs one graph load to rebuild; it
  does not touch the config hash.
- **Regression oracle for any change here:** the Manhattan Bridge
  (`edge_geom[(1371803831, 7480410407)]`) must appear twice in the ranking,
  SE and NW, ~5 mph each. Collapsing to one row means the sign-change split
  broke; a single direction means orientation broke. A unit test on a
  synthetic grid cannot catch either -- grid orientation is uniform.

#### Traversal counts

`state["edge_traversals"][key][ride]` is how many times one ride crossed one
edge, and it is what the map colours by. **Never derive this from the
matcher's edge list**: it collapses consecutive repeats, and its
non-consecutive repeats cannot be told apart from lattice oscillation at an
intersection (`A->B->C->B->A` emits `(A,B)` twice for one pass). Passes can,
because they come from the raw fixes.

- **Only counts >= 2 are stored**, and every reader goes through
  `ride_traversals()`, which floors at 1. So a ride the detector could not
  measure -- unparsable timestamps, a trace beyond `SPEED_SNAP_M`, a pass
  under `SPEED_MIN_PASS_M` -- still draws its edges exactly as before.
  Measurement can raise a count; it can never take an edge off the map.
- `_runs` cuts at `SPEED_MAX_FIX_GAP_S`, which is right for speed (a red
  light is not riding time) and wrong for counting. `_merge_resumed` re-joins
  same-direction passes that restart within `TRAVERSAL_RESUME_M` of where the
  last stopped; a second lap re-enters from the far end instead. Removing it
  scores every mid-block stop as an extra traversal.
- `merge.py` combines a corridor's parallel members by **max** per ride
  (`_merge_ride_counts`), not sum: one pass drifting from a street to its bike
  lane must not become two. The cost is an out-and-back on separately mapped
  directions reading as one. `tools/traversal_audit.py --merge` measures how
  often the two rules actually disagree.
- The export ships `properties.rides` with one entry per traversal, so the
  page's count is array length and needs no new field. Equal filenames map to
  equal indices, so the array stays sorted for `hasRide`'s binary search.
- **Before changing any threshold here, run `tools/traversal_audit.py`** on
  the real rides: it prints the inflation ratio (a few percent over 1.0 is
  what real riding looks like) and the highest-multiplicity (ride, edge)
  pairs to check against the trace. A synthetic grid cannot tell you whether
  a threshold over-fires on real GPS.

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
  authenticated from a
  token in `~/.garminconnect` (override with `GARMINTOKENS`). Keep it
  local: Garmin's login is behind Cloudflare TLS fingerprinting that blocks
  datacenter IPs, and the ride CSVs plus the ~260 MB graph cache only exist
  on the owner's machine. The previous Dropbox-based `update-map.yml` failed
  all 8 of its scheduled runs and was removed rather than fixed.
- **`rides/*.csv` on that machine is the only copy of the 2021-2025 rides**
  (gitignored, never uploaded anywhere). Only `docs/rides.geojson.gz` is
  committed, and it holds edge counts, not traces -- losing `rides/` loses
  the history irrecoverably.
- Personal data (`rides/*.csv` and everything in `cache/`) is gitignored --
  never commit ride files or force-add ignored paths.
