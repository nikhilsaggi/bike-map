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
- Biggest direction splits: the corridors where riding one way is much
  faster than the other ([how it works](findings/direction-split-speed.md))
- Hover for ride count, click for the full list of ride dates
- Date-range slider with time-lapse playback (watch the network grow)
- Collapsible stats panel with total rides, edges covered, and street
  miles
- Riding stats: distance/time totals, average speed, longest ride,
  miles and new-street miles per year, rides-by-hour and weekday
  histograms (data is stored metric; the UI displays miles)

To view locally:

```bash
python -m bike_routes         # generates docs/rides.geojson.gz
python -m http.server 8000 --directory docs
```

## How It Works

1. Loads GPS ride data from CSV files (longitude, latitude, timestamp)
2. Filters to NYC area, splits rides at GPS gaps, resamples to even spacing
3. Fetches and merges OpenStreetMap bike/drive/walk street networks
4. Map-matches GPS traces to street edges using heading-aware spatial snapping
5. Routes between matched nodes, accumulates edge traversal counts
6. Projects each ride's timestamped trace back onto its matched edges to
   recover per-direction speed, and ranks the most asymmetric corridors
7. Exports GeoJSON (gzipped) for the interactive map + renders static PNGs

All intermediate results are cached. First run takes longer
(OSM download + full processing). Subsequent runs process only new rides.

## Repository Layout

```
bike_routes/        the pipeline, one stage per module
  ingest/           Garmin download and GPX -> CSV (the front of the pipeline)
docs/               the published Leaflet map + its rides.geojson.gz
tools/              standalone analysis run by hand, not part of the pipeline
findings/           write-ups of what that analysis found
tests/              pytest suite (synthetic grids) + Playwright e2e for docs/
rides/              ride CSVs (gitignored -- personal GPS traces)
cache/              everything the pipeline generates (gitignored)
sample_output/      the PNGs the README links to
update.py           fetch -> convert -> reprocess -> commit, in one command
```

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

1. Place GPS ride CSVs in the `rides/` folder (or GPX files in `incoming/` —
   `python -m bike_routes.ingest.garmin_sync incoming/` fetches them from
   Garmin Connect)
2. If using GPX files, convert them first:

```bash
python -m bike_routes.ingest.gpx_to_csv incoming/ rides/
```

3. Run the pipeline:

```bash
python -m bike_routes
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
- Timestamps order the trace and drive the direction-split speed layer

## Configuration

Key parameters in `bike_routes/config.py`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MATCHER` | hmm | Map-matcher: `hmm` (Viterbi) or `heuristic` (edge snapping) |
| `HMM_MAX_DIST` | 80 | Max GPS-to-edge distance considered (meters) |
| `HMM_OBS_NOISE` | 15 | Expected GPS noise (meters) |
| `HMM_LATTICE_WIDTH` | 8 | Viterbi beam width (widened to 24 on retry) |
| `RESAMPLE_SPACING_M` | 20 | Resample GPS points to this spacing (meters) |
| `MAX_GPS_GAP_M` | 300 | Split ride into segments at gaps larger than this |
| `NETWORK_TYPES` | bike, drive, walk | OSM network types to fetch |
| `SAMPLE_SIZE` | None | Limit number of rides processed (for testing) |
| `SPEED_VERSION` | 2 | Bump to recompute the speed layer (no rematch) |
| `SPEED_CHUNK_M` | 150 | Long ways are measured in chunks this size |
| `SPEED_SNAP_M` | 25 | Max GPS-to-edge distance for a fix to count as on-edge |
| `SPEED_SPLIT_PASSES` | 3 | Passes per direction before a stretch is ranked |
| `SPEED_CORRIDOR_N` | 10 | Corridors listed in the stats panel |

Heuristic-matcher parameters (`SNAP_TOLERANCE_M`, `HEADING_PENALTY`,
`HW_PENALTY`, ...) remain in `bike_routes/config.py` and apply when
`MATCHER = "heuristic"`.

## Map-Matching

