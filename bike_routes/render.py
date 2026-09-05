"""Render cache (geometry + highway tags) and static PNG rendering."""

from __future__ import annotations

import pickle
from typing import TYPE_CHECKING, Any

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.cm import ScalarMappable
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D

from . import config

if TYPE_CHECKING:
    from collections.abc import Callable

    import networkx as nx

# (edge_geom, edge_hw, edge_name), each keyed by canonical node pair.
RenderData = tuple[
    dict[tuple[int, int], list[tuple[float, float]]],
    dict[tuple[int, int], str],
    dict[tuple[int, int], str],
]

# (west, south, east, north) in lon/lat: a crop of the drawn map.
Bbox = tuple[float, float, float, float]


def _classify_hw(hw: str | list[str]) -> str:
    """Map an OSM highway tag value to an infrastructure class."""
    if isinstance(hw, list):
        classes = [config.INFRA_CLASS.get(h, config.INFRA_DEFAULT) for h in hw]
        return "bike" if "bike" in classes else config.INFRA_DEFAULT
    return config.INFRA_CLASS.get(hw, config.INFRA_DEFAULT)


def _primary_hw_tag(hw: str | list[str]) -> str:
    """Pick one highway tag for an edge, preferring a bike-classified tag."""
    if isinstance(hw, list):
        for h in hw:
            if config.INFRA_CLASS.get(h) == "bike":
                return h
        return hw[0] if hw else ""
    return hw or ""


def _primary_name(name: str | list[str] | None) -> str:
    """Pick one street name for an edge; OSM lists several on shared ways."""
    if isinstance(name, list):
        return name[0] if name else ""
    return name or ""


def _build_render_cache(
    G: nx.MultiDiGraph,
) -> RenderData:
    """Extract one canonical geometry, highway tag, and name per node pair.

    When multiple parallel edges exist between the same nodes (from
    composing bike/drive/walk networks), keeps only the shortest edge's
    geometry to avoid overlapping rendered lines.  The name is kept for the
    speed-asymmetry ranking (export.py), which reports corridors by street
    name; it is the only consumer, so unnamed edges simply stay absent.
    """
    print("Building render cache...")
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]] = {}
    edge_hw: dict[tuple[int, int], str] = {}
    edge_name: dict[tuple[int, int], str] = {}
    edge_len: dict[tuple[int, int], float] = {}
    for u, v, _key, data in G.edges(data=True, keys=True):
        canon = (min(u, v), max(u, v))
        length = data.get("length", float("inf"))
        tag = _primary_hw_tag(data.get("highway", ""))
        if (nm := _primary_name(data.get("name"))) and canon not in edge_name:
            edge_name[canon] = nm
        if canon in edge_geom:
            if _classify_hw(tag) == "bike" and _classify_hw(edge_hw[canon]) != "bike":
                edge_hw[canon] = tag
            if edge_len[canon] <= length:
                continue
        if "geometry" in data:
            xs, ys = data["geometry"].xy
            edge_geom[canon] = list(zip(xs, ys))
        else:
            edge_geom[canon] = [
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"]),
            ]
        edge_len[canon] = length
        if canon not in edge_hw:
            edge_hw[canon] = tag

    config.RENDER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.RENDER_CACHE_PATH.open("wb") as f:
        pickle.dump(
            (config.RENDER_CACHE_FORMAT, edge_geom, edge_hw, edge_name),
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )
    print(f"  Cached {len(edge_geom):,} edge geometries, {len(edge_name):,} named")
    return edge_geom, edge_hw, edge_name


def _get_render_data(
    G: nx.MultiDiGraph | None = None,
) -> RenderData | None:
    """Load render cache, or build it from graph if missing.

    A cache in any older format is treated as missing so it rebuilds with
    the data newer features need, rather than silently exporting without it.
    """
    if config.RENDER_CACHE_PATH.exists():
        with config.RENDER_CACHE_PATH.open("rb") as f:
            cached = pickle.load(f)
        if (
            isinstance(cached, tuple)
            and len(cached) == 4
            and cached[0] == config.RENDER_CACHE_FORMAT
        ):
            _fmt, edge_geom, edge_hw, edge_name = cached
            print(f"Loaded render cache ({len(edge_geom):,} edges)")
            return edge_geom, edge_hw, edge_name
        print("Render cache in legacy format -- rebuilding")
    if G is None:
        return None
    return _build_render_cache(G)


def _make_fig(
    skeleton_lines: list[list[tuple[float, float]]],
    bbox: Bbox | None = None,
) -> tuple[plt.Figure, plt.Axes]:
    """Create figure with OSM skeleton background, framed to bbox if given."""
    fig, ax = plt.subplots(figsize=config.FIG_SIZE, facecolor="#0d0d0d")
    ax.set_facecolor("#0d0d0d")
    ax.set_aspect("equal")
    ax.axis("off")
    lc = LineCollection(skeleton_lines, colors="#2a2a2a", linewidths=0.3, zorder=1)
    ax.add_collection(lc)
    if bbox is None:
        ax.autoscale_view()
    else:
        west, south, east, north = bbox
        ax.set_xlim(west, east)
        ax.set_ylim(south, north)
    return fig, ax


def _save_fig(fig: plt.Figure, path: str, *, chrome: bool = True, dpi: int = 180) -> None:
    """Save figure to disk and close it.

    The colorbar sits below the axes in figure coordinates, so the space for
    it is only worth reserving when there is one.
    """
    if chrome:
        fig.subplots_adjust(bottom=0.10)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"  Saved -> {path}")
    plt.close(fig)


