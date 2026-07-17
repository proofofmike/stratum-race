#!/usr/bin/env python3
"""Test suite for state persistence functions (save_state, load_state)."""

import sys
import os
import json
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "collector")

from str_race import (
    save_state, load_state, _get_state_path,
    STATE_FILE_DEFAULT, STATE_VERSION,
    RaceTracker, PoolState, Race,
)


def test_get_state_path_default():
    """Default path is ~/.str_race_state.json"""
    # Ensure env is clean
    os.environ.pop("STR_RACE_STATE_FILE", None)
    assert _get_state_path() == STATE_FILE_DEFAULT
    print("PASS: _get_state_path default")


def test_get_state_path_env_override():
    """STR_RACE_STATE_FILE env var overrides default path"""
    os.environ["STR_RACE_STATE_FILE"] = "/tmp/custom_state.json"
    assert _get_state_path() == Path("/tmp/custom_state.json")
    del os.environ["STR_RACE_STATE_FILE"]
    print("PASS: _get_state_path env override")


def test_load_state_missing_file():
    """load_state returns None for non-existent file"""
    assert load_state(Path("/tmp/does_not_exist_xyz_12345.json")) is None
    print("PASS: load_state missing file")


def test_load_state_corrupted_json():
    """load_state returns None for corrupted JSON"""
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text("{invalid json!!!}")
    assert load_state(tmp) is None
    tmp.unlink()
    print("PASS: load_state corrupted JSON")


def test_load_state_missing_fields():
    """load_state returns None when required fields are missing"""
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps({"version": 1}))
    assert load_state(tmp) is None
    tmp.unlink()
    print("PASS: load_state missing fields")


def test_load_state_valid():
    """load_state returns state dict for valid file"""
    valid = {
        "version": 1,
        "saved_utc": "2025-01-15T12:00:00Z",
        "consensus_prevhash": "0000000000000abc",
        "connected_pools": ["pool_a", "pool_b"],
        "session_races": 10,
        "uptime_at_save": 1800,
    }
    tmp = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps(valid))
    result = load_state(tmp)
    assert result == valid
    tmp.unlink()
    print("PASS: load_state valid state")


def test_save_state_atomic_write():
    """save_state writes atomically (no .tmp left behind)"""
    tracker = RaceTracker()
    tracker.consensus_prevhash = "abc123"
    pools = {
        "p1": PoolState(name="p1", host="h1", port=3333, user="u"),
        "p2": PoolState(name="p2", host="h2", port=3333, user="u"),
    }
    pools["p1"].connected = True
    pools["p2"].connected = True

    state_path = Path(tempfile.mktemp(suffix=".json"))
    save_state(pools, tracker, state_path=state_path, start_time=time.monotonic() - 100)

    assert state_path.exists()
    assert not state_path.with_suffix(".tmp").exists()

    data = json.loads(state_path.read_text())
    assert data["version"] == STATE_VERSION
    assert data["consensus_prevhash"] == "abc123"
    assert data["connected_pools"] == ["p1", "p2"]
    assert data["session_races"] == 0
    assert data["uptime_at_save"] >= 99

    state_path.unlink()
    print("PASS: save_state atomic write")


def test_save_state_load_state_roundtrip():
    """save then load produces equivalent state"""
    tracker = RaceTracker()
    tracker.consensus_prevhash = "deadbeef"
    race = Race(
        index=1, prevhash="deadbeef", first_pool="p1",
        first_ts=100.0, first_wall="wall", first_epoch=100.0,
        first_utc="utc", eligible_at_start={"p1", "p2"},
    )
    race.confirmed = True
    tracker.all_races.append(race)
    tracker.all_races.append(race)  # 2 races

    pools = {
        "p1": PoolState(name="p1", host="h1", port=3333, user="u"),
        "p2": PoolState(name="p2", host="h2", port=3333, user="u"),
    }
    pools["p1"].connected = True

    state_path = Path(tempfile.mktemp(suffix=".json"))
    save_state(pools, tracker, state_path=state_path, start_time=time.monotonic() - 50)

    loaded = load_state(state_path)
    assert loaded is not None
    assert loaded["consensus_prevhash"] == "deadbeef"
    assert loaded["session_races"] == 2
    assert loaded["connected_pools"] == ["p1"]

    state_path.unlink()
    print("PASS: save_state + load_state round-trip")


def test_save_state_no_start_time():
    """save_state with no start_time sets uptime to 0"""
    tracker = RaceTracker()
    pools = {}
    state_path = Path(tempfile.mktemp(suffix=".json"))
    save_state(pools, tracker, state_path=state_path, start_time=None)
    data = json.loads(state_path.read_text())
    assert data["uptime_at_save"] == 0
    state_path.unlink()
    print("PASS: save_state no start_time")


def test_save_state_empty_pools():
    """save_state with empty pools dict produces empty connected_pools"""
    tracker = RaceTracker()
    state_path = Path(tempfile.mktemp(suffix=".json"))
    save_state({}, tracker, state_path=state_path)
    data = json.loads(state_path.read_text())
    assert data["connected_pools"] == []
    state_path.unlink()
    print("PASS: save_state empty pools")


def test_state_file_schema():
    """Verify the state file matches the expected schema from the design"""
    tracker = RaceTracker()
    tracker.consensus_prevhash = "00000000000000000002a8f1"
    pools = {
        "atlaspool": PoolState(name="atlaspool", host="solo.atlaspool.io", port=3333, user="u"),
        "ckpool": PoolState(name="ckpool", host="solo.ckpool.org", port=3333, user="u"),
    }
    pools["atlaspool"].connected = True
    pools["ckpool"].connected = True

    # Add some races
    for i in range(42):
        race = Race(
            index=i + 1, prevhash=f"hash{i}", first_pool="atlaspool",
            first_ts=100.0 + i, first_wall="wall", first_epoch=100.0 + i,
            first_utc="utc", eligible_at_start={"atlaspool", "ckpool"},
        )
        tracker.all_races.append(race)

    state_path = Path(tempfile.mktemp(suffix=".json"))
    save_state(pools, tracker, state_path=state_path, start_time=time.monotonic() - 3600)

    data = json.loads(state_path.read_text())

    # Check all required schema fields exist
    assert "version" in data and data["version"] == 1
    assert "saved_utc" in data and "T" in data["saved_utc"]  # ISO format
    assert "consensus_prevhash" in data
    assert "connected_pools" in data and isinstance(data["connected_pools"], list)
    assert "session_races" in data and data["session_races"] == 42
    assert "uptime_at_save" in data and data["uptime_at_save"] >= 3599

    state_path.unlink()
    print("PASS: state file schema matches design")


if __name__ == "__main__":
    test_get_state_path_default()
    test_get_state_path_env_override()
    test_load_state_missing_file()
    test_load_state_corrupted_json()
    test_load_state_missing_fields()
    test_load_state_valid()
    test_save_state_atomic_write()
    test_save_state_load_state_roundtrip()
    test_save_state_no_start_time()
    test_save_state_empty_pools()
    test_state_file_schema()
    print("\nAll 11 tests passed!")