Raw GPS traces are noisy — points drift to sidewalks, parallel service roads,
or the wrong side of an intersection. Traces are matched with a hidden
Markov model matcher
([leuvenmapmatching](https://github.com/wannesm/LeuvenMapMatching)): each
observation gets candidate street edges, transitions are scored by route
plausibility, and the most likely path through the street network is decoded
jointly. Compared to per-point snapping this eliminates parallel-way
oscillation and block-sized routing detours — matched path length is ~1.1x
the GPS track length vs ~2x with the previous heuristic. Stretches the
model cannot explain (off-network riding, GPS teleports) are retried with a
wider beam, then skipped, and matching resumes past them.

The original heuristic matcher (heading-aware edge snapping with
highway-type penalties, shortest-path routing, and loop removal) is kept
and selectable with `MATCHER = "heuristic"` in `bike_routes/config.py`.
Changing the matcher or its parameters triggers a full reprocess
automatically.

## Updating the Map

Rides come off a Garmin watch. `update.py` does the whole loop — fetch new
rides, convert, reprocess, commit — on Windows, WSL, macOS, or Linux:

```bash
python update.py         # or python update.py --days 30 to narrow the lookback
git push                 # GitHub Pages serves docs/ straight from main
```

It leaves the commit unpushed on purpose, so you can look at the map first.

This runs locally rather than in CI, deliberately. The ride CSVs and the
~260 MB OSM graph cache already live on this machine, and Garmin's login
sits behind Cloudflare TLS fingerprinting that tends to block datacenter IPs
— so a home network is both simpler and likelier to work than a runner.

### Setting up Garmin access

Garmin has no personal-use API — the Connect Developer Program only accepts
legal entities — so `bike_routes.ingest.garmin_sync` uses the endpoints the
Connect web UI uses, via
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect).
Log in once to leave a token behind:

```bash
pip install '.[garmin]'
python -c "
from garminconnect import Garmin
Garmin(input('email: '), input('password: '),
       prompt_mfa=lambda: input('MFA code: ')).login('~/.garminconnect')
"
```

**If that returns 429 ("rate limited"):** Garmin throttles its SSO endpoints
per *account*, keyed on the account email — so changing network or VPN does
not help, and every retry re-arms the block. Stop retrying, confirm you are
on `garminconnect>=0.3.2` (earlier releases lack the `widget+cffi` strategy,
which is the one that gets through while an account is throttled), and if all
five strategies still 429, wait it out — reports range from under an hour to
about two days. `logging.basicConfig(level=logging.DEBUG)` before the login
shows which strategies were actually tried.

After that `garmin_sync` reads `~/.garminconnect` on its own. The token is
good for about a year; when it expires the script fails loudly with a re-mint
message rather than silently fetching nothing. To keep tokens elsewhere, set
`GARMINTOKENS` to a path — or, if you ever do want this in CI, to the token
JSON itself.

Rides are saved as `garmin_<activityId>.gpx`, so the activity id is the dedup
key and re-runs only fetch what's missing. Indoor and virtual rides are
skipped — they carry no usable GPS track.

## Cache Files

The pipeline keeps everything it generates in `cache/` (auto-managed,
gitignored):

- `cache/osm_graph_cache.pkl` — merged OSM street graph (~260 MB)
- `cache/hmm_map_cache.pkl` — HMM matcher's map index (nodes + adjacency);
  lets runs and worker processes skip loading the full graph
- `cache/state.pkl` — processed filenames, edge counts, per-edge speeds,
  config snapshot
- `cache/render_cache.pkl` — pre-extracted edge geometries, highway classes,
  names
- `cache/route_cache.pkl` — shortest-path results between node pairs
- `cache/cache_versions.json` — osmnx/networkx versions that wrote the graph
- `cache/weather_cache.json` — Open-Meteo daily weather, so a failed API call
  falls back to the last good copy

These used to sit loose in the repo root; the first run after upgrading moves
any it finds into `cache/` rather than refetching (a rename, so the 260 MB
graph is not recopied).

Delete any cache file — or the whole directory — to force a rebuild.
Changing processing parameters automatically triggers a full reprocess, and
upgrading osmnx/networkx automatically refetches the graph (pickled graphs
are version-bound).

## License

MIT
