"""Tests for direction-split per-edge speed.

Geometry is built in local metres via conftest.lonlat, so every expected
value here is hand-computable.
"""

from __future__ import annotations

import pytest
from conftest import lonlat

from bike_routes import cache, config, edge_speed

# A straight 100 m edge running east from the origin.
EDGE = (1, 2)
GEOM = {EDGE: [lonlat(0, 0), lonlat(100, 0)]}


def _csv(tmp_path, rows, name="2024-06-19_12-00-00_-0400.csv"):
    """Write a ride CSV from [(x_m, y_m, epoch_offset_s)] rows."""
    lines = ["longitude,latitude,timestamp"]
    for x, y, t in rows:
        lon, lat = lonlat(x, y)
        stamp = f"2024-06-19T{12 + t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}Z"
        lines.append(f"{lon},{lat},{stamp}")
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return p


def _state(files):
    return {
        "processed_files": set(files),
        "edge_rides": {EDGE: list(files)},
        "edge_speed": {},
        "speed_rides": set(),
        "speed_version": config.SPEED_VERSION,
    }


def _run(tmp_path, rows, monkeypatch, geom=None, name="2024-06-19_12-00-00_-0400.csv"):
    _csv(tmp_path, rows, name)
    monkeypatch.setattr(config, "RIDES_FOLDER", str(tmp_path))
    st = _state([name])
    edge_speed._backfill_edge_speeds(st, geom or GEOM)
    return st


# -- track loading -----------------------------------------------------------


