"""Per-neighborhood coverage for the interactive map.

The page had exactly one geographic statistic -- the citywide coverage
percentage -- and neighborhood is the unit a reader of a NYC bike map
already thinks in.  This module cuts the same measurement the citywide
number makes into NYC's 2020 Neighborhood Tabulation Areas and ships it as
the ``properties.neighborhoods`` block: one polygon per area, the rideable
network inside it, and the metres of that network first ridden on each date.

Two things follow from doing it this way rather than as a ranked table:

- **Coverage of a neighborhood is not coverage of the graph.**  The graph is
  built from the rides' own bounding box (``graph._compute_bbox``), so a
  single ride out to Long Island enlarges the citywide denominator and lowers
  the percentage.  Half the rideable network in the graph is outside every
  NYC neighborhood.  A per-area denominator does not have that problem, and
  the areas summed give the coverage of New York City rather than of the box.
- **The block ships the dates, not a summary of them.**  The page's slider
  and time-lapse already move the edges and the dock markers; giving each
  area the metres it first gained on each day lets them move the
  neighborhood layer too, so a reader watches the city fill in instead of
  reading a number someone else picked out.

Like ``citibike``, this is a top-level properties block computed inline in
``export.py`` -- no pipeline stage, no state key, no key in
``cache._processing_config()``, and ``None`` when the boundary cache is
absent and cannot be fetched.

Areas are assigned by edge midpoint, which is exact for a street and wrong
for a bridge: 4.9% of ridden metres sit outside the area their midpoint
falls in, nearly all of it on the handful of long waterfront paths and
bridge decks that span a boundary by design
([details](../findings/neighborhoods.md)).
"""

from __future__ import annotations

import json
import urllib.request
from typing import TYPE_CHECKING, Any

from . import config
from .merge import _geom_len_m

if TYPE_CHECKING:
    from collections.abc import Sequence

# NYC Open Data, 2020 Neighborhood Tabulation Areas (262 polygons, 4.6 MB).
# The vintage is part of the identity: NTA boundaries were redrawn for 2020
# and a mix of vintages would double-count the areas that changed.
NTA_SOURCE_URL = (
    "https://data.cityofnewyork.us/api/geospatial/9nt8-h7nd?method=export&format=GeoJSON"
)
NTA_VINTAGE = "NYC Neighborhood Tabulation Areas 2020 (9nt8-h7nd)"

# How far outside a polygon an edge midpoint may sit and still belong to it.
# Shorelines are digitised from a different survey than the street network,
# so waterfront paths and bridge decks routinely land tens of metres offshore
# of every polygon; without this they would read as "not in New York City".
# Beyond it, the midpoint really is outside the city -- the graph reaches
# Long Island and New Jersey.
BOUNDARY_TOLERANCE_M = 55.0

# Polygon simplification for the browser, in degrees (~11 m).  The raw file
# is 1.6 MB gzipped, far more than the whole rides payload; at this tolerance
# the 240 shipped areas cost ~67 KB and no boundary moves by more than the
# width of the street it runs down.
SIMPLIFY_DEG = 0.0001
COORD_PRECISION = 5  # ~1 m; matches the export's own rounding

# Slots 1 and 5 of an edge_speed chunk record are the forward and reverse
# elapsed seconds; see edge_speed._new_chunk for the full eight-slot layout.
_TIME_FWD, _TIME_REV = 1, 5

BOROUGH_ABBR = {
    "Manhattan": "Mn",
    "Brooklyn": "Bk",
    "Queens": "Qn",
    "Bronx": "Bx",
    "Staten Island": "SI",
}


def _fetch_boundaries() -> dict[str, Any]:
    """Download the NTA boundaries from NYC Open Data."""
    with urllib.request.urlopen(NTA_SOURCE_URL, timeout=120) as resp:
        return json.load(resp)


def ensure_boundaries() -> bool:
    """Download the boundary cache if it is not already there.

    Called once from ``cli.main``, next to the other cache bootstrapping, and
    never from the export: a 4.6 MB download has no business happening inside
    a function whose job is to write a file, and keeping the network out of
    ``_export_geojson`` is what lets the export tests stay offline.

    Unlike the weather cache this is never refreshed -- boundaries do not
    change between runs, so a present cache is always used as-is.
    """
    path = config.NTA_CACHE_PATH
    if path.exists():
        return True
    print("Fetching NYC neighborhood boundaries...")
    try:
        payload = _fetch_boundaries()
    except Exception as exc:
        print(f"  Boundaries unavailable ({exc}); the map ships without the layer")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  Cached {len(payload.get('features', ()))} neighborhoods to {path}")
    return True


def _load_boundaries() -> dict[str, Any] | None:
    """Return the cached NTA GeoJSON, or None when there is none to read."""
    path = config.NTA_CACHE_PATH
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except Exception as exc:
        print(f"  Neighborhood boundary cache unreadable ({exc}); ignoring it")
        return None


