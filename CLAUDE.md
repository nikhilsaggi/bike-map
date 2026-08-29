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
4. `cache.py` -- state.pkl (processed files, edge counts), config-hash
   invalidation
5. `merge.py` -- collapse parallel/duplicate edge geometries into corridors
6. `edge_speed.py` -- direction-split per-edge speed, backfilled from the
   ride CSVs' timestamps (see below); needs `edge_geom`, so it runs from
   `cli._finalize` rather than the matching checkpoint
7. `render.py` / `export.py` -- PNGs and `docs/rides.geojson.gz`
8. `weather.py` -- Open-Meteo ride-weather stats embedded in the GeoJSON

`docs/index.html` is a single self-contained Leaflet page (no build step); it
reads everything from `rides.geojson.gz` top-level `properties`.

## Invariants

- **Changing any parameter in `cache._processing_config()` triggers a full
  reprocess** of all rides (config hash mismatch discards state.pkl). Don't
  add keys to it unless the change genuinely invalidates prior matches.
- Edge keys are canonical `(min(u,v), max(u,v))` node pairs everywhere.
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
  disk (graph cache / hmm_map_cache.pkl), never closure state.
- Caches are mtime/version-invalidated: hmm_map_cache.pkl must be newer than
  osm_graph_cache.pkl; graph cache is bound to osmnx/networkx versions.

### Edge speed (`edge_speed.py`)

- `state["edge_speed"]` / `state["speed_rides"]` are deliberately **outside**
  `_processing_config()`: speed comes from timestamps the matcher never saw,
  so changing it must never trigger a rematch of 1380 rides. Its own
  invalidation lever is `config.SPEED_VERSION` -- bump it and the next run
  discards and recomputes (`_records_well_formed` is a fail-closed shape
  guard for records that predate a layout change). Editing the algorithm
  without bumping it leaves stale records in place, silently.
- **Forward means "along the stored vertex order of `edge_geom[key]`", not
  `min(u,v)` -> `max(u,v)`.** `render.py` builds `edge_geom[canon]` from
  whichever *directed* edge won the shortest-edge tie-break, so ~9.5% of
  geometries run max->min. Anchoring direction to the node key would invert
  a scattered subset against the line actually drawn. Backfill, merge, and
  client all use the geometry convention; no bearing is shipped.
- Records are per-chunk (~`SPEED_CHUNK_M`), not per-edge: the Manhattan
  Bridge bike path is a single 2163 m edge whose climb cancels its descent,
  so a whole-edge average shows nothing. `export.py` emits one feature per
  chunk via `_chunk_slices`; the slices share boundary vertices, so the
  drawn line is unchanged.
- Every direction bucket is `[dist, time, moving, n]` per direction, so it
  combines by addition and survives chunked folding like `edge_counts`.
- **`merge.py` has three places a geometry is replaced or combined, and all
  three must reorient the buckets**: the Phase-1 cluster keep
  (`_cluster_speed`), the Phase-2 absorb (`_speed_add`), and
  `_harmonize_representatives` (`_reorient_speed`, before it swaps in an
  alt). `MERGE_HEADING_DEG` matches mod 180, so anti-parallel geometries do
  cluster; adding unflipped buckets reverses a corridor silently.
  Orientation comes from `_relative_orientation` (along-parameter
  correlation, chord only as fallback -- 6.5% of features have a chord under
  half their length).
- **Regression oracle for any change here:** the Manhattan Bridge
  (`edge_geom[(1371803831, 7480410407)]`) must show southbound faster on the
  Brooklyn half and northbound faster on the Manhattan half, with the
  direction gap swinging from about -8.5 mph to +6.8 mph across the span. An orientation bug flips the sign;
  a unit test on a synthetic grid cannot catch it because grid orientation
  is uniform.

## Performance notes

- Matching is the hot path; leuvenmapmatching is pure Python. Profile before
  optimizing and benchmark against real rides + caches (repo root of a full
  checkout with `rides/` and `*.pkl` present), not the synthetic test grids.
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
- Ride ingest is Garmin Connect (`garmin_sync.py`), authenticated from a
  token in `~/.garminconnect` (override with `GARMINTOKENS`). Keep it
  local: Garmin's login is behind Cloudflare TLS fingerprinting that blocks
  datacenter IPs, and the ride CSVs plus the ~260 MB graph cache only exist
  on the owner's machine. The previous Dropbox-based `update-map.yml` failed
  all 8 of its scheduled runs and was removed rather than fixed.
- **`rides/*.csv` on that machine is the only copy of the 2021-2025 rides**
  (gitignored, never uploaded anywhere). Only `docs/rides.geojson.gz` is
  committed, and it holds edge counts, not traces -- losing `rides/` loses
  the history irrecoverably.
- Personal data (`rides/*.csv`, caches, `weather_cache.json`) is gitignored --
  never commit ride files or force-add ignored paths.