def test_load_track_accepts_both_timestamp_formats(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text(
        "longitude,latitude,timestamp\n"
        "-73.99,40.73,2021-09-04 18:53:31 -0400\n"
        "-73.98,40.74,2021-09-04 18:53:41 -0400\n"
    )
    track = edge_speed._load_ride_track(p)
    assert track is not None
    latlon, times = track
    assert latlon.shape == (2, 2)
    assert times[1] - times[0] == pytest.approx(10.0)


def test_load_track_rejects_short_file(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("longitude,latitude,timestamp\n-73.99,40.73,2021-09-04 18:53:31 -0400\n")
    assert edge_speed._load_ride_track(p) is None


# -- direction ---------------------------------------------------------------


def test_forward_pass_fills_forward_bucket(tmp_path, monkeypatch):
    # 11 fixes 10 m apart, 2 s each: 100 m in 20 s = 18 km/h.
    rows = [(10 * i, 0, 2 * i) for i in range(11)]
    st = _run(tmp_path, rows, monkeypatch)
    chunk = st["edge_speed"][EDGE]["c"][0]
    assert chunk[edge_speed._FWD + 3] == 1
    assert chunk[edge_speed._REV + 3] == 0
    assert chunk[edge_speed._FWD] == pytest.approx(100, abs=2)
    assert chunk[edge_speed._FWD + 1] == pytest.approx(20, abs=0.5)
    assert edge_speed._chunk_speed_kmh(chunk, edge_speed._FWD) == pytest.approx(18, abs=0.5)


def test_reverse_pass_fills_reverse_bucket(tmp_path, monkeypatch):
    rows = [(100 - 10 * i, 0, 2 * i) for i in range(11)]
    st = _run(tmp_path, rows, monkeypatch)
    chunk = st["edge_speed"][EDGE]["c"][0]
    assert chunk[edge_speed._REV + 3] == 1
    assert chunk[edge_speed._FWD + 3] == 0
    assert edge_speed._chunk_speed_kmh(chunk, edge_speed._REV) == pytest.approx(18, abs=0.5)


def test_out_and_back_fills_both_buckets(tmp_path, monkeypatch):
    out = [(10 * i, 0, 2 * i) for i in range(11)]
    back = [(100 - 10 * i, 0, 22 + 2 * i) for i in range(1, 11)]
    st = _run(tmp_path, out + back, monkeypatch)
    chunk = st["edge_speed"][EDGE]["c"][0]
    assert chunk[edge_speed._FWD + 3] == 1
    assert chunk[edge_speed._REV + 3] == 1


# -- rejection rules ---------------------------------------------------------


def test_edge_far_from_trace_accumulates_nothing(tmp_path, monkeypatch):
    """An edge the matcher reached by routing, not by the rider passing it."""
    far = {EDGE: [lonlat(0, 500), lonlat(100, 500)]}
    st = _run(tmp_path, [(10 * i, 0, 2 * i) for i in range(11)], monkeypatch, geom=far)
    assert st["edge_speed"] == {}


def test_recording_gap_splits_the_pass(tmp_path, monkeypatch):
    """A 10-minute gap is a stop, not 600 s of traversal time."""
    rows = [(10 * i, 0, 2 * i) for i in range(6)]  # 0..50 m
    rows += [(10 * i, 0, 600 + 2 * i) for i in range(6, 11)]  # resumes 10 min later
    st = _run(tmp_path, rows, monkeypatch)
    chunk = st["edge_speed"][EDGE]["c"][0]
    assert chunk[edge_speed._FWD + 1] < 60, "the gap must not be counted as traversal time"


def test_implausible_speed_is_rejected(tmp_path, monkeypatch):
    # 100 m in 2 s = 180 km/h, well over SPEED_MAX_KMH.
    st = _run(tmp_path, [(0, 0, 0), (50, 0, 1), (100, 0, 2)], monkeypatch)
    assert st["edge_speed"] == {}


# -- accumulator invariants --------------------------------------------------


def test_backfill_is_idempotent(tmp_path, monkeypatch):
    rows = [(10 * i, 0, 2 * i) for i in range(11)]
    st = _run(tmp_path, rows, monkeypatch)
    before = [list(c) for c in st["edge_speed"][EDGE]["c"]]
    assert edge_speed._backfill_edge_speeds(st, GEOM) == 0
    assert [list(c) for c in st["edge_speed"][EDGE]["c"]] == before


def test_fold_order_does_not_matter(tmp_path, monkeypatch):
    """The chunked-fold invariant: accumulators combine by addition only."""
    a = "2024-06-19_12-00-00_-0400.csv"
    b = "2024-06-20_12-00-00_-0400.csv"
    _csv(tmp_path, [(10 * i, 0, 2 * i) for i in range(11)], a)
    _csv(tmp_path, [(100 - 10 * i, 0, 3 * i) for i in range(11)], b)
    monkeypatch.setattr(config, "RIDES_FOLDER", str(tmp_path))

    results = []
    for order in ([a, b], [b, a]):
        st = _state(order)
        st["edge_rides"] = {EDGE: list(order)}
        edge_speed._backfill_edge_speeds(st, GEOM)
        results.append([list(c) for c in st["edge_speed"][EDGE]["c"]])
    assert results[0] == results[1]


# -- chunking ----------------------------------------------------------------


def test_short_edge_is_one_chunk_long_edge_is_many():
    assert edge_speed._n_chunks(63) == 1
    assert edge_speed._n_chunks(2163) == 14
    assert edge_speed._n_chunks(1e9) == config.SPEED_MAX_CHUNKS


def test_chunk_slices_partition_the_line():
    coords = [lonlat(0, 0), lonlat(300, 0)]
    pieces = edge_speed._chunk_slices(coords, 3)
    assert len(pieces) == 3
    # Contiguous: each piece starts where the previous ended.
    for a, b in zip(pieces, pieces[1:]):
        assert a[-1] == b[0]
    assert pieces[0][0] == coords[0]
    assert pieces[-1][-1] == coords[-1]


def test_chunk_slices_declines_to_split_degenerate_line():
    coords = [lonlat(0, 0), lonlat(0, 0)]
    assert edge_speed._chunk_slices(coords, 4) == [coords]


def test_long_edge_records_a_gradient(tmp_path, monkeypatch):
    """Speed varying along an edge must land in different chunks."""
    geom = {EDGE: [lonlat(0, 0), lonlat(600, 0)]}
    # Fast over the first 300 m (10 m/s), slow over the second (2.5 m/s).
    rows = [(10 * i, 0, i) for i in range(31)]
    rows += [(300 + 10 * i, 0, 30 + 4 * i) for i in range(1, 31)]
    st = _run(tmp_path, rows, monkeypatch, geom=geom)
    chunks = st["edge_speed"][EDGE]["c"]
    assert len(chunks) == 4
    first = edge_speed._chunk_speed_kmh(chunks[0], edge_speed._FWD)
    last = edge_speed._chunk_speed_kmh(chunks[-1], edge_speed._FWD)
    assert first > last * 2, f"expected a gradient, got {first} then {last}"


# -- orientation -------------------------------------------------------------


def test_oriented_chunks_flips_a_reversed_geometry():
    coords = [lonlat(0, 0), lonlat(100, 0)]
    rec = {"b": edge_speed._chord_bearing(coords), "c": [[9, 1, 1, 1, 0, 0, 0, 0]]}
    same = edge_speed._oriented_chunks(rec, coords)
    assert same[0][edge_speed._FWD] == 9
    flipped = edge_speed._oriented_chunks(rec, list(reversed(coords)))
    assert flipped[0][edge_speed._REV] == 9, "buckets must swap when the geometry reverses"
    assert flipped[0][edge_speed._FWD] == 0


def test_oriented_chunks_reverses_chunk_order_too():
    coords = [lonlat(0, 0), lonlat(300, 0)]
    rec = {
        "b": edge_speed._chord_bearing(coords),
        "c": [[1, 1, 1, 1, 0, 0, 0, 0], [2, 1, 1, 1, 0, 0, 0, 0]],
    }
    flipped = edge_speed._oriented_chunks(rec, list(reversed(coords)))
    # Chunk 0 of the reversed line is the far end of the original.
    assert flipped[0][edge_speed._REV] == 2
    assert flipped[1][edge_speed._REV] == 1


# -- traversal counts --------------------------------------------------------
# The map's frequency is passes, not rides, and everything below is about the
# one thing that must not happen: inventing a traversal the rider never made.

RIDE = "2024-06-19_12-00-00_-0400.csv"


def _traversals(st, key=EDGE, ride=RIDE):
    return edge_speed.ride_traversals(st, key, ride)


def test_single_pass_counts_once(tmp_path, monkeypatch):
    rows = [(10 * i, 0, 2 * i) for i in range(11)]
    st = _run(tmp_path, rows, monkeypatch)
    assert _traversals(st) == 1
    # Ones are not stored: absence is what the floor reads.
    assert st["edge_traversals"] == {}


def test_out_and_back_counts_twice(tmp_path, monkeypatch):
    """The turnaround _split_monotonic finds is a second traversal."""
    out = [(10 * i, 0, 2 * i) for i in range(11)]
    back = [(100 - 10 * i, 0, 22 + 2 * i) for i in range(1, 11)]
    st = _run(tmp_path, out + back, monkeypatch)
    assert _traversals(st) == 2


def test_second_lap_counts_twice(tmp_path, monkeypatch):
    """Leaving the edge and coming back the same way is two traversals."""
    lap1 = [(10 * i, 0, 2 * i) for i in range(11)]
    away = [(200, 500, 40), (300, 500, 60)]  # off-edge, beyond SPEED_SNAP_M
    lap2 = [(10 * i, 0, 100 + 2 * i) for i in range(11)]
    st = _run(tmp_path, lap1 + away + lap2, monkeypatch)
    assert _traversals(st) == 2


def test_stop_mid_edge_stays_one_traversal(tmp_path, monkeypatch):
    """_runs cuts at a recording gap for speed's sake; counting must not.

    A rider who stops for two minutes halfway down a block rode that block
    once.  The resumed pass continues from where the first stopped, which is
    what tells it apart from a second lap re-entering at the far end.
    """
    before = [(10 * i, 0, 2 * i) for i in range(5)]  # 0 -> 40 m
    after = [(40 + 10 * i, 0, 128 + 2 * i) for i in range(7)]  # 120 s gap, 40 -> 100 m
    st = _run(tmp_path, before + after, monkeypatch)
    assert st["edge_speed"][EDGE]["c"][0][edge_speed._FWD + 3] == 2  # speed still splits
    assert _traversals(st) == 1


def test_gps_wobble_is_not_a_second_traversal(tmp_path, monkeypatch):
    """A backtrack shorter than SPEED_REVERSAL_M is noise, not a turnaround."""
    xs = [0, 10, 20, 14, 30, 40, 50, 45, 60, 70, 80, 90, 100]
    rows = [(x, 0, 2 * i) for i, x in enumerate(xs)]
    st = _run(tmp_path, rows, monkeypatch)
    assert _traversals(st) == 1


# A 2 km edge, the shape that breaks counting rules a 100 m block cannot: the
# Manhattan and Williamsburg bridge paths are each a single edge that long.
LONG = {EDGE: [lonlat(0, 0), lonlat(2000, 0)]}


def _fragmented_crossing(t0):
    """One 2 km crossing at 5 m/s, arriving as three fragments.

    The two off-edge fixes are the trace snapping to a neighbouring way --
    beside a bridge path, the roadway -- which ends the run in _runs exactly
    as a recording gap does.
    """
    rows = []
    for i in range(21):
        x, t = 100 * i, t0 + 20 * i
        rows.append((x, 500, t) if x in (700, 1400) else (x, 0, t))
    return rows


def test_a_fragmented_crossing_of_a_long_edge_counts_once(tmp_path, monkeypatch):
    """Every fragment of a crossing is not itself a crossing.

    One ride over the Williamsburg Bridge arrived here as 23 fragments and
    scored 10 traversals of the 2.3 km path.  They are one: each resumes
    ahead of where the last stopped, so the merged span sweeps the edge once.
    """
    st = _run(tmp_path, _fragmented_crossing(0), monkeypatch, geom=LONG)
    assert st["edge_traversals"] == {}
    assert _traversals(st) == 1


def test_two_fragmented_crossings_still_count_twice(tmp_path, monkeypatch):
    """Merging fragments is what lets real repeats clear the coverage bar.

    Unmerged, no fragment covers enough of the edge to count and both
    crossings would floor to one.
    """
    away = [(2500, 500, 420), (2600, 500, 440)]
    rows = _fragmented_crossing(0) + away + _fragmented_crossing(600)
    st = _run(tmp_path, rows, monkeypatch, geom=LONG)
    assert _traversals(st) == 2


def test_clipping_a_long_edge_twice_is_not_two_traversals(tmp_path, monkeypatch):
    """A pass has to sweep the edge, not nick the end of it.

    Both visits here cover 100 m of 2 km.  Without the coverage rule each one
    scored as a full crossing, so a wobble at one end of a bridge outvoted the
    ride that actually rode it.
    """
    first = [(10 * i, 0, 2 * i) for i in range(11)]  # 0 -> 100 m
    away = [(200, 500, 60), (300, 500, 80)]  # off-edge, beyond SPEED_SNAP_M
    second = [(10 * i, 0, 100 + 2 * i) for i in range(11)]  # 0 -> 100 m again
    st = _run(tmp_path, first + away + second, monkeypatch, geom=LONG)
    assert st["edge_traversals"] == {}
    assert _traversals(st) == 1


def test_unmeasurable_ride_still_counts_once(tmp_path, monkeypatch):
    """The floor: measurement can raise a count, never take an edge off the map."""
    far = {EDGE: [lonlat(0, 500), lonlat(100, 500)]}
    rows = [(10 * i, 0, 2 * i) for i in range(11)]
    st = _run(tmp_path, rows, monkeypatch, geom=far)
    assert st["edge_traversals"] == {}
    assert _traversals(st) == 1


def test_traversal_counts_total_over_rides():
    st = {
        "edge_rides": {EDGE: ["a.csv", "b.csv"], (3, 4): ["a.csv"]},
        "edge_traversals": {EDGE: {"a.csv": 3}},
    }
    assert edge_speed.traversal_counts(st) == {EDGE: 4, (3, 4): 1}


def test_traversal_counts_ignore_a_repeated_ride_id():
    """Multiplicity comes from measurement, never from how state was written."""
    st = {"edge_rides": {EDGE: ["a.csv", "a.csv"]}, "edge_traversals": {}}
    assert edge_speed.traversal_counts(st) == {EDGE: 1}


# -- version / format guards -------------------------------------------------


def test_version_bump_discards_old_records(tmp_path, monkeypatch):
    """One lever recomputes both outputs of the pass detector."""
    out = [(10 * i, 0, 2 * i) for i in range(11)]
    back = [(100 - 10 * i, 0, 22 + 2 * i) for i in range(1, 11)]
    st = _run(tmp_path, out + back, monkeypatch)
    assert st["edge_speed"]
    assert st["edge_traversals"]
    monkeypatch.setattr(config, "SPEED_VERSION", config.SPEED_VERSION + 1)
    st["processed_files"] = set()  # nothing to re-measure, so the reset shows
    edge_speed._backfill_edge_speeds(st, GEOM)
    assert st["speed_version"] == config.SPEED_VERSION
    assert st["edge_speed"] == {}
    assert st["edge_traversals"] == {}


def test_malformed_records_are_discarded():
    """A format change that forgets to bump SPEED_VERSION must fail closed."""
    assert edge_speed._records_well_formed({})
    assert edge_speed._records_well_formed({EDGE: {"b": 0.0, "c": [[0] * 8]}})
    assert not edge_speed._records_well_formed({EDGE: [0.0] * 9})  # the pre-chunking layout
    assert not edge_speed._records_well_formed({EDGE: {"b": 0.0, "c": [[0] * 4]}})


# -- publish thresholds ------------------------------------------------------


def test_corridor_ranking_splits_a_run_at_the_sign_change():
    """A bridge's whole signal is that the faster direction flips at the crest."""
    coords = [lonlat(x, 0) for x in range(0, 1201, 100)]
    # 6 chunks: the first three faster reverse (westbound), the last three
    # faster forward (eastbound) -- a crest in the middle.
    slow, fast = [100, 40, 40, 5], [100, 20, 20, 5]
    rec = {
        "b": edge_speed._chord_bearing(coords),
        "c": [(slow + fast) if i < 3 else (fast + slow) for i in range(6)],
    }
    out = edge_speed._top_corridors({EDGE: rec}, {EDGE: coords}, {EDGE: "Crest Bridge"})

    assert len(out) == 2, "the two descents must be reported separately"
    east, west = sorted(out, key=lambda c: c["dir"])  # "E" sorts before "W"
    assert (east["dir"], west["dir"]) == ("E", "W")
    # Disjoint halves of one 1200 m edge, so they partition it exactly.
    assert east["m"] + west["m"] == 1200
    for c in out:
        assert (c["fast"], c["slow"], c["gap"]) == (18.0, 9.0, 9.0)
        assert c["name"] == "Crest Bridge"


def test_corridor_ranking_skips_short_and_undersampled_runs(monkeypatch):
    monkeypatch.setattr(config, "SPEED_CORRIDOR_MIN_M", 250.0)
    coords = [lonlat(0, 0), lonlat(600, 0)]
    both = [100, 20, 20, 5, 100, 40, 40, 5]
    rec = {"b": edge_speed._chord_bearing(coords), "c": [list(both) for _ in range(4)]}
    names = {EDGE: "Kent Avenue"}

    assert edge_speed._top_corridors({EDGE: rec}, {EDGE: coords}, names)

    # One direction under the split threshold: nothing to compare.
    thin = {"b": rec["b"], "c": [[100, 20, 20, 5, 100, 40, 40, 1] for _ in range(4)]}
    assert edge_speed._top_corridors({EDGE: thin}, {EDGE: coords}, names) == []

    # Long enough per chunk, but the run is under the length floor.
    monkeypatch.setattr(config, "SPEED_CORRIDOR_MIN_M", 5000.0)
    assert edge_speed._top_corridors({EDGE: rec}, {EDGE: coords}, names) == []


def test_corridor_ranking_breaks_a_run_at_an_unmeasured_gap():
    """Two stretches either side of a gap are not one corridor."""
    coords = [lonlat(x, 0) for x in range(0, 1201, 100)]
    both = [100, 20, 20, 5, 100, 40, 40, 5]
    rec = {
        "b": edge_speed._chord_bearing(coords),
        # Chunk 2 was never ridden both ways, so it splits the edge in two.
        "c": [([0.0] * 8 if i == 2 else list(both)) for i in range(6)],
    }
    out = edge_speed._top_corridors({EDGE: rec}, {EDGE: coords}, {EDGE: "Kent Avenue"})
    # Same direction either side, so the (name, dir) slot keeps only the best
    # -- the point is that 1200 m did not become one 1200 m corridor.
    assert len(out) == 1
    assert out[0]["m"] < 1200


def test_corridor_ranking_keeps_one_entry_per_street_and_direction():
    coords = [lonlat(0, 0), lonlat(600, 0)]
    big = [100, 20, 20, 5, 100, 60, 60, 5]  # 18 vs 6 km/h
    small = [100, 20, 20, 5, 100, 30, 30, 5]  # 18 vs 12 km/h
    edges = {
        (1, 2): {"b": edge_speed._chord_bearing(coords), "c": [list(big) for _ in range(4)]},
        (3, 4): {"b": edge_speed._chord_bearing(coords), "c": [list(small) for _ in range(4)]},
    }
    geom = {(1, 2): coords, (3, 4): coords}
    names = {(1, 2): "Kent Avenue", (3, 4): "Kent Avenue"}
    out = edge_speed._top_corridors(edges, geom, names)
    assert len(out) == 1
    assert out[0]["gap"] == 12.0  # the stronger of the two stretches


def test_corridor_ranking_is_capped(monkeypatch):
    monkeypatch.setattr(config, "SPEED_CORRIDOR_N", 3)
    coords = [lonlat(0, 0), lonlat(600, 0)]
    edges, geom, names = {}, {}, {}
    for i in range(8):
        key = (i, i + 100)
        edges[key] = {
            "b": edge_speed._chord_bearing(coords),
            "c": [[100, 20, 20, 5, 100, 40 + i, 40 + i, 5] for _ in range(4)],
        }
        geom[key] = coords
        names[key] = f"Street {i}"
    out = edge_speed._top_corridors(edges, geom, names)
    assert len(out) == 3
    assert [c["gap"] for c in out] == sorted((c["gap"] for c in out), reverse=True)


def test_hairpin_chunks_do_not_name_a_run():
    """A bridge's spiral approach ramp points nowhere useful."""
    # Five chunks running east, plus a leading ramp that doubles back west.
    coords = [lonlat(0, 0), lonlat(100, 60), lonlat(20, 0)]
    coords += [lonlat(20 + 100 * i, 0) for i in range(1, 6)]
    rec = {
        "b": edge_speed._chord_bearing(coords),
        "c": [[100, 20, 20, 5, 100, 40, 40, 5] for _ in range(4)],
    }
    out = edge_speed._top_corridors({EDGE: rec}, {EDGE: coords}, {EDGE: "Ramp Bridge"})
    assert len(out) == 1
    assert out[0]["dir"] == "E", "the ramp's chord must not set the label"


def test_octant_covers_the_compass():
    assert edge_speed._octant(0) == "N"
    assert edge_speed._octant(90) == "E"
    assert edge_speed._octant(180) == "S"
    assert edge_speed._octant(270) == "W"
    assert edge_speed._octant(359) == "N"  # wraps
    assert edge_speed._octant(45) == "NE"


def test_summary_reports_the_corridor_block():
    coords = [lonlat(0, 0), lonlat(600, 0)]
    rec = {
        "b": edge_speed._chord_bearing(coords),
        "c": [[100, 20, 20, 5, 100, 40, 40, 5] for _ in range(4)],
    }
    summary = edge_speed._speed_summary({EDGE: rec}, {EDGE: coords}, {EDGE: "Kent Avenue"})
    assert summary is not None
    assert summary["split_n"] == config.SPEED_SPLIT_PASSES
    assert summary["measured"] == 4
    assert [c["name"] for c in summary["corridors"]] == ["Kent Avenue"]


def test_summary_is_none_without_data():
    assert edge_speed._speed_summary({}, {}, {}) is None


# -- config hash: the hard constraint ----------------------------------------


def test_speed_settings_are_not_in_the_processing_config(monkeypatch):
    """Speed and traversal counts must never be able to trigger a rematch."""
    before = cache._config_hash()
    for name, value in [
        ("SPEED_VERSION", 99),
        ("SPEED_CHUNK_M", 42.0),
        ("SPEED_CORRIDOR_MIN_M", 7.0),
        ("SPEED_SPLIT_PASSES", 9),
        ("TRAVERSAL_RESUME_M", 7.0),
        ("TRAVERSAL_MIN_COVER", 0.9),
    ]:
        monkeypatch.setattr(config, name, value)
    assert cache._config_hash() == before
    keys = cache._processing_config()
    assert not [k for k in keys if "speed" in k.lower() or "traversal" in k.lower()]


def test_empty_state_carries_speed_keys():
    st = cache._empty_state()
    assert st["edge_speed"] == {}
    assert st["edge_traversals"] == {}
    assert st["speed_rides"] == set()
    assert st["speed_version"] == config.SPEED_VERSION


def test_missing_csv_is_retried_next_run(tmp_path, monkeypatch):
    """A ride whose file is absent must not be marked done."""
    monkeypatch.setattr(config, "RIDES_FOLDER", str(tmp_path))
    st = _state(["gone.csv"])
    st["edge_rides"] = {EDGE: ["gone.csv"]}
    assert edge_speed._backfill_edge_speeds(st, GEOM) == 0
    assert "gone.csv" not in st["speed_rides"]


def test_ride_with_no_matched_edges_is_marked_done(tmp_path, monkeypatch):
    """Otherwise every run would re-read a ride that can never contribute."""
    name = "2024-06-19_12-00-00_-0400.csv"
    _csv(tmp_path, [(10 * i, 0, 2 * i) for i in range(11)], name)
    monkeypatch.setattr(config, "RIDES_FOLDER", str(tmp_path))
    st = _state([name])
    st["edge_rides"] = {}
    assert edge_speed._backfill_edge_speeds(st, GEOM) == 1
    assert name in st["speed_rides"]


def test_moving_time_excludes_stops(tmp_path, monkeypatch):
    """Elapsed time includes a red light; moving time does not."""
    rows = [(10 * i, 0, 2 * i) for i in range(6)]  # 50 m moving
    rows += [(50, 0, 10 + t) for t in range(1, 11)]  # stopped 10 s
    rows += [(50 + 10 * i, 0, 20 + 2 * i) for i in range(1, 6)]  # 50 m moving
    st = _run(tmp_path, rows, monkeypatch)
    chunk = st["edge_speed"][EDGE]["c"][0]
    assert chunk[edge_speed._FWD + 2] < chunk[edge_speed._FWD + 1], (
        "moving time must be under elapsed"
    )


def test_densify_is_monotonic_along_the_line():
    xy, along = edge_speed._densify([lonlat(0, 0), lonlat(100, 0), lonlat(100, 100)])
    assert len(xy) == len(along)
    assert along[0] == 0
    assert all(b >= a for a, b in zip(along, along[1:]))
    assert along[-1] == pytest.approx(200, abs=1)


def test_bearing_flipped():
    assert edge_speed._bearing_flipped(0, 180)
    assert edge_speed._bearing_flipped(350, 175)
    assert not edge_speed._bearing_flipped(0, 10)
    assert not edge_speed._bearing_flipped(350, 10)


def test_assign_ignores_fixes_beyond_snap_distance(tmp_path, monkeypatch):
    """A detour off the edge and back must not become one long traversal."""
    rows = [(0, 0, 0), (50, 0, 5), (50, 400, 10), (100, 0, 15)]
    st = _run(tmp_path, rows, monkeypatch)
    # The 400 m excursion is unassigned, so no single run spans the full edge.
    rec = st["edge_speed"].get(EDGE)
    assert rec is None or rec["c"][0][edge_speed._FWD] < 100
