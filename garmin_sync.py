"""Download new outdoor cycling activities from Garmin Connect as GPX.

Garmin offers no Dropbox export and no personal-use API (the Connect Developer
Program only accepts legal entities), so this talks to the same endpoints the
Connect web UI uses, via python-garminconnect.

Authentication is token-only: mint a token blob once on a machine you can do
MFA on, and pass it as ``GARMINTOKENS`` (a path, or the JSON itself). Tokens
skip Garmin's SSO endpoint, which is Cloudflare-fingerprinted and often blocks
datacenter IPs like CI runners.

Files are written as ``garmin_<activityId>.gpx``: the activity id is the dedup
key, so re-running is cheap and only missing rides are downloaded. The GPX then
feeds gpx_to_csv.py unchanged.

Usage:
    python garmin_sync.py incoming/ [--days 365]
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Trainer and virtual rides record no usable GPS track.
INDOOR_TYPES = frozenset({"indoor_cycling", "virtual_ride"})

# Wide enough that a few missed weekly runs still catch up on their own; the
# per-activity file check keeps the cost to one listing call plus new rides.
DEFAULT_LOOKBACK_DAYS = 365


def _connect() -> Any:  # noqa: ANN401 -- garminconnect.Garmin, imported lazily
    """Log in to Garmin Connect using the GARMINTOKENS token store.

    Imported here rather than at module scope so the pipeline's own CI, which
    installs the package without the ``garmin`` extra, can still import and
    test this module.
    """
    from garminconnect import Garmin  # noqa: PLC0415 -- optional dependency

    client = Garmin()
    try:
        needs_mfa, _ = client.login()
    except Exception as exc:
        msg = f"Garmin login failed: {exc}\nRe-mint GARMINTOKENS (see README)."
        raise SystemExit(msg) from exc
    if needs_mfa:
        msg = "Garmin demanded MFA, so the stored token is no longer valid.\nRe-mint GARMINTOKENS (see README)."
        raise SystemExit(msg)
    return client


def _outdoor_rides(activities: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Filter an activity listing to outdoor rides as [(activity_id, type_key)]."""
    rides = []
    for act in activities:
        type_key = (act.get("activityType") or {}).get("typeKey", "")
        activity_id = act.get("activityId")
        if activity_id is None or type_key in INDOOR_TYPES:
            continue
        rides.append((str(activity_id), type_key))
    return rides


def sync(client: Any, out_dir: Path, days: int) -> int:  # noqa: ANN401 -- garminconnect.Garmin
    """Download rides from the last `days` that aren't in out_dir yet.

    Returns the number of newly written GPX files. Individual download
    failures are reported and skipped -- the file stays absent, so the next
    run retries it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    start = (datetime.now(tz=timezone.utc) - timedelta(days=days)).date().isoformat()

    try:
        activities = client.get_activities_by_date(start, activitytype="cycling", sortorder="asc")
    except Exception as exc:
        msg = f"Could not list Garmin activities since {start}: {exc}"
        raise SystemExit(msg) from exc

    rides = _outdoor_rides(activities)
    print(f"Garmin: {len(rides)} outdoor rides since {start}")

    written = 0
    failed = 0
    for activity_id, type_key in rides:
        out_path = out_dir / f"garmin_{activity_id}.gpx"
        if out_path.exists():
            continue
        try:
            data = client.download_activity(activity_id, dl_fmt=client.ActivityDownloadFormat.GPX)
        except Exception as exc:
            print(f"  Failed {activity_id} ({type_key}): {exc}")
            failed += 1
            continue
        out_path.write_bytes(data)
        print(f"  {activity_id} ({type_key}) -> {out_path.name}")
        written += 1

    print(f"Done: {written} new GPX files in {out_dir} ({failed} failed)")
    return written


def main() -> None:
    """Sync new Garmin rides into a GPX directory."""
    parser = argparse.ArgumentParser(
        description="Download new outdoor cycling activities from Garmin Connect as GPX."
    )
    parser.add_argument("out_dir", type=Path, help="directory to write GPX files into")
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        metavar="N",
        help=f"how far back to look for rides (default: {DEFAULT_LOOKBACK_DAYS})",
    )
    args = parser.parse_args()
    sync(_connect(), args.out_dir, args.days)


if __name__ == "__main__":
    sys.exit(main())
