---
name: benchmark-matching
description: Benchmark or profile the map-matching pipeline against real rides and caches without touching pipeline state. Use when evaluating matcher performance, comparing matcher changes for speed or output parity, or investigating slow rides.
---

# Benchmark map matching

Procedure for measuring matcher speed/quality on real data. The synthetic
test grids in `tests/` are useless for performance work -- benchmark against
the real OSM graph and ride corpus.

## Setup

- Benchmarks need a checkout root containing `rides/*.csv`,
  `cache/osm_graph_cache.pkl`, and (fast path) `cache/hmm_map_cache.pkl`. Run scripts
  with that directory as cwd; import the code under test via
  `sys.path.insert(0, <repo root>)`.
- Write benchmark scripts to a scratch directory, never the repo.
- Scripts that use worker processes need a real file with an
  `if __name__ == "__main__":` guard -- Windows spawn cannot re-import
  `<stdin>` heredocs.

## Rules (learned the hard way)

- **Never call `cache._load_state()` in a benchmark.** On a config-hash
  mismatch it DELETES cache/route_cache.pkl (and possibly graph caches) as a side
  effect. Unpickle `cache/state.pkl` directly instead.
- Redirect all outputs before importing pipeline stages:
  `config.GEOJSON_OUTPUT_PATH`, `config.OUTPUT_PATH_UNWEIGHTED/WEIGHTED`.
- Load the map index via `hmm._load_cached_inmem_map()` (~4s) rather than
  building from the graph; only fall back to `_build_inmem_map(G)`.
- Sample rides with a fixed random seed and report per-ride times -- ride
  sizes are heavy-tailed (median ~150 pts, max ~8,000) and one monster ride
  can dominate any aggregate.

## What to measure

- **Speed**: wall time per ride plus points per ride. Distinguish normal
  rides (~13 ms/pt) from retry-heavy rides (~50 ms/pt, triggered when the
  matcher stops early and re-runs at `HMM_LATTICE_WIDTH_RETRY`).
- **Parity** (for any matcher change): exact edge-sequence equality against
  the unmodified matcher on 15-20 sampled rides. Report count identical and
  set-intersection size for diffs. Result-changing optimizations need the
  quality eval (`tools/hmm_matcher_eval.py`, matched/GPS length-ratio metric)
  before adoption.

## Known results (July 2026, 4-core/16GB machine)

- Bulk InMemMap build ~8s vs ~115s incremental; cache load ~4s.
- XY-projected matching (use_latlon=False) is NOT faster than latlon; tested,
  rejected -- do not revisit.
- Windowed wide-beam retry (keep narrow prefix, re-decode a window around
  the dead end): tested, REJECTED. Only ~1.25x faster and quality regressed
  (p90 length ratio 1.129 -> 1.167; worst ride 1.002 -> 1.209, jaccard
  0.58). The narrow-beam path is worse over the WHOLE ride when it
  dead-ends; the full-track wide retry silently rescues it. Do not revisit
  without a fundamentally different idea.
- 123-ride parallel batch on 6 workers: 110s wall (~18 min projected for the
  full corpus).
