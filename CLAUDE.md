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
6. `render.py` / `export.py` -- PNGs and `docs/rides.geojson.gz`
7. `weather.py` -- Open-Meteo ride-weather stats embedded in the GeoJSON

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
