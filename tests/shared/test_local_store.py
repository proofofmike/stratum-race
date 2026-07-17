"""Tests for lib/local_store.py (LocalStorage).

Covers: atomic visibility, dedup + cap 55 + newest-first ordering,
cold-start file creation, StorageBackend protocol methods.
"""

import json
import os
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.local_store import LocalStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_race_result(
    height: int = 800000,
    vantage: str = "local",
    epoch: float = 1700000000.0,
    pools: dict | None = None,
) -> dict:
    """Build a minimal race_result dict for testing."""
    if pools is None:
        pools = {"pool_a": 0.0, "pool_b": 50.5}
    return {
        "block_height": height,
        "vantage": vantage,
        "first_epoch": epoch,
        "block_miner": "TestMiner",
        "arrivals_offset_ms": pools,
        "nonempty_arrivals_offset_ms": pools,
        "empty_first_pools": [],
        "winner_nonempty": "pool_a",
        "eligible_at_start": list(pools.keys()),
        "pools_connected": len(pools),
        "pools_eligible": len(pools),
    }


# ---------------------------------------------------------------------------
# ensure_initial_files tests
# ---------------------------------------------------------------------------


class TestEnsureInitialFiles:
    def test_creates_directory_structure(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        assert (tmp_path / "api" / "races").is_dir()
        assert (tmp_path / "api" / "recent").is_dir()
        assert (tmp_path / "api" / "aggregates" / "daily").is_dir()
        assert (tmp_path / "api" / "aggregates" / "monthly").is_dir()
        assert (tmp_path / "api" / "config").is_dir()

    def test_creates_empty_recent_blocks(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        recent_path = tmp_path / "api" / "recent" / "recent-blocks.json"
        assert recent_path.exists()
        data = json.loads(recent_path.read_text())
        assert data == []

    def test_creates_latest_with_null(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        latest_path = tmp_path / "api" / "latest.json"
        assert latest_path.exists()
        data = json.loads(latest_path.read_text())
        assert data == {"height": None, "epoch": None}

    def test_creates_empty_aggregates(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        for name in ["recent-10.json", "recent-50.json", "last-24h.json", "last-7d.json"]:
            agg_path = tmp_path / "api" / "aggregates" / name
            assert agg_path.exists(), f"{name} not created"
            data = json.loads(agg_path.read_text())
            assert data["total_races"] == 0
            assert data["pools"] == {}

    def test_idempotent_does_not_overwrite(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write custom data to recent-blocks
        recent_path = tmp_path / "api" / "recent" / "recent-blocks.json"
        custom_data = [{"height": 1, "vantage": "test"}]
        recent_path.write_text(json.dumps(custom_data))

        # Call again — should NOT overwrite
        storage.ensure_initial_files()
        data = json.loads(recent_path.read_text())
        assert data == custom_data


# ---------------------------------------------------------------------------
# write_race tests
# ---------------------------------------------------------------------------


class TestWriteRace:
    def test_creates_correct_path(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # epoch 1700000000 => 2023-11-14 UTC
        race = _make_race_result(height=800001, vantage="local", epoch=1700000000.0)
        storage.write_race(race)

        expected_path = tmp_path / "api" / "races" / "2023" / "11" / "14" / "800001-local.json"
        assert expected_path.exists()
        data = json.loads(expected_path.read_text())
        assert data["block_height"] == 800001

    def test_unknown_height_path(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        race = _make_race_result(height=None, epoch=1700000000.0)
        # Manually set to None
        race["block_height"] = None
        storage.write_race(race)

        # Null-height races include the epoch in the filename so two unknown
        # blocks on the same day never overwrite each other
        expected_path = tmp_path / "api" / "races" / "2023" / "11" / "14" / "unknown-1700000000-local.json"
        assert expected_path.exists()

    def test_two_unknown_height_races_do_not_overwrite(self, tmp_path):
        """Two null-height races on the same day must produce two distinct files
        and two distinct recent-blocks entries."""
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        race1 = _make_race_result(height=None, epoch=1700000000.0)
        race1["block_height"] = None
        race2 = _make_race_result(height=None, epoch=1700000600.0)
        race2["block_height"] = None

        storage.write_race(race1)
        storage.write_race(race2)

        day_dir = tmp_path / "api" / "races" / "2023" / "11" / "14"
        assert (day_dir / "unknown-1700000000-local.json").exists()
        assert (day_dir / "unknown-1700000600-local.json").exists()

        recent = json.loads(
            (tmp_path / "api" / "recent" / "recent-blocks.json").read_text()
        )
        null_entries = [e for e in recent if e["height"] is None]
        assert len(null_entries) == 2

    def test_unknown_height_repost_dedupes_by_epoch(self, tmp_path):
        """Re-posting the SAME null-height race (same epoch) replaces its
        recent-blocks entry instead of duplicating it."""
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        race = _make_race_result(height=None, epoch=1700000000.0)
        race["block_height"] = None
        storage.write_race(race)
        storage.write_race(race)

        recent = json.loads(
            (tmp_path / "api" / "recent" / "recent-blocks.json").read_text()
        )
        null_entries = [e for e in recent if e["height"] is None]
        assert len(null_entries) == 1

    def test_updates_recent_blocks_dedup_cap_newest_first(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write 60 distinct races to exceed cap of 55
        for i in range(60):
            race = _make_race_result(
                height=800000 + i, epoch=1700000000.0 + i * 600
            )
            storage.write_race(race)

        recent_path = tmp_path / "api" / "recent" / "recent-blocks.json"
        data = json.loads(recent_path.read_text())

        # Capped at 55
        assert len(data) == 55

        # Newest first (last written is at index 0)
        assert data[0]["height"] == 800059

        # Oldest retained is 800005 (60 - 55 = 5 dropped)
        assert data[-1]["height"] == 800005

    def test_dedup_by_height_and_vantage(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write same height+vantage twice
        race1 = _make_race_result(height=800000, vantage="local", epoch=1700000000.0)
        storage.write_race(race1)

        race2 = _make_race_result(height=800000, vantage="local", epoch=1700000001.0)
        storage.write_race(race2)

        recent_path = tmp_path / "api" / "recent" / "recent-blocks.json"
        data = json.loads(recent_path.read_text())

        # Only one entry for height 800000 + vantage "local"
        matching = [e for e in data if e["height"] == 800000 and e["vantage"] == "local"]
        assert len(matching) == 1

        # Should be the newer one (epoch from race2)
        assert matching[0]["epoch"] == 1700000001.0

    def test_updates_latest(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        race = _make_race_result(height=800123, epoch=1700001000.0)
        storage.write_race(race)

        latest_path = tmp_path / "api" / "latest.json"
        data = json.loads(latest_path.read_text())
        assert data["height"] == 800123
        assert data["epoch"] == 1700001000.0

    def test_atomic_write_no_partial_file(self, tmp_path):
        """Verify that the race file is either fully written or not present."""
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        race = _make_race_result(height=800000, epoch=1700000000.0)
        storage.write_race(race)

        race_path = tmp_path / "api" / "races" / "2023" / "11" / "14" / "800000-local.json"
        # File must exist and be valid JSON
        assert race_path.exists()
        data = json.loads(race_path.read_text())
        assert data["block_height"] == 800000

        # No temp files left behind
        tmp_files = list(race_path.parent.glob("*.tmp"))
        assert tmp_files == []

    def test_preserves_recent_on_failure(self, tmp_path):
        """If race file write fails, recent-blocks should be unchanged."""
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write an initial race
        race1 = _make_race_result(height=800000, epoch=1700000000.0)
        storage.write_race(race1)

        recent_path = tmp_path / "api" / "recent" / "recent-blocks.json"
        original_data = json.loads(recent_path.read_text())

        # Make the race file directory unwritable to force failure on _atomic_write
        # We'll patch _atomic_write to raise on the first call (race file)
        # but allow subsequent calls (recent-blocks, latest)
        call_count = {"n": 0}
        original_atomic = storage._atomic_write

        def failing_atomic(path, data):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("Simulated disk full")
            return original_atomic(path, data)

        with patch.object(storage, "_atomic_write", side_effect=failing_atomic):
            with pytest.raises(OSError):
                race2 = _make_race_result(height=800001, epoch=1700000600.0)
                storage.write_race(race2)

        # recent-blocks should be unchanged (race write failed before
        # we got to the recent-blocks update)
        data_after = json.loads(recent_path.read_text())
        assert data_after == original_data


# ---------------------------------------------------------------------------
# StorageBackend protocol tests
# ---------------------------------------------------------------------------


class TestStorageBackend:
    def test_list_race_refs_returns_correct_refs(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write some races for 2023-11-14
        for h in [800001, 800002, 800003]:
            race = _make_race_result(height=h, epoch=1700000000.0 + (h - 800001) * 600)
            storage.write_race(race)

        refs = storage.list_race_refs(date(2023, 11, 14))
        assert len(refs) == 3

        heights = sorted(r["height"] for r in refs)
        assert heights == [800001, 800002, 800003]
        assert all(r["vantage"] == "local" for r in refs)

    def test_list_race_refs_empty_day(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        refs = storage.list_race_refs(date(2020, 1, 1))
        assert refs == []

    def test_list_race_refs_skips_unknown_height(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write a race with unknown height
        race = _make_race_result(epoch=1700000000.0)
        race["block_height"] = None
        storage.write_race(race)

        # Also write one with known height
        race2 = _make_race_result(height=800001, epoch=1700000600.0)
        storage.write_race(race2)

        refs = storage.list_race_refs(date(2023, 11, 14))
        # Only the known-height race should appear
        assert len(refs) == 1
        assert refs[0]["height"] == 800001

    def test_read_race_returns_parsed_json(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        race = _make_race_result(height=800005, epoch=1700000000.0)
        storage.write_race(race)

        refs = storage.list_race_refs(date(2023, 11, 14))
        assert len(refs) == 1

        read_data = storage.read_race(refs[0])
        assert read_data["block_height"] == 800005
        assert read_data["vantage"] == "local"

    def test_write_aggregate_atomic(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        agg_data = {
            "date": "2023-11-14",
            "generated_utc": "2023-11-14T12:00:00Z",
            "total_races": 5,
            "pools": {"pool_a": {"combined": {}}},
        }
        storage.write_aggregate("aggregates/daily/2023-11-14.json", agg_data)

        written_path = tmp_path / "api" / "aggregates" / "daily" / "2023-11-14.json"
        assert written_path.exists()
        data = json.loads(written_path.read_text())
        assert data["total_races"] == 5

        # No temp files left
        tmp_files = list(written_path.parent.glob("*.tmp"))
        assert tmp_files == []
