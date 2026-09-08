"""Shared fixtures: synthetic street graphs, geometry and speed-record helpers."""

from __future__ import annotations

import math

import networkx as nx
import pytest

# Reference point for synthetic geometry: mid-NYC, matching the pipeline's
# fixed-latitude meter conversions.
LAT0 = 40.73
LON0 = -73.99
M_PER_LAT = 110_540
M_PER_LON = 111_320 * math.cos(math.radians(LAT0))


def lonlat(x_m: float, y_m: float) -> tuple[float, float]:
    """Convert local meter offsets from (LON0, LAT0) to (lon, lat)."""
    return LON0 + x_m / M_PER_LON, LAT0 + y_m / M_PER_LAT


def add_street(G: nx.MultiDiGraph, u: int, v: int, highway: str = "residential") -> None:
    """Add a two-way street between existing nodes u and v."""
    dx = (G.nodes[u]["x"] - G.nodes[v]["x"]) * M_PER_LON
    dy = (G.nodes[u]["y"] - G.nodes[v]["y"]) * M_PER_LAT
    length = math.hypot(dx, dy)
    G.add_edge(u, v, length=length, highway=highway)
    G.add_edge(v, u, length=length, highway=highway)


@pytest.fixture
def grid_graph() -> nx.MultiDiGraph:
    """5x5 street grid with 100m blocks; node id = row*10 + col."""
    G = nx.MultiDiGraph()
    for r in range(5):
        for c in range(5):
            lon, lat = lonlat(c * 100.0, r * 100.0)
            G.add_node(r * 10 + c, x=lon, y=lat)
    for r in range(5):
        for c in range(5):
            if c < 4:
                add_street(G, r * 10 + c, r * 10 + c + 1)
            if r < 4:
                add_street(G, r * 10 + c, (r + 1) * 10 + c)
    return G


def chunk(
    fwd: tuple[float, float, float, float] = (0, 0, 0, 0),
    rev: tuple[float, float, float, float] = (0, 0, 0, 0),
    *,
    fwd_speeds: list[float] | None = None,
    rev_speeds: list[float] | None = None,
) -> list[float]:
    """Build one edge_speed chunk record from (dist, time, moving, n) per direction.

    Written here rather than as a literal in each test because the record has
    grown once and will again: a test that spells out its slots is testing the
    layout it was written against.  Pass speeds default to n crossings all at
    the bucket's own average -- a rider who was the same every time -- so a
    test only says what it means to vary.
    """
    out: list[float] = []
    for (dist, time_s, moving, n), given in ((fwd, fwd_speeds), (rev, rev_speeds)):
        speeds = given if given is not None else [3.6 * dist / time_s] * int(n) if time_s else []
        out += [dist, time_s, moving, n, sum(speeds), sum(s * s for s in speeds)]
    return out
