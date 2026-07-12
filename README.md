# NYC Bike Route Map

Visualizes personal bike ride data on the NYC street network. Processes GPS
ride logs, matches them to OpenStreetMap streets, and renders an interactive
zoomable map and static heatmaps.

**[View the live map](https://nikhilsaggi.github.io/bike-map/)**

![Frequency map](sample_output/bike_routes_frequency.png)

## Interactive Map

The pipeline exports a compressed GeoJSON that powers an interactive
[Leaflet](https://leafletjs.com/) map served via GitHub Pages. Features:

- Coloring by ride frequency
- Hover for ride count, click for the full list of ride dates
- Date-range slider with time-lapse playback (watch the network grow)
- Stats panel with total rides, edges covered, street km, and % of the
  rideable street network explored
- Riding stats: distance/time totals, average speed, longest ride,
  km and new-street km per year, rides-by-hour and weekday histograms

To view locally:

```bash
python bike_routes.py          # generates docs/rides.geojson.gz
python -m http.server 8000 --directory docs
```

## How It Works

1. Loads GPS ride data from CSV files (longitude, latitude, timestamp)
2. Filters to NYC area, splits rides at GPS gaps, resamples to even spacing
3. Fetches and merges OpenStreetMap bike/drive/walk street networks
4. Map-matches GPS traces to street edges using heading-aware spatial snapping
5. Routes between matched nodes, accumulates edge traversal counts
6. Exports GeoJSON (gzipped) for the interactive map + renders static PNGs

All intermediate results are cached. First run takes longer
(OSM download + full processing). Subsequent runs process only new rides.

## Setup

```bash
pip install .
```

Requires Python 3.9+.

## Tests

The map-matching and merge logic is covered by a pytest suite using small
synthetic street grids (no OSM download or ride data needed):

```bash
pip install pytest
pytest
```

Tests also run in CI on every push and pull request.

## Usage

1. Place GPS ride CSVs in the `rides/` folder (or GPX files in `incoming/`)
2. If using GPX files, convert them first:

```bash
python gpx_to_csv.py incoming/ rides/
```

3. Run the pipeline:

```bash
python bike_routes.py
```

Optional flags:

```
--sample N               process only the first N ride files
--rides FILE [FILE ...]  process only these ride CSV filenames
--no-png                 skip rendering the static PNG maps
--workers N              worker processes for map matching (1 = sequential)
```

4. Outputs:
   - `docs/rides.geojson.gz` — interactive map data
   - `bike_routes_coverage.png` and `bike_routes_frequency.png` — static images

### Ride CSV Format

Each CSV file represents one ride with the columns:

```
longitude,latitude,timestamp
-73.98478,40.76030,2023-12-17 18:41:53 -0500
-73.98475,40.76035,2023-12-17 18:41:54 -0500
...
```

- One row per GPS fix, typically 1-second intervals
- Longitude and latitude in decimal degrees (WGS-84)
- Timestamp is used only for ordering (data should already be chronological)

## Configuration

Key parameters at the top of `bike_routes.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `RESAMPLE_SPACING_M` | 20 | Resample GPS points to this spacing (meters) |
| `SNAP_TOLERANCE_M` | 80 | Max distance to snap a GPS point to a street edge |
| `MAX_ROUTING_DISTANCE_M` | 2500 | Max route length between consecutive snapped nodes |
| `MAX_GPS_GAP_M` | 300 | Split ride into segments at gaps larger than this |
| `HEADING_PENALTY` | 0.15 | Penalty per degree of heading mismatch during snap |
| `HW_PENALTY` | (see code) | Per-highway-type snap bias (negative = prefer) |
| `NETWORK_TYPES` | bike, drive, walk | OSM network types to fetch |
| `SAMPLE_SIZE` | None | Limit number of rides processed (for testing) |

## Map-Matching Techniques

Raw GPS traces are noisy — points drift to sidewalks, parallel service roads,
or the wrong side of an intersection. The pipeline uses several techniques to
produce clean route matches:

- **Edge-based snapping** — GPS points snap to the nearest *edge* (perpendicular
  projection), not the nearest node, giving much more accurate placement.
- **Heading-aware snapping** — edges misaligned with the GPS travel direction are
  penalized (`HEADING_PENALTY` m/degree), preventing snaps to perpendicular
  cross-streets.
- **Highway-type preference** — `HW_PENALTY` biases snapping toward cycleways
  and away from footways, service roads, and motorways.
- **Edge densification** — virtual points are added every 150m along long edges
  so GPS traces on bridges and highways can find the correct edge even when its
  endpoints are far away.
- **Distance-gated loop removal** — detects A→...→A loops within a sliding
  window and collapses them, but only when all intermediate nodes stay within
  `LOOP_MAX_DETOUR_M` of the anchor. This cleans up zigzag noise from parallel
  footways without stripping legitimate forward-progress segments.

## Auto-Updating via GitHub Actions

A GitHub Actions workflow (`.github/workflows/update-map.yml`) can
automatically sync new rides from Dropbox and update the map weekly:

1. **Dropbox**: Create an app at [developers.dropbox.com](https://www.dropbox.com/developers)
   and save GPX files to `Apps/bike-rides/`
2. **GitHub Secrets**: Add `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, and
   `DROPBOX_REFRESH_TOKEN` as repository Actions secrets
3. **GitHub Pages**: Enable in repo Settings → Pages → main branch → `/docs`
4. **iOS Shortcut** (optional): Create a Personal Automation that saves the
   workout GPX to `Apps/bike-rides/` in Dropbox when a cycling workout ends

The workflow runs every Monday at 9am UTC and can be triggered manually from
the Actions tab. It syncs GPX files via rclone, converts to CSV, runs the
pipeline, and commits the updated GeoJSON.

## Cache Files

The pipeline creates several cache files (auto-managed, gitignored):

- `osm_graph_cache.pkl` — merged OSM street graph (~260 MB)
- `state.pkl` — processed filenames, edge counts, config snapshot
- `render_cache.pkl` — pre-extracted edge geometries + highway classes
- `route_cache.pkl` — shortest-path results between node pairs
- `cache_versions.json` — osmnx/networkx versions that wrote the graph cache

Delete any cache file to force it to rebuild. Changing processing parameters
automatically triggers a full reprocess, and upgrading osmnx/networkx
automatically refetches the graph (pickled graphs are version-bound).

## License

MIT
