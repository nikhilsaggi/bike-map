#!/usr/bin/env bash
# Fetch new rides from Garmin Connect and rebuild the map.
#
# Run from the repo root. Any arguments are passed through to garmin_sync.py,
# so `./update.sh --days 30` narrows the lookback window.
#
# Log in to Garmin once first, so ~/.garminconnect holds a token (see README).
# The commit is left unpushed on purpose -- check the map, then `git push`.

set -euo pipefail

cd "$(dirname "$0")"

python garmin_sync.py incoming/ "$@"
python gpx_to_csv.py incoming/ rides/
python -m bike_routes --no-png   # drop --no-png to also refresh the static PNGs

if git diff --quiet -- docs/rides.geojson.gz; then
    echo "Map unchanged; nothing to commit."
else
    git add docs/rides.geojson.gz
    git commit -m "Update bike map GeoJSON"
    echo "Committed. Review the map, then: git push"
fi
