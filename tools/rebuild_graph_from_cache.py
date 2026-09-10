"""Rebuild the OSM graph from osmnx's cached Overpass responses, with no network.

The pipeline refetches the graph whenever `cache._graph_cache_valid()` rejects
the pickle -- an osmnx or networkx upgrade is enough.  That refetch needs
Overpass, and Overpass is a single volunteer-run service that goes down: on
2026-09-09 it refused every connection for over five hours, which is long
enough to leave a machine with an invalidated graph and no way to draw its map.

osmnx caches every Overpass response it has ever received under `cache/`, keyed
by query hash, and assembles its own chunked queries by handing a *list* of
those responses to `graph._create_graph`.  The same thing works with the cached
ones, so the graph can be rebuilt from disk alone.

Two things make the result better than a single fetch rather than merely equal
to one.  The responses accumulate across every region ever fetched, so their
union covers more ground than the last fetch did -- a corridor fetched for one
long ride is still there after a later run narrowed the region.  And the
responses are the output of osmnx's own network filters, so the node ids that
survive simplification are the ones `state["edge_rides"]` is keyed on: a
rebuild does not invalidate the matching already in state.

What it cannot do is see OSM edits made since the newest cached response, and
it can only cover ground some past fetch asked for.  It is a recovery path for
a graph that is gone or wrong, not a substitute for `python -m bike_routes`.

Reads `cache/*.json` and `state["graph_region"]`; writes `cache/osm_graph_cache.pkl`
and `cache/cache_versions.json`, and only with --write.  Delete
`cache/render_cache.pkl` afterwards or the next run will load the stale one
without ever consulting the graph.

Usage:
    python tools/rebuild_graph_from_cache.py [--write]   # from the repo root
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import re
import sys
from typing import TYPE_CHECKING, Any

import networkx as nx
import osmnx as ox
from osmnx import settings, simplification, stats, truncate
from osmnx._errors import InsufficientResponseError
from osmnx.graph import _create_graph
from osmnx.projection import project_geometry

from bike_routes import config
from bike_routes.cache import _write_cache_versions
from bike_routes.graph import _region_km2, _remove_subsumed_edges, _stored_region

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from shapely.geometry.base import BaseGeometry

# Each network filter admits exactly one highway value the other two exclude,
# so the presence of that value identifies a response unambiguously:
#   footway  -- walk only  (osmnx's bike and drive filters both exclude it)
#   motorway -- drive only (bike and walk exclude it, via the substring "motor")
#   cycleway -- bike only  (drive and walk both exclude it)
# Match the whole key/value pair: "cycleway:right"="lane" is a tag on an
# ordinary road, not a cycleway, and counting bare "cycleway" reads a drive
# response as a bike one.
MARKERS = {
    "walk": re.compile(rb'"highway":\s*"footway"'),
    "drive": re.compile(rb'"highway":\s*"motorway"'),
    "bike": re.compile(rb'"highway":\s*"cycleway"'),
}

CHUNK = 1 << 24
SLICE = 1 << 20
# Longest marker plus its formatting, so a match split across two reads is
# still seen when the tail is carried forward.
OVERLAP = 60


def _is_overpass_response(path: Path) -> bool:
    """Report whether a cached JSON file is an Overpass response.

    `cache/` also holds weather, Citibike and boundary JSON, none of which
    carries the Overpass generator banner.
    """
    with path.open("rb") as f:
        return b'"generator": "Overpass' in f.read(200)


def _classify(path: Path) -> tuple[str | None, dict[str, int]]:
    """Return the network type a response was fetched for, and the marker counts.

    None where no marker appears at all, which is what an empty or errored
    response looks like.
    """
    counts = dict.fromkeys(MARKERS, 0)
    with path.open("rb") as f:
        tail = b""
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            buf = tail + chunk
            for network_type, pattern in MARKERS.items():
                counts[network_type] += len(pattern.findall(buf))
            tail = buf[-OVERLAP:]
    best = max(counts, key=lambda k: counts[k])
    return (best if counts[best] else None), counts


def _fingerprint(path: Path) -> str:
    """Identify re-fetches of the same response without hashing gigabytes.

    Deliberately skips the head of the file: every response opens with an
    `osm3s.timestamp_osm_base`, so two fetches of identical data at different
    times differ in their first few hundred bytes and nowhere else.  Size plus
    two slices from the body is enough to tell those apart from a response
    covering genuinely different ground.
    """
    size = path.stat().st_size
    digest = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        for fraction in (3, 2):
            offset = size // fraction
            if offset + SLICE <= size:
                f.seek(offset)
                digest.update(f.read(SLICE))
    return digest.hexdigest()


def _newest_of_each(paths: list[Path]) -> list[Path]:
    """Drop re-fetched duplicates, keeping the newest copy, oldest-first.

    Order matters downstream: `_create_graph` keys by OSM id, so the last
    response holding an id wins, and the newest data should be the one that
    survives.
    """
    seen: dict[str, Path] = {}
    for path in paths:
        seen[_fingerprint(path)] = path
    return sorted(seen.values(), key=lambda p: p.stat().st_mtime)


def _responses(paths: list[Path]) -> Iterator[dict[str, Any]]:
    """Yield parsed responses one at a time.

    `_create_graph` takes an iterable, and the walk responses alone run to
    hundreds of megabytes, so they are never all held at once.
    """
    for path in paths:
        with path.open() as f:
            yield json.load(f)


def _group_responses(cache_dir: Path) -> tuple[dict[str, list[Path]], list[Path]]:
    """Sort every cached Overpass response in cache_dir by network type."""
    groups: dict[str, list[Path]] = {}
    unknown: list[Path] = []
    for path in sorted(cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime):
        if not _is_overpass_response(path):
            continue
        network_type, counts = _classify(path)
        marks = " ".join(f"{k}={v:,}" for k, v in counts.items())
        print(
            f"  {path.name[:8]}  {path.stat().st_size / 1e6:7.1f}MB  {marks:<44} -> {network_type or 'unknown'}"
        )
        if network_type is None:
            unknown.append(path)
        else:
            groups.setdefault(network_type, []).append(path)
    return groups, unknown


def _build_one(
    network_type: str,
    paths: list[Path],
    polygon: BaseGeometry,
    poly_buff: BaseGeometry,
) -> nx.MultiDiGraph:
    """Reproduce `ox.graph_from_polygon` for one network type, from cached responses.

    Mirrors that function step for step so the surviving node ids match a
    fetched graph's.  It reaches into osmnx internals to do so and will need
    revisiting if `graph_from_polygon` changes shape.
    """
    bidirectional = network_type in settings.bidirectional_network_types
    G = _create_graph(_responses(paths), bidirectional)
    print(f"    raw        {G.number_of_nodes():>9,} nodes {G.number_of_edges():>9,} edges")
    G = truncate.truncate_graph_polygon(G, poly_buff, truncate_by_edge=False)
    G = truncate.largest_component(G, strongly=False)
    G = simplification.simplify_graph(G)
    G_final = truncate.truncate_graph_polygon(G, polygon, truncate_by_edge=False)
    # Street counts are taken over the buffered graph so an intersection at the
    # region's edge keeps neighbours that fall outside it, as osmnx does.
    nx.set_node_attributes(
        G_final, values=stats.count_streets_per_node(G, nodes=G_final.nodes), name="street_count"
    )
    print(
        f"    simplified {G_final.number_of_nodes():>9,} nodes {G_final.number_of_edges():>9,} edges"
    )
    return G_final


def _compose(graphs: list[nx.MultiDiGraph]) -> nx.MultiDiGraph:
    """Merge the per-network graphs the way `graph._fetch_graph` does."""
    G = nx.compose_all(graphs)
    print(f"  Removed {_remove_subsumed_edges(G):,} subsumed edges")
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    print(f"  Merged: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    return G


def _load_region() -> BaseGeometry:
    """Return the region to truncate to, or exit saying how to get one."""
    if not config.STATE_CACHE_PATH.exists():
        msg = f"{config.STATE_CACHE_PATH} not found -- run from the repo root"
        raise SystemExit(msg)
    with config.STATE_CACHE_PATH.open("rb") as f:
        state = pickle.load(f)
    region = _stored_region(state)
    if region is None:
        msg = (
            "state carries no graph_region, so there is nothing to truncate to.\n"
            "The region is only written on a fetch, so a state whose graph came\n"
            "from the cache can lack one -- derive it with graph._fetch_region\n"
            "over the ride tracks and store it before rebuilding."
        )
        raise SystemExit(msg)
    return region


def main() -> None:
    """Rebuild the graph cache from cached Overpass responses."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="save the graph to cache/; without it the rebuild only reports what it would produce",
    )
    args = parser.parse_args()

    region = _load_region()
    proj, crs = project_geometry(region)
    poly_buff, _ = project_geometry(proj.buffer(500), crs=crs, to_latlong=True)
    print(f"Region: {_region_km2(region):,.0f} km2")

    groups, unknown = _group_responses(config.CACHE_DIR)
    print()
    for network_type in config.NETWORK_TYPES:
        paths = groups.get(network_type, [])
        print(
            f"{network_type:>6}: {len(paths)} responses, {sum(p.stat().st_size for p in paths) / 1e6:,.0f} MB"
        )
    if unknown:
        print(f"{'skipped':>6}: {len(unknown)} responses with no usable elements")

    graphs = []
    for network_type in config.NETWORK_TYPES:
        if not groups.get(network_type):
            msg = f"no cached responses for the {network_type!r} network -- a fetch is needed"
            raise SystemExit(msg)
        paths = _newest_of_each(groups[network_type])
        print(f"  {network_type}: {len(groups[network_type])} responses -> {len(paths)} distinct")
        try:
            graphs.append(_build_one(network_type, paths, region, poly_buff))
        except InsufficientResponseError:
            msg = f"cached {network_type!r} responses held no usable elements"
            raise SystemExit(msg) from None

    G = _compose(graphs)
    del graphs

    if not args.write:
        print("  (dry run -- pass --write to save)")
        return
    with config.GRAPH_CACHE_PATH.open("wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    _write_cache_versions()
    print(f"  Cached to {config.GRAPH_CACHE_PATH}")
    if config.RENDER_CACHE_PATH.exists():
        print(f"  Now delete {config.RENDER_CACHE_PATH}, or the next run will use the stale one")


if __name__ == "__main__":
    sys.exit(main())
