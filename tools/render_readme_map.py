"""Render the README's map image: the ridden core, cropped, no chrome.

The pipeline's own frequency PNG frames the whole graph.  That is the right
frame for the file it writes -- it should show everything that was ridden --
but the rides now reach eastern Long Island and Westchester, so most of it is
empty black with the part a reader recognises squeezed into a corner.  This
crops to that part: Manhattan up to the top of Central Park, downtown
Brooklyn, and east to Williamsburg.  Same colours, same scale (anchored to the
global maximum, so a stretch means here what it means on the full map),
without the colorbar and legend -- the README's text carries the scale, and a
key inside the image only competes with it at that size.

Reads cache/state.pkl and cache/render_cache.pkl; writes one PNG and nothing
else.  Run it after the pipeline, whenever the README's image should catch up
with the rides.

Usage:
    python tools/render_readme_map.py   # from the repo root
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

from bike_routes import config, render
from bike_routes.edge_speed import traversal_counts
from bike_routes.render import _get_render_data, _render_frequency

# This script lives in tools/, so anchor the output to the repo root rather
# than the working directory it happens to be launched from.
ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "sample_output/pass_frequency.png"

# (west, south, east, north).  Central Park's north end is 40.80; downtown
# Brooklyn 40.69; East Williamsburg runs out to -73.92.
BBOX: render.Bbox = (-74.022, 40.665, -73.905, 40.81)

# The pipeline's own PNGs are archival, so they render at 180; this one is
# loaded by everyone who opens the README, and at 120 it is a third of the
# bytes with the streets still separate.
DPI = 120


def main() -> None:
    """Render the cropped frequency map from the pipeline's caches."""
    if not config.STATE_CACHE_PATH.exists():
        sys.exit(f"{config.STATE_CACHE_PATH} not found -- run the pipeline first")
    with config.STATE_CACHE_PATH.open("rb") as f:
        state = pickle.load(f)
    render_data = _get_render_data()
    if render_data is None:
        sys.exit(f"{config.RENDER_CACHE_PATH} not found -- run the pipeline first")
    edge_geom, _edge_hw, _edge_name = render_data

    counts = traversal_counts(state)
    print(f"{len(state['processed_files']):,} rides, {len(counts):,} edges")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _render_frequency(edge_geom, counts, str(OUT_PATH), bbox=BBOX, chrome=False, dpi=DPI)


if __name__ == "__main__":
    main()