class Areas:
    """NYC neighborhood polygons, indexed for point lookup.

    Built once per export and used twice: to place every graph edge for the
    coverage denominator, and to place every drawn corridor so the page can
    count passes per area.
    """

    def __init__(self, features: list[dict[str, Any]]) -> None:
        """Index the polygons of an NTA GeoJSON feature list."""
        from shapely.geometry import shape  # noqa: PLC0415 -- optional at import time
        from shapely.strtree import STRtree  # noqa: PLC0415

        self.names = [f["properties"]["ntaname"] for f in features]
        self.boros = [
            BOROUGH_ABBR.get(f["properties"]["boroname"], f["properties"]["boroname"])
            for f in features
        ]
        self.shapes = [shape(f["geometry"]) for f in features]
        self._tree = STRtree(self.shapes)

    def __len__(self) -> int:
        """Count the areas."""
        return len(self.names)

    def locate(self, points: Sequence[tuple[float, float]]) -> list[int]:
        """Place (lon, lat) points in areas; -1 for a point outside them all.

        A point inside a polygon takes it; one that is not takes the nearest
        polygon within BOUNDARY_TOLERANCE_M, which is what keeps a pier or a
        bridge deck in the neighborhood it belongs to.  Ties go to the lower
        index, so the answer never depends on tree order.
        """
        import numpy as np  # noqa: PLC0415 -- keeps the import next to its use
        from shapely import points as shapely_points  # noqa: PLC0415

        if not points:
            return []
        geoms = shapely_points(np.asarray(points, dtype=float))
        out = np.full(len(points), -1, dtype=np.int64)
        # Reverse order so the lowest tree index wins a boundary tie: query()
        # returns pairs sorted by input then tree index.
        hits = self._tree.query(geoms, predicate="covered_by")
        out[hits[0][::-1]] = hits[1][::-1]
        stray = np.flatnonzero(out < 0)
        if len(stray):
            # Degrees of latitude: a degree of longitude is shorter here, so
            # the tolerance is ~55 m north-south and ~41 m east-west. The
            # slack is for survey mismatch, not a measurement of its own.
            near = self._tree.query_nearest(
                geoms[stray],
                max_distance=BOUNDARY_TOLERANCE_M / config.M_PER_LAT,
                all_matches=False,
            )
            out[stray[near[0]]] = near[1]
        return [int(i) for i in out]

    def rings(self, index: int) -> list[list[list[list[float]]]]:
        """Simplified outline of one area, as Leaflet takes a multi-polygon.

        Nested one level deeper than a flat ring list on purpose: 34 of the
        areas are several disjoint pieces (a neighborhood plus its islands),
        and Leaflet reads a second ring at the top level as a *hole* in the
        first.  Flattened, Rockaway would punch a hole through itself.
        """
        geom = self.shapes[index].simplify(SIMPLIFY_DEG, preserve_topology=True)
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        return [
            [
                [[round(x, COORD_PRECISION), round(y, COORD_PRECISION)] for x, y in ring.coords]
                for ring in (poly.exterior, *poly.interiors)
            ]
            for poly in polys
        ]


def load_areas() -> Areas | None:
    """Load the neighborhood polygons, or None if they cannot be had."""
    payload = _load_boundaries()
    if not payload or not payload.get("features"):
        return None
    try:
        return Areas(payload["features"])
    except Exception as exc:
        print(f"  Neighborhood boundaries unusable ({exc}); skipping the layer")
        return None


