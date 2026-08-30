"""Benchmark the heuristic matcher against a leuvenmapmatching HMM (issue #10).

Runs both matchers on a random sample of processed rides against the cached
OSM graph and reports, per ride, the matched-length / GPS-length ratio
(1.0 is ideal; parallel-way zigzag and block detours inflate it), runtime,
and completion.

Requires the pipeline caches (cache/osm_graph_cache.pkl, cache/state.pkl)
and rides/, plus `pip install leuvenmapmatching rtree`.

Usage:
    python tools/hmm_matcher_eval.py [--rides N]   # from the repo root

Findings as of 2026-07 (12-ride sample): heuristic matches 12/12 at median
ratio 2.89 in 0.3s total; the HMM completes 9/12 at median ratio 1.09 in
14.5s total. See the issue for the adoption plan.
"""

from __future__ import annotations

import argparse
import pickle
import random
import time

import numpy as np
from leuvenmapmatching.map.inmem import InMemMap
from leuvenmapmatching.matcher.distance import DistanceMatcher

from bike_routes import config
from bike_routes.cache import _load_route_cache
from bike_routes.gps import _load_and_resample, haversine_m
from bike_routes.matching import _build_snap_tree, _map_match_ride


def _gps_len(coords: np.ndarray) -> float:
    return float(haversine_m(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1]).sum())


def main() -> None:
    """Run the benchmark and print per-ride and summary stats."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--rides", type=int, default=12, metavar="N", help="number of ride files to sample"
    )
    args = parser.parse_args()

    print("Loading graph...", flush=True)
    with config.GRAPH_CACHE_PATH.open("rb") as f:
        G = pickle.load(f)
    with config.STATE_CACHE_PATH.open("rb") as f:
        state = pickle.load(f)

    rng = random.Random(42)  # noqa: S311 -- reproducible sampling, not crypto
    sample_files = rng.sample(sorted(state["processed_files"]), args.rides)
    rides, _ = _load_and_resample(sample_files)
    print(f"{len(rides)} ride segments from {args.rides} files", flush=True)

    def edge_len(u: int, v: int) -> float:
        d = G[u][v] if G.has_edge(u, v) else G[v][u]
        return min(dd.get("length", 0) for dd in d.values())

    def path_len(edges: list[tuple[int, int]]) -> float:
        return sum(edge_len(u, v) for u, v in edges)

    # -- Current heuristic matcher --
    print("Building snap tree...", flush=True)
    snap_data = _build_snap_tree(G)
    route_cache = _load_route_cache()
    cur_ratios: list[float] = []
    cur_time = 0.0
    for fname, coords in rides:
        t0 = time.time()
        edges, skipped = _map_match_ride(
            G, coords, config.SNAP_TOLERANCE_M, route_cache, *snap_data
        )
        cur_time += time.time() - t0
        g = _gps_len(coords)
        m = path_len(edges) if edges else 0.0
        if g and m:
            cur_ratios.append(m / g)
        print(
            f"  CUR {fname[:16]} gps={g / 1000:.1f}km matched={m / 1000:.1f}km "
            f"ratio={m / g if g else 0:.3f} skip={skipped}",
            flush=True,
        )

    # -- leuvenmapmatching HMM --
    print("Building InMemMap (bbox around sample rides)...", flush=True)
    pts = np.vstack([c for _, c in rides])
    lat0, lat1 = pts[:, 0].min() - 0.01, pts[:, 0].max() + 0.01
    lon0, lon1 = pts[:, 1].min() - 0.01, pts[:, 1].max() + 0.01
    mmap = InMemMap("nyc", use_latlon=True, use_rtree=True, index_edges=True)
    node_set: set[int] = set()
    for nid, data in G.nodes(data=True):
        if lat0 <= data["y"] <= lat1 and lon0 <= data["x"] <= lon1:
            mmap.add_node(nid, (data["y"], data["x"]))
            node_set.add(nid)
    n_edges = 0
    for u, v in G.edges():
        if u in node_set and v in node_set:
            mmap.add_edge(u, v)
            n_edges += 1
    print(f"  {len(node_set):,} nodes, {n_edges:,} edges", flush=True)

    hmm_ratios: list[float] = []
    hmm_time = 0.0
    n_complete = 0
    for fname, coords in rides:
        track = [(float(la), float(lo)) for la, lo in coords]
        matcher = DistanceMatcher(
            mmap,
            max_dist=80,
            max_dist_init=80,
            obs_noise=15,
            obs_noise_ne=30,
            min_prob_norm=0.001,
            non_emitting_states=True,
            max_lattice_width=8,
        )
        t0 = time.time()
        try:
            states, last_idx = matcher.match(track)
            edges = []
            for u, v in states:
                if u != v and (not edges or edges[-1] != (u, v)):
                    edges.append((u, v))
            m = path_len(edges)
            done = last_idx >= len(track) - 1
        except Exception as exc:
            m, done, last_idx = 0.0, False, 0
            print(f"  HMM {fname[:16]} FAILED: {exc!r}", flush=True)
        hmm_time += time.time() - t0
        g = _gps_len(coords)
        if done:
            n_complete += 1
            if g and m:
                hmm_ratios.append(m / g)
        print(
            f"  HMM {fname[:16]} gps={g / 1000:.1f}km matched={m / 1000:.1f}km "
            f"ratio={m / g if g else 0:.3f} complete={done} "
            f"({last_idx + 1}/{len(track)})",
            flush=True,
        )

    print("\n=== SUMMARY ===")
    print(
        f"heuristic: {len(cur_ratios)}/{len(rides)} matched, "
        f"median ratio {sorted(cur_ratios)[len(cur_ratios) // 2]:.3f}, {cur_time:.1f}s"
    )
    if hmm_ratios:
        print(
            f"HMM:       {n_complete}/{len(rides)} complete, "
            f"median ratio {sorted(hmm_ratios)[len(hmm_ratios) // 2]:.3f}, {hmm_time:.1f}s"
        )


if __name__ == "__main__":
    main()