def _render_coverage(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_counts: dict[tuple[int, int], int],
    path: str,
) -> None:
    """Draw every ridden edge in one colour over the street skeleton."""
    print("Rendering coverage map...")
    cmap = plt.colormaps[config.COLORMAP]
    fig, ax = _make_fig(list(edge_geom.values()))

    lines = [edge_geom[k] for k in edge_counts if k in edge_geom]
    lc = LineCollection(
        lines, colors=[cmap(1.0)], linewidths=config.LINE_WIDTH_MIN * 2, alpha=0.85, zorder=2
    )
    ax.add_collection(lc)
    _save_fig(fig, path)


def _render_frequency(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    edge_counts: dict[tuple[int, int], int],
    path: str,
    *,
    bbox: Bbox | None = None,
    chrome: bool = True,
    dpi: int = 180,
) -> None:
    """Draw ridden edges coloured by pass count.

    bbox crops the view (the whole graph when None); the colour scale stays
    anchored to the global maximum either way, so a crop means the same
    thing as the full map.  chrome=False drops the colorbar and legend, for
    a figure whose caption carries the scale instead (the README's image),
    and dpi trades that figure's detail against the bytes a page must load.
    """
    print("Rendering frequency map...")
    cmap = plt.colormaps[config.COLORMAP]
    counts_arr = np.array(list(edge_counts.values()))
    max_count = counts_arr.max()
    print(f"  Max edge count: {max_count}")

    def scale(c: float) -> np.floating:
        return np.sqrt(c) / np.sqrt(max_count)

    fig, ax = _make_fig(list(edge_geom.values()), bbox)

    # Pass 1: faint underlay
    lines_u, colors_u = [], []
    for edge_key, count in edge_counts.items():
        if edge_key not in edge_geom:
            continue
        lines_u.append(edge_geom[edge_key])
        rgba = cmap(scale(count))
        colors_u.append((rgba[0], rgba[1], rgba[2], 0.25))
    ax.add_collection(
        LineCollection(lines_u, colors=colors_u, linewidths=config.LINE_WIDTH_MIN, zorder=2)
    )

    # Pass 2: overlay, rare -> frequent
    sorted_items = sorted(
        ((k, v) for k, v in edge_counts.items() if k in edge_geom),
        key=lambda x: x[1],
    )
    lines_o, colors_o = [], []
    for edge_key, count in sorted_items:
        s = scale(count)
        lines_o.append(edge_geom[edge_key])
        rgba = cmap(s)
        colors_o.append((rgba[0], rgba[1], rgba[2], 0.9))
    ax.add_collection(
        LineCollection(lines_o, colors=colors_o, linewidths=config.LINE_WIDTH_MIN * 2, zorder=3)
    )

    if chrome:
        _add_scale_chrome(fig, ax, cmap, scale, max_count)
    _save_fig(fig, path, chrome=chrome, dpi=dpi)


def _add_scale_chrome(
    fig: plt.Figure,
    ax: plt.Axes,
    cmap: mcolors.Colormap,
    scale: Callable[[float], np.floating],
    max_count: int,
) -> None:
    """Add the colorbar and the sampled-colour legend for the pass scale."""
    sm = ScalarMappable(cmap=cmap, norm=mcolors.PowerNorm(gamma=0.5, vmin=0, vmax=max_count))
    sm.set_array([])
    cbar_ax = fig.add_axes([0.15, 0.06, 0.70, 0.015])
    cbar = fig.colorbar(sm, cax=cbar_ax, orientation="horizontal")
    cbar.set_label("Number of passes", color="white", fontsize=11, labelpad=8)
    cbar.ax.xaxis.set_tick_params(color="white")
    plt.setp(cbar.ax.xaxis.get_ticklabels(), color="white", fontsize=9)
    cbar.outline.set_edgecolor("white")

    legend_elements = [
        Line2D([0], [0], color=cmap(scale(1)), linewidth=2, label="1 pass"),
        Line2D(
            [0],
            [0],
            color=cmap(scale(max(max_count // 4, 1))),
            linewidth=2,
            label=f"~{max_count // 4} passes",
        ),
        Line2D(
            [0],
            [0],
            color=cmap(scale(max_count // 2)),
            linewidth=2,
            label=f"~{max_count // 2} passes",
        ),
        Line2D([0], [0], color=cmap(1.0), linewidth=2, label=f"{max_count} passes"),
    ]
    legend = ax.legend(
        handles=legend_elements,
        loc="lower right",
        framealpha=0.15,
        facecolor="#0d0d0d",
        edgecolor="#555",
        labelcolor="white",
        fontsize=9,
        title="Pass frequency",
        title_fontsize=9,
    )
    legend.get_title().set_color("white")


def _render(
    edge_geom: dict[tuple[int, int], list[tuple[float, float]]],
    state: dict[str, Any],
    *,
    counts: dict[tuple[int, int], int] | None = None,
    skip_png: bool = config.SKIP_PNG_RENDER,
) -> None:
    """Render both coverage and frequency maps.

    counts weights the frequency map; the caller passes traversals (see
    edge_speed.traversal_counts), falling back to state's per-ride counts.
    The coverage map only asks which edges appear, so both agree there.
    """
    if skip_png:
        print("Skipping PNG render")
        return

    edge_counts = state["edge_counts"] if counts is None else counts

    if not edge_counts:
        print("No edges to render")
        return

    _render_coverage(edge_geom, edge_counts, config.OUTPUT_PATH_UNWEIGHTED)
    _render_frequency(edge_geom, edge_counts, config.OUTPUT_PATH_WEIGHTED)
