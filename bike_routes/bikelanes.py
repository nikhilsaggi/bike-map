"""NYC DOT bike lanes, as a layer under the rides.

Reshapes NYC DOT's *New York City Bike Routes* dataset into the
``properties.bike_lanes`` block. The dataset keeps a lane's whole history
rather than only its current state: every upgrade retires the old record
(``ret_date``) and starts a new one that names it (``prevbikeid``), so the
network as it stood on any day is the set of records installed by then and not
yet retired. That is what lets the page's slider move this layer the way it
moves the streets -- a lane appears on the day it was built.

Like ``citibike`` and ``subway`` this describes the city, not the riding, so
none of it reaches ``edge_counts``, ``coverage`` or ``features[]``. The one
thing it says about the rides is which lanes they used: each drawn lane carries
the date by which half of it had been ridden *while it existed* (``first``), and
the page draws a lane bright from that date on. A street ridden before its lane
was built does not count as the lane having been ridden.

The file is fetched by ``cli.main`` through :func:`ensure_lanes`, never by the
export, so the export tests stay offline. ``None`` when it is absent.
"""

from __future__ import annotations

import json
import math
import string
import time
import urllib.request
from bisect import bisect_left
from typing import TYPE_CHECKING, Any

import numpy as np
import shapely
from shapely.geometry import LineString, MultiLineString
from shapely.ops import linemerge

from . import config
from .edge_speed import ride_traversals
from .merge import _geom_len_m
from .neighborhoods import _midpoint

if TYPE_CHECKING:
    from pathlib import Path

    from .neighborhoods import Areas

# SoQL rather than the bulk export: the export endpoint was returning 503 while
# this one answered. The limit is far above the ~30k rows the dataset holds,
# and a response that fills it is treated as truncated rather than trusted.
SOURCE_LIMIT = 200_000
SOURCE_URL = f"https://data.cityofnewyork.us/resource/mzxg-pwib.geojson?$limit={SOURCE_LIMIT}"
SOURCE_NAME = "NYC DOT New York City Bike Routes (mzxg-pwib)"

# DOT republishes about once a year, so a cache this old is refetched; a failed
# refetch keeps the old file rather than losing the layer.
MAX_AGE_DAYS = 90

# How a lane is matched to a ridden street: a lane sample point counts as
# ridden by an edge running within JOIN_TOL_M of it and within JOIN_ANGLE_DEG
# of parallel (either direction -- a lane is ridden whichever way the bike
# went). The tolerance has to reach an OSM cycleway drawn beside the street
# DOT draws on the centreline, and the angle test is what keeps a cross street
# from claiming the avenue it meets.
JOIN_TOL_M = 12.0
JOIN_ANGLE_DEG = 25.0
JOIN_STEP_M = 10.0
JOIN_TANGENT_M = 5.0
# A lane is ridden once half its length has been: one corner clipped by a
# turn is not a ride along it.
RIDDEN_SHARE = 0.5

SIMPLIFY_DEG = 0.00001  # ~1 m
COORD_PRECISION = 5

# DOT's facility classes, in the order the page ranks them.
CLASSES = ("I", "II", "III", "L")


def _fetch() -> dict[str, Any]:
    """Download the dataset from NYC Open Data."""
    with urllib.request.urlopen(SOURCE_URL, timeout=300) as resp:
        return json.load(resp)


def ensure_lanes(path: Path | None = None) -> bool:
    """Download the lane cache if it is missing or stale.

    Called once from ``cli.main``. A failed download keeps whatever file is
    already there: last year's network is a better layer than none.
    """
    p = config.BIKE_LANES_CACHE_PATH if path is None else path
    if p.exists() and time.time() - p.stat().st_mtime < MAX_AGE_DAYS * 86_400:
        return True
    print("Fetching NYC DOT bike routes...")
    try:
        payload = _fetch()
        n = len(payload.get("features", ()))
        if n == 0 or n >= SOURCE_LIMIT:
            msg = f"{n} records -- empty or truncated at the request limit"
            raise ValueError(msg)  # noqa: TRY301
    except Exception as exc:
        print(
            f"  Bike routes unavailable ({exc}); keeping {'the old file' if p.exists() else 'no layer'}"
        )
        return p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"  Cached {n:,} bike route records to {p}")
    return True


def _load(path: Path | None = None) -> dict[str, Any] | None:
    p = config.BIKE_LANES_CACHE_PATH if path is None else path
    if not p.exists():
        return None
    try:
        with p.open() as f:
            return json.load(f)
    except Exception as exc:
        print(f"  Bike route cache unreadable ({exc}); ignoring it")
        return None


