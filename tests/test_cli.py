"""Tests for command-line argument parsing."""

from __future__ import annotations

from bike_routes import cli


def test_defaults():
    args = cli._parse_args([])
    assert args.sample is None
    assert args.rides is None
    assert args.no_png is False
    assert args.workers is None


def test_all_flags():
    args = cli._parse_args(
        ["--sample", "5", "--rides", "a.csv", "b.csv", "--no-png", "--workers", "2"]
    )
    assert args.sample == 5
    assert args.rides == ["a.csv", "b.csv"]
    assert args.no_png is True
    assert args.workers == 2
