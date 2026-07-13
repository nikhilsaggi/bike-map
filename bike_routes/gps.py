"""GPS trace loading, filtering, and resampling."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import config


def haversine_m(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Compute distance in metres between WGS-84 points (scalar or array)."""
    R = 6_371_000
    phi1, phi2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlam / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(a))
def resample_ride_by_distance(coords: np.ndarray, spacing_m: float) -> np.ndarray:
    """Resample (N,2) [lat,lon] array to ~spacing_m metre intervals."""
    if len(coords) < 2:
        return coords
    seg_dists = haversine_m(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    dists = np.empty(len(coords))
    dists[0] = 0.0
    np.cumsum(seg_dists, out=dists[1:])
    total = dists[-1]
    if total < spacing_m:
        return coords[[0, -1]]
    targets = np.arange(0, total, spacing_m)
    return np.column_stack(
        [
            np.interp(targets, dists, coords[:, 0]),
            np.interp(targets, dists, coords[:, 1]),
        ]
    )
def _split_at_gaps(coords: np.ndarray, max_gap_m: float) -> list[np.ndarray]:
    """Split (N,2) [lat,lon] into sub-arrays wherever consecutive points exceed max_gap_m."""
    if len(coords) < 2:
        return [coords]
    dists = haversine_m(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    gap_idx = np.where(dists > max_gap_m)[0] + 1
    if len(gap_idx) == 0:
        return [coords]
    return np.split(coords, gap_idx)
def _is_nyc_ride(coords: np.ndarray) -> bool:
    """Check if any point in the ride falls within config.NYC_BBOX."""
    lat_min, lon_min, lat_max, lon_max = config.NYC_BBOX
    in_bbox = (
        (coords[:, 0] >= lat_min)
        & (coords[:, 0] <= lat_max)
        & (coords[:, 1] >= lon_min)
        & (coords[:, 1] <= lon_max)
    )
    return in_bbox.any()
def _load_and_resample(
    filenames: list[str],
) -> tuple[list[tuple[str, np.ndarray]], int]:
    """Load CSVs, filter to NYC, split at GPS gaps, and resample.

    Returns (nyc_rides, skipped_non_nyc) where nyc_rides is [(filename, coords)].
    A single file may produce multiple entries if it has GPS gaps.
    """
    rides = []
    non_nyc = 0
    for f in filenames:
        path = Path(config.RIDES_FOLDER) / f
        try:
            data = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(0, 1))
        except Exception as exc:
            print(f"  Skipping {f}: {exc}")
            continue
        if data.ndim == 1:
            data = data.reshape(1, 2)
        coords = data[:, ::-1]  # (lon, lat) -> (lat, lon)
        if not _is_nyc_ride(coords):
            non_nyc += 1
            continue
        segments = _split_at_gaps(coords, config.MAX_GPS_GAP_M)
        for seg in segments:
            rs = resample_ride_by_distance(seg, config.RESAMPLE_SPACING_M)
            if len(rs) >= 2:
                rides.append((f, rs))
    return rides, non_nyc
