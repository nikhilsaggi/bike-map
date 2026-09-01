"""Tests for mid-match checkpointing (incremental state folding)."""

from __future__ import annotations

from collections import Counter

from bike_routes.cache import _empty_state
from bike_routes.cli import _apply_results, _ready_results


def _state():
    s = _empty_state()
    s["edge_counts"] = {}
    s["processed_files"] = set()
    return s


def _results(fname, edges, skipped=0):
    return (fname, edges, skipped)


def test_apply_results_is_order_independent():
    """Folding in chunks must equal one globally sorted pass."""
    batch = [
        _results("b.csv", [(1, 2), (2, 3)]),
        _results("a.csv", [(2, 3)]),
        _results("c.csv", [(1, 2), (3, 4)]),
    ]

    one_pass = _state()
    _apply_results(one_pass, batch)

    chunked = _state()
    _apply_results(chunked, [batch[2]])
    _apply_results(chunked, [batch[0]])
    _apply_results(chunked, [batch[1]])

    assert one_pass["edge_counts"] == chunked["edge_counts"]
    assert one_pass["processed_files"] == chunked["processed_files"]
    # edge_rides list order may differ, but export.py only reads it via
    # min() and set(), so those views must agree.
    for edge, rides in one_pass["edge_rides"].items():
        other = chunked["edge_rides"][edge]
        assert set(rides) == set(other)
        assert min(rides) == min(other)


def test_apply_results_counts_traversals_and_skips():
    state = _state()
    skipped = _apply_results(
        state,
        [_results("a.csv", [(1, 2)], skipped=3), _results("b.csv", [(1, 2)], skipped=4)],
    )
    assert skipped == 7
    assert state["edge_counts"][(1, 2)] == 2
    assert state["processed_files"] == {"a.csv", "b.csv"}


def test_apply_results_counts_an_edge_once_per_ride():
    """edge_counts is rides, not passes.

    The matcher's repeats cannot be trusted as traversals -- lattice
    oscillation at an intersection looks the same -- so how many times a ride
    crossed an edge is measured from timestamps in edge_speed.py instead.
    """
    state = _state()
    _apply_results(state, [_results("a.csv", [(1, 2), (1, 2), (1, 2)])])
    assert state["edge_counts"][(1, 2)] == 1
    assert state["edge_rides"][(1, 2)] == ["a.csv"]


def test_ready_results_releases_unsplit_files_immediately():
    pending = {}
    seg_total = Counter({"a.csv": 1})
    ready = _ready_results(pending, seg_total, [_results("a.csv", [(1, 2)])])
    assert [r[0] for r in ready] == ["a.csv"]
    assert pending == {}


def test_ready_results_holds_split_files_until_complete():
    """A file split at a GPS gap must not be released on its first segment."""
    pending = {}
    seg_total = Counter({"split.csv": 2, "solo.csv": 1})

    first = _ready_results(pending, seg_total, [_results("split.csv", [(1, 2)])])
    assert first == []
    assert "split.csv" in pending

    second = _ready_results(
        pending,
        seg_total,
        [_results("split.csv", [(3, 4)]), _results("solo.csv", [(5, 6)])],
    )
    assert sorted(r[0] for r in second) == ["solo.csv", "split.csv", "split.csv"]
    assert pending == {}


def test_split_file_contributes_all_segments_once_complete():
    """Both halves of a split ride land, and the file counts as one ride."""
    pending = {}
    seg_total = Counter({"split.csv": 2})
    state = _state()

    _apply_results(state, _ready_results(pending, seg_total, [_results("split.csv", [(1, 2)])]))
    assert state["processed_files"] == set()  # held back, so a checkpoint here is safe

    _apply_results(state, _ready_results(pending, seg_total, [_results("split.csv", [(3, 4)])]))
    assert state["processed_files"] == {"split.csv"}
    assert state["edge_counts"] == {(1, 2): 1, (3, 4): 1}


def test_split_file_counts_a_shared_edge_once():
    """An edge on both sides of a GPS gap is still one ride, listed once."""
    pending = {}
    seg_total = Counter({"split.csv": 2})
    state = _state()

    batch = [_results("split.csv", [(1, 2), (3, 4)]), _results("split.csv", [(1, 2), (5, 6)])]
    _apply_results(state, _ready_results(pending, seg_total, batch))

    assert state["edge_counts"] == {(1, 2): 1, (3, 4): 1, (5, 6): 1}
    assert state["edge_rides"][(1, 2)] == ["split.csv"]