def _day(value: object) -> str | None:
    """ISO date from a Socrata timestamp, or None."""
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def _bike_id(value: object) -> str | None:
    """Normalise a bike id: the dataset writes it as both 2080 and "2080.0"."""
    if value in (None, ""):
        return None
    try:
        return str(int(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _street(name: object) -> str:
    # capwords, not str.title: title() capitalises after an apostrophe.
    return string.capwords(name) if isinstance(name, str) else ""


def _to_m(lon: np.ndarray, lat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return lon * config.M_PER_LON, lat * config.M_PER_LAT


def _line_m(coords: list[tuple[float, float]] | list[list[float]]) -> LineString:
    a = np.asarray(coords, dtype=float)
    x, y = _to_m(a[:, 0], a[:, 1])
    return LineString(np.column_stack([x, y]))


def _parts(geom: dict[str, Any] | None) -> list[list[list[float]]]:
    if not geom:
        return []
    if geom.get("type") == "LineString":
        lines = [geom["coordinates"]]
    elif geom.get("type") == "MultiLineString":
        lines = geom["coordinates"]
    else:
        return []
    return [ln for ln in lines if len(ln) >= 2]


def _ride_dates(
    state: dict[str, Any], edge_geom: dict[tuple[int, int], Any], date_idx: dict[str, int]
) -> tuple[list[tuple[int, int]], list[np.ndarray]]:
    """Ridden edges with geometry, and the sorted date indices each was ridden on."""
    keys, dates = [], []
    for key, rides in state.get("edge_rides", {}).items():
        if key not in edge_geom or len(edge_geom[key]) < 2:
            continue
        days = sorted({date_idx[r[:10]] for r in rides if r[:10] in date_idx})
        if days:
            keys.append(key)
            dates.append(np.asarray(days, dtype=np.int64))
    return keys, dates


def _sample(
    lines: list[LineString],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    """Points every JOIN_STEP_M along each line, their tangents, and whose each is."""
    owner, pts, ahead, behind = [], [], [], []
    counts = [0] * len(lines)
    for li, line in enumerate(lines):
        n = max(2, math.ceil(line.length / JOIN_STEP_M) + 1)
        d = np.linspace(0.0, line.length, n)
        pts.append(shapely.line_interpolate_point(line, d))
        ahead.append(shapely.line_interpolate_point(line, d + JOIN_TANGENT_M))
        behind.append(shapely.line_interpolate_point(line, d - JOIN_TANGENT_M))
        owner.append(np.full(n, li))
        counts[li] = n
    tangents = shapely.get_coordinates(np.concatenate(ahead)) - shapely.get_coordinates(
        np.concatenate(behind)
    )
    return np.concatenate(pts), tangents, np.concatenate(owner), counts


def _parallel_pairs(
    pts: np.ndarray, tangents: np.ndarray, targets: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """(point, target) pairs within JOIN_TOL_M and JOIN_ANGLE_DEG of parallel.

    Either direction counts: a lane is ridden whichever way the bike went.
    """
    pi, ti = shapely.STRtree(targets).query(pts, predicate="dwithin", distance=JOIN_TOL_M)
    if not len(pi):
        return pi, ti
    along = shapely.line_locate_point(targets[ti], pts[pi])
    target_t = shapely.get_coordinates(
        shapely.line_interpolate_point(targets[ti], along + JOIN_TANGENT_M)
    ) - shapely.get_coordinates(shapely.line_interpolate_point(targets[ti], along - JOIN_TANGENT_M))
    dot = np.abs(np.einsum("ij,ij->i", tangents[pi], target_t))
    norm = np.linalg.norm(tangents[pi], axis=1) * np.linalg.norm(target_t, axis=1)
    keep = norm > 0
    keep[keep] = dot[keep] / norm[keep] >= math.cos(math.radians(JOIN_ANGLE_DEG))
    return pi[keep], ti[keep]


def _first_ridden(
    rows: list[dict[str, Any]],
    edge_geom: dict[tuple[int, int], Any],
    state: dict[str, Any],
    all_dates: list[str],
) -> list[int]:
    """Per lane record, the date index by which half of it had been ridden, or -1.

    Only rides inside the record's own lifetime count, so a street ridden
    before its lane was painted reads unridden until the lane itself is.
    """
    date_idx = {d: i for i, d in enumerate(all_dates)}
    keys, edge_days = _ride_dates(state, edge_geom, date_idx)
    out = [-1] * len(rows)
    if not keys or not rows:
        return out
    edges = np.array([_line_m(edge_geom[k]) for k in keys], dtype=object)

    parts = [(ri, part) for ri, row in enumerate(rows) for part in row["parts_m"]]
    pts, tangents, part_of, part_n = _sample([part for _ri, part in parts])
    owner = np.array([ri for ri, _part in parts])[part_of]
    per_row = [0] * len(rows)
    for (ri, _part), n in zip(parts, part_n, strict=True):
        per_row[ri] += n
    pi, ei = _parallel_pairs(pts, tangents, edges)

    # Earliest ride on a matching edge inside the record's lifetime, per point.
    # Points are grouped by record because _sample walks the parts in order.
    point_first = np.full(len(pts), np.iinfo(np.int64).max, dtype=np.int64)
    lo = [bisect_left(all_dates, r["inst"]) for r in rows]
    hi = [bisect_left(all_dates, r["ret"]) if r["ret"] else len(all_dates) for r in rows]
    for p, e in zip(pi.tolist(), ei.tolist(), strict=True):
        r = owner[p]
        days = edge_days[e]
        j = int(np.searchsorted(days, lo[r]))
        if j < len(days) and days[j] < hi[r] and days[j] < point_first[p]:
            point_first[p] = days[j]

    start = 0
    for ri, n in enumerate(per_row):
        firsts = np.sort(point_first[start : start + n])
        start += n
        k = max(0, math.ceil(n * RIDDEN_SHARE) - 1)
        if n and firsts[k] != np.iinfo(np.int64).max:
            out[ri] = int(firsts[k])
    return out


def _edge_cover(
    rows: list[dict[str, Any]],
    keys: list[tuple[int, int]],
    edge_geom: dict[tuple[int, int], Any],
) -> dict[tuple[int, int], list[tuple[int, float]]]:
    """Per ridden edge, the lane records running along it and the share each covers.

    The other direction from _first_ridden, and it has to be: one long edge
    -- a bridge path -- is covered by several short DOT records, none of which
    reaches half of it, so the shares are summed over whatever records are
    standing on the date in question rather than tested one at a time.
    """
    parts = [(ri, part) for ri, row in enumerate(rows) for part in row["parts_m"]]
    if not keys or not parts:
        return {}
    lanes = np.array([part for _ri, part in parts], dtype=object)
    lane_row = np.array([ri for ri, _part in parts])
    pts, tangents, edge_of, counts = _sample([_line_m(edge_geom[k]) for k in keys])
    pi, li = _parallel_pairs(pts, tangents, lanes)
    hits: dict[tuple[int, int], set[int]] = {}
    for p, part in zip(pi.tolist(), li.tolist(), strict=True):
        hits.setdefault((int(edge_of[p]), int(lane_row[part])), set()).add(p)
    out: dict[tuple[int, int], list[tuple[int, float]]] = {}
    for (e, ri), points in hits.items():
        out.setdefault(keys[e], []).append((ri, len(points) / counts[e]))
    return out


def _riding_share(
    rows: list[dict[str, Any]],
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    state: dict[str, Any],
    areas: Areas | None,
) -> dict[str, list[int]] | None:
    """Metres of NYC riding per year: ``{year: [all, on a lane, on a protected lane]}``.

    A pass is on a lane when the records standing on its ride's date cover
    half of the edge, and on a protected one when class I records alone do.
    Metres are edge length times passes -- an estimate that overstates a total
    (a pass that clipped an edge is charged the whole edge) but mostly cancels
    in a share, which is all the page prints. Only edges inside a NYC
    neighborhood count on either side, because DOT maps nothing outside the
    city; without the boundaries there is no share at all.
    """
    if areas is None:
        return None
    keys = [k for k in state.get("edge_rides", {}) if len(edge_geom.get(k) or ()) >= 2]
    places = areas.locate([_midpoint(edge_geom[k]) for k in keys])
    keys = [k for k, a in zip(keys, places, strict=True) if a >= 0]
    cover = _edge_cover(rows, keys, edge_geom)
    out: dict[str, list[float]] = {}
    for key in keys:
        length = _geom_len_m(edge_geom[key])
        standing = cover.get(key, ())
        for ride in set(state["edge_rides"][key]):
            day = ride[:10]
            lane = protected = 0.0
            for ri, share in standing:
                r = rows[ri]
                if r["inst"] <= day and (r["ret"] is None or r["ret"] > day):
                    lane += share
                    if r["cls"] == "I":
                        protected += share
            m = length * ride_traversals(state, key, ride)
            year = out.setdefault(ride[:4], [0.0, 0.0, 0.0])
            year[0] += m
            if lane >= RIDDEN_SHARE:
                year[1] += m
            if protected >= RIDDEN_SHARE:
                year[2] += m
    return {y: [round(v) for v in out[y]] for y in sorted(out)} or None


def _rounded(line: LineString) -> list[list[float]]:
    coords = line.simplify(SIMPLIFY_DEG).coords
    return [[round(x, COORD_PRECISION), round(y, COORD_PRECISION)] for x, y in coords]


def _bike_lane_summary(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    state: dict[str, Any],
    all_dates: list[str],
    path: Path | None = None,
    areas: Areas | None = None,
) -> dict[str, Any] | None:
    """Build the ``properties.bike_lanes`` block, or None without a cache.

    ``versions`` is one row per lane record -- ``[class, facility one way,
    facility the other, installed, retired, street, previous]``, facilities and
    streets as indices into their tables, ``previous`` the index of the record
    it superseded or -1. ``lines`` is ``[version, first, coords]``: a record's
    geometry, split where its parts were first ridden on different dates.
    ``by_year`` is the riding share (see _riding_share), None without the
    neighborhood boundaries.

    Only records alive at some point between the first and last ride are
    drawn; the records they superseded are kept, undrawn, so a lane's panel
    can say what was there before.
    """
    payload = _load(path)
    if not payload or not all_dates:
        return None
    first_day, last_day = all_dates[0], all_dates[-1]

    rows: list[dict[str, Any]] = []
    for f in payload.get("features", ()):
        p = f.get("properties") or {}
        inst = _day(p.get("instdate"))
        parts = _parts(f.get("geometry"))
        if not inst or not parts:
            continue
        rows.append(
            {
                "id": _bike_id(p.get("bikeid")),
                "prev": _bike_id(p.get("prevbikeid")),
                "cls": p.get("facilitycl") if p.get("facilitycl") in CLASSES else "L",
                "ft": p.get("ft_facilit") or "",
                "tf": p.get("tf_facilit") or "",
                "inst": inst,
                "ret": _day(p.get("ret_date")),
                "street": _street(p.get("street")),
                "parts": parts,
            }
        )
    if not rows:
        return None

    def alive(r: dict[str, Any]) -> bool:
        return r["inst"] <= last_day and (r["ret"] is None or r["ret"] > first_day)

    # A version is a bike id's attributes; its segments are the rows sharing
    # them. The id is not quite unique across the dataset, so the attributes
    # are part of the key.
    def vkey(r: dict[str, Any]) -> tuple[Any, ...]:
        return (r["id"], r["cls"], r["ft"], r["tf"], r["inst"], r["ret"], r["street"])

    drawn = [r for r in rows if alive(r)]
    for r in drawn:
        r["parts_m"] = [_line_m(part) for part in r["parts"]]
    firsts = _first_ridden(drawn, edge_geom, state, all_dates)

    # Versions: the drawn ones, then every record they descend from.
    versions: dict[tuple[Any, ...], int] = {}
    vrows: list[dict[str, Any]] = []
    for r in drawn:
        if vkey(r) not in versions:
            versions[vkey(r)] = len(vrows)
            vrows.append(r)
    id_row = {r["id"]: r for r in rows if r["id"] is not None}
    i = 0
    while i < len(vrows):
        prev = id_row.get(vrows[i]["prev"]) if vrows[i]["prev"] else None
        if prev is not None and vkey(prev) not in versions:
            versions[vkey(prev)] = len(vrows)
            vrows.append(prev)
        i += 1

    facilities: list[str] = []
    fslot: dict[str, int] = {}
    streets: list[str] = []
    sslot: dict[str, int] = {}

    def slot(table: list[str], index: dict[str, int], value: str) -> int:
        if value not in index:
            index[value] = len(table)
            table.append(value)
        return index[value]

    out_versions = []
    for r in vrows:
        prev = id_row.get(r["prev"]) if r["prev"] else None
        out_versions.append(
            [
                r["cls"],
                slot(facilities, fslot, r["ft"]),
                slot(facilities, fslot, r["tf"]),
                r["inst"],
                r["ret"],
                slot(streets, sslot, r["street"]),
                versions.get(vkey(prev), -1) if prev is not None else -1,
            ]
        )

    # One drawn line per run of a version's segments that were first ridden on
    # the same day: joining them is most of the payload saving, and splitting
    # on `first` is what lets half a corridor light up before the rest.
    groups: dict[tuple[int, int], list[LineString]] = {}
    for r, first in zip(drawn, firsts, strict=True):
        groups.setdefault((versions[vkey(r)], first), []).extend(
            LineString(part) for part in r["parts"]
        )
    lines = []
    for (vi, first), parts in sorted(groups.items()):
        merged = linemerge(MultiLineString(parts)) if len(parts) > 1 else parts[0]
        for line in getattr(merged, "geoms", [merged]):
            coords = _rounded(line)
            if len(coords) >= 2:
                lines.append([vi, first, coords])

    return {
        "source": SOURCE_NAME,
        "facilities": facilities,
        "streets": streets,
        "versions": out_versions,
        "lines": lines,
        "by_year": _riding_share(drawn, edge_geom, state, areas),
    }
