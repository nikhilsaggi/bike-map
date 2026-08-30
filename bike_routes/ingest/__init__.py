"""Ride ingest: Garmin Connect download and GPX -> CSV conversion.

The front of the pipeline. These run by hand (or via update.py) to fill
``rides/`` before ``python -m bike_routes`` matches anything:

    python -m bike_routes.ingest.garmin_sync incoming/ [--days 365]
    python -m bike_routes.ingest.gpx_to_csv incoming/ rides/

Kept out of CI on purpose -- Garmin's login is behind Cloudflare TLS
fingerprinting that blocks datacenter IPs, and garminconnect is an optional
dependency (``pip install '.[garmin]'``).
"""

from __future__ import annotations

__all__: list[str] = []
