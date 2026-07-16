"""Tests for GPX -> CSV conversion."""

from __future__ import annotations

import gpx_to_csv

GPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1" creator="test">
  <trk><trkseg>
{points}
  </trkseg></trk>
</gpx>
"""


def _write_gpx(path, points):
    body = "\n".join(
        f'    <trkpt lat="{lat}" lon="{lon}"><time>{ts}</time></trkpt>' for lon, lat, ts in points
    )
    path.write_text(GPX_TEMPLATE.format(points=body))


def test_gpx_to_csv_basic(tmp_path):
    gpx = tmp_path / "ride.gpx"
    out_dir = tmp_path / "rides"
    out_dir.mkdir()
    _write_gpx(
        gpx,
        [
            ("-73.98478", "40.76030", "2023-12-17T18:41:53Z"),
            ("-73.98475", "40.76035", "2023-12-17T18:41:54Z"),
        ],
    )

    out = gpx_to_csv.gpx_to_csv(gpx, out_dir)
    assert out is not None
    assert out.name == "2023-12-17_18-41-53+0000.csv"

    lines = out.read_text().splitlines()
    assert lines[0] == "longitude,latitude,timestamp"
    assert lines[1] == "-73.98478,40.76030,2023-12-17T18:41:53Z"
    assert len(lines) == 3


def test_gpx_to_csv_skips_existing(tmp_path):
    gpx = tmp_path / "ride.gpx"
    out_dir = tmp_path / "rides"
    out_dir.mkdir()
    _write_gpx(gpx, [("-73.98", "40.76", "2023-12-17T18:41:53Z")])

    assert gpx_to_csv.gpx_to_csv(gpx, out_dir) is not None
    assert gpx_to_csv.gpx_to_csv(gpx, out_dir) is None  # already converted


def test_gpx_to_csv_no_trackpoints(tmp_path):
    gpx = tmp_path / "empty.gpx"
    out_dir = tmp_path / "rides"
    out_dir.mkdir()
    _write_gpx(gpx, [])

    assert gpx_to_csv.gpx_to_csv(gpx, out_dir) is None
    assert list(out_dir.iterdir()) == []


def test_gpx_to_csv_offset_timestamp(tmp_path):
    gpx = tmp_path / "ride.gpx"
    out_dir = tmp_path / "rides"
    out_dir.mkdir()
    _write_gpx(gpx, [("-73.98", "40.76", "2024-06-19T13:35:52-04:00")])

    out = gpx_to_csv.gpx_to_csv(gpx, out_dir)
    assert out is not None
    assert out.suffix == ".csv"
    assert out.name.startswith("2024-06-19_13-35-52")