def _midpoint(coords: Sequence[tuple[float, float]]) -> tuple[float, float]:
    """Return the vertex a geometry is placed by: its middle coordinate."""
    return tuple(coords[len(coords) // 2])  # type: ignore[return-value]


def _edge_seconds(state: dict[str, Any]) -> dict[tuple[int, int], float]:
    """Elapsed seconds measured on each edge, both directions together.

    From ``edge_speed``, which backfills the ride CSVs' timestamps onto the
    geometry -- so this is measured time on that street, not an estimate from
    distance.  It is a floor: the detector attributes ~370 of the ~520
    recorded hours to an edge, and the rest is time off the network, inside a
    recording gap, or on a pass too short to admit.  It carries no per-ride
    breakdown, so nothing here can follow the date slider.
    """
    out: dict[tuple[int, int], float] = {}
    for key, rec in state.get("edge_speed", {}).items():
        chunks = rec.get("c") if isinstance(rec, dict) else None
        if not chunks:
            continue
        out[key] = sum(c[_TIME_FWD] + c[_TIME_REV] for c in chunks)
    return out


def measure(
    areas: Areas | None,
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_hw: dict[tuple[int, int], str],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Per-area rideable network, ridden network, time on it, and when it was first ridden.

    Numerator and denominator are the ones ``_coverage_summary`` uses -- graph
    edges whose highway tag is rideable, not the merged corridors the map
    draws -- so an area's percentage is the same kind of number as the
    citywide one in the hero tile.  ``first`` maps an ISO date to the metres
    of network first ridden on it, which is what lets the page's slider move
    the layer.

    ``time_s`` is the exception to the rideable-only rule: time on a park path
    or a service road is still time spent in the neighborhood, so every edge
    with a measured record counts, whatever its highway tag.

    One row per area, in the boundary file's own order and including the
    areas the graph has no rideable street in; callers drop those.  Shared by
    the export and ``tools/neighborhood_audit.py``.
    """
    if areas is None or not edge_hw:
        return []
    edge_rides: dict[tuple[int, int], list[str]] = state.get("edge_rides", {})
    seconds = _edge_seconds(state)
    keys = [k for k in edge_geom if edge_hw.get(k, "") not in config.COVERAGE_EXCLUDE]
    rideable = set(keys)
    # Timed edges the rideable filter drops -- a park path, a service road --
    # still have to be placed, or their time would silently land nowhere.
    timed_only = [k for k in seconds if k not in rideable and k in edge_geom]
    placed = areas.locate([_midpoint(edge_geom[k]) for k in [*keys, *timed_only]])

    rows: list[dict[str, Any]] = [
        {
            "name": n,
            "boro": b,
            "net_m": 0.0,
            "ridden_m": 0.0,
            "time_s": 0.0,
            "rides": set(),
            "first": {},
        }
        for n, b in zip(areas.names, areas.boros)
    ]
    for key, area in zip(keys, placed):
        if area < 0:
            continue
        row = rows[area]
        row["time_s"] += seconds.get(key, 0.0)
        length = _geom_len_m(edge_geom[key])
        row["net_m"] += length
        rides = edge_rides.get(key)
        if not rides:
            continue
        row["ridden_m"] += length
        row["rides"].update(rides)
        day = min(rides)[:10]
        row["first"][day] = row["first"].get(day, 0.0) + length
    for key, area in zip(timed_only, placed[len(keys) :]):
        if area >= 0:
            rows[area]["time_s"] += seconds[key]
    return rows


def _neighborhood_summary(
    areas: Areas | None,
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_hw: dict[tuple[int, int], str],
    state: dict[str, Any],
    date_index: dict[str, int],
    features: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build the export block, and tag each drawn feature with its area.

    The two go together because the tag is an index into the block's own
    ``areas`` array, which drops the areas holding no rideable street -- a
    feature tagged against the boundary file's numbering would point at the
    wrong neighborhood.  A feature outside every area (the graph reaches
    well past the city) is tagged -1 and counts towards nothing.

    Areas ship even at 0% covered: where the bike has never been is the other
    half of what this layer shows.
    """
    if areas is None:
        return None
    rows = measure(areas, edge_geom, edge_hw, state)
    if not rows:
        return None

    keep = [i for i, row in enumerate(rows) if row["net_m"] > 0]
    # Busiest first, so the page can rank without re-sorting.
    keep.sort(key=lambda i: (-rows[i]["ridden_m"], rows[i]["name"]))
    if not keep:
        return None
    renumber = {old: new for new, old in enumerate(keep)}
    for feature, area in zip(features, area_of_features(areas, features)):
        feature["properties"]["n"] = renumber.get(area, -1)

    out = []
    for old in keep:
        row = rows[old]
        first = sorted((date_index[d], m) for d, m in row["first"].items() if d in date_index)
        out.append(
            {
                "name": row["name"],
                "boro": row["boro"],
                "net_m": round(row["net_m"]),
                "ridden_m": round(row["ridden_m"]),
                # Measured on-street seconds, all-time: edge_speed has no
                # per-ride breakdown, so unlike `new` this cannot follow the
                # slider, and the panel that shows it says all-time.
                "time_s": round(row["time_s"]),
                # [date index, metres first ridden that day], chronological:
                # the page takes a running total up to the date it is showing.
                "new": [[d, round(m)] for d, m in first],
                "rings": areas.rings(old),
            }
        )
    return {
        "source": NTA_VINTAGE,
        # The same measurement as `coverage`, over the part of the graph that
        # is actually in New York City. Not a share of the whole city: the
        # graph is only as wide as the rides made it, which is why Staten
        # Island contributes 25 km of network and no rides at all.
        "net_m": round(sum(a["net_m"] for a in out)),
        "ridden_m": round(sum(a["ridden_m"] for a in out)),
        "time_s": round(sum(a["time_s"] for a in out)),
        "areas": out,
    }


def area_of_features(areas: Areas | None, features: list[dict[str, Any]]) -> list[int]:
    """Place each drawn feature in an area, by the same midpoint rule."""
    if areas is None:
        return []
    return areas.locate([_midpoint(f["geometry"]["coordinates"]) for f in features])
