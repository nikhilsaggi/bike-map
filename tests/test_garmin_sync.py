"""Tests for the Garmin Connect ride sync (no network: the client is a stub)."""

from __future__ import annotations

import garmin_sync


class _FakeFormat:
    GPX = "gpx"


class _FakeClient:
    """Stands in for garminconnect.Garmin, recording what was downloaded."""

    ActivityDownloadFormat = _FakeFormat

    def __init__(self, activities, fail_ids=()):
        self.activities = activities
        self.fail_ids = set(fail_ids)
        self.downloaded = []

    def get_activities_by_date(self, startdate, activitytype=None, sortorder=None):
        self.listed = (startdate, activitytype, sortorder)
        return self.activities

    def download_activity(self, activity_id, dl_fmt=None):
        if activity_id in self.fail_ids:
            msg = "boom"
            raise RuntimeError(msg)
        self.downloaded.append((activity_id, dl_fmt))
        return f"<gpx id={activity_id}/>".encode()


def _activity(activity_id, type_key="cycling"):
    return {"activityId": activity_id, "activityType": {"typeKey": type_key}}


def test_skips_indoor_and_virtual_rides():
    rides = garmin_sync._outdoor_rides(
        [
            _activity(1, "cycling"),
            _activity(2, "indoor_cycling"),
            _activity(3, "virtual_ride"),
            _activity(4, "gravel_cycling"),
        ]
    )
    assert rides == [("1", "cycling"), ("4", "gravel_cycling")]


def test_skips_activities_without_an_id():
    assert garmin_sync._outdoor_rides([{"activityType": {"typeKey": "cycling"}}]) == []


def test_missing_activity_type_is_kept():
    # An unusual/absent typeKey is not a reason to silently drop a ride; the
    # NYC bbox filter downstream is the real gate.
    assert garmin_sync._outdoor_rides([{"activityId": 7}]) == [("7", "")]


def test_sync_writes_one_gpx_per_ride(tmp_path):
    client = _FakeClient([_activity(11), _activity(12)])
    written = garmin_sync.sync(client, tmp_path, days=30)

    assert written == 2
    assert sorted(p.name for p in tmp_path.glob("*.gpx")) == [
        "garmin_11.gpx",
        "garmin_12.gpx",
    ]
    assert (tmp_path / "garmin_11.gpx").read_bytes() == b"<gpx id=11/>"
    assert client.listed == (
        (garmin_sync.datetime.now(tz=garmin_sync.timezone.utc) - garmin_sync.timedelta(days=30))
        .date()
        .isoformat(),
        "cycling",
        "asc",
    )


def test_sync_is_idempotent(tmp_path):
    activities = [_activity(11), _activity(12)]
    garmin_sync.sync(_FakeClient(activities), tmp_path, days=30)

    second = _FakeClient(activities)
    assert garmin_sync.sync(second, tmp_path, days=30) == 0
    assert second.downloaded == []


def test_failed_download_leaves_no_file_so_it_retries(tmp_path):
    client = _FakeClient([_activity(11), _activity(12)], fail_ids={"11"})
    assert garmin_sync.sync(client, tmp_path, days=30) == 1
    assert not (tmp_path / "garmin_11.gpx").exists()
    assert (tmp_path / "garmin_12.gpx").exists()

    retry = _FakeClient([_activity(11), _activity(12)])
    assert garmin_sync.sync(retry, tmp_path, days=30) == 1
    assert retry.downloaded == [("11", "gpx")]
