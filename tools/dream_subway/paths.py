"""Shared paths for the dream-subway derivation. Run every script from the repo root."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Intermediates land in cache/ (gitignored), beside every other generated file.
WORK = os.environ.get("DREAM_SUBWAY_WORK", os.path.join(ROOT, "cache", "dream_subway"))
GEO = os.environ.get("DREAM_SUBWAY_GEO", os.path.join(ROOT, "docs", "rides.geojson.gz"))
RIDES = os.environ.get("DREAM_SUBWAY_RIDES", os.path.join(ROOT, "rides"))
HERE = os.path.dirname(os.path.abspath(__file__))

os.makedirs(WORK, exist_ok=True)


def work(name):
    return os.path.join(WORK, name)
