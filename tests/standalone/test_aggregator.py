"""Tests for standalone/aggregator.py.

Validates:
- run_all_aggregations produces the expected 6 aggregate file types
- Empty periods produce valid zero-aggregate files
- The aggregator loop runs the first cycle within 30s of startup
- The loop survives exceptions without dying
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.aggregation import run_all_aggregations
from lib.local_store import LocalStorage
from standalone.aggregator import STARTUP_DELAY_S, aggregator_loop


def _make_race_result(height: int, vantage: str = "local", epoch: float = None) -> dict:
    """Create a minimal valid race result for testing."""
    if epoch is None:
        epoch = 1700000000.0 + height
    return {
        "version": "2.0",
        "vantage": vantage,
        "block_height": height,
        "prevhash": f"000000000000000000{height:010d}",
        "first_epoch": epoch,
        "winner": "pool-a",
        "winner_nonempty": "pool-a",
        "block_miner": "pool-a",
        "arrivals_offset_ms": {"pool-a": 0.0, "pool-b": 150.5},
        "nonempty_arrivals_offset_ms": {"pool-a": 0.0, "pool-b": 200.3},
        "empty_first_pools": [],
        "empty_to_full_ms": {},
        "missed_pools": [],
        "eligible_at_start": ["pool-a", "pool-b"],
        "pools_connected": 2,
        "pools_eligible": 2,
        "collector_meta": {},
    }


@pytest.fixture
def storage(tmp_path):
    """Create a LocalStorage instance with initial files."""
    store = LocalStorage(tmp_path / "data")
    store.ensure_initial_files()
    return store


class TestRunAllAggregationsProducesExpectedFiles:
    """Test that run_all_aggregations produces all 6 aggregate file types."""

    def test_aggregator_produces_expected_files(self, storage, tmp_path):
        """With race data present, all 6 aggregate file types are produced."""
        # Write some race files
        now = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
        epoch = now.timestamp()

        for i in range(3):
            race = _make_race_result(height=800000 + i, epoch=epoch + i * 60)
            storage.write_race(race)

        # Run aggregation
        run_all_aggregations(storage, now)

        api_dir = storage.api_dir

        # Check daily aggregate
        daily_path = api_dir / "aggregates" / "daily" / "2024-03-15.json"
        assert daily_path.exists()
        daily = json.loads(daily_path.read_text())
        assert daily["total_races"] == 3
        assert "pool-a" in daily["pools"]
        assert "pool-b" in daily["pools"]

        # Check monthly aggregate
        monthly_path = api_dir / "aggregates" / "monthly" / "2024-03.json"
        assert monthly_path.exists()
        monthly = json.loads(monthly_path.read_text())
        assert monthly["total_races"] == 3

        # Check recent-10
        recent10_path = api_dir / "aggregates" / "recent-10.json"
        assert recent10_path.exists()
        recent10 = json.loads(recent10_path.read_text())
        assert recent10["total_races"] == 3

        # Check recent-50
        recent50_path = api_dir / "aggregates" / "recent-50.json"
        assert recent50_path.exists()
        recent50 = json.loads(recent50_path.read_text())
        assert recent50["total_races"] == 3

        # Check last-24h
        last24h_path = api_dir / "aggregates" / "last-24h.json"
        assert last24h_path.exists()
        last24h = json.loads(last24h_path.read_text())
        assert last24h["total_races"] == 3

        # Check last-7d
        last7d_path = api_dir / "aggregates" / "last-7d.json"
        assert last7d_path.exists()
        last7d = json.loads(last7d_path.read_text())
        assert last7d["total_races"] == 3

    def test_empty_period_zero_aggregates(self, storage, tmp_path):
        """With no race data, aggregates are valid files with total_races: 0."""
        now = datetime(2024, 3, 15, 12, 0, 0, tzinfo=timezone.utc)

        # Run aggregation with no race data
        run_all_aggregations(storage, now)

        api_dir = storage.api_dir

        # Daily aggregate should have total_races = 0
        daily_path = api_dir / "aggregates" / "daily" / "2024-03-15.json"
        assert daily_path.exists()
        daily = json.loads(daily_path.read_text())
        assert daily["total_races"] == 0
        assert daily["pools"] == {}

        # Monthly aggregate should have total_races = 0
        monthly_path = api_dir / "aggregates" / "monthly" / "2024-03.json"
        assert monthly_path.exists()
        monthly = json.loads(monthly_path.read_text())
        assert monthly["total_races"] == 0
        assert monthly["pools"] == {}

        # Recent-10 should have total_races = 0
        recent10_path = api_dir / "aggregates" / "recent-10.json"
        assert recent10_path.exists()
        recent10 = json.loads(recent10_path.read_text())
        assert recent10["total_races"] == 0

        # Recent-50 should have total_races = 0
        recent50_path = api_dir / "aggregates" / "recent-50.json"
        assert recent50_path.exists()
        recent50 = json.loads(recent50_path.read_text())
        assert recent50["total_races"] == 0

        # Last-24h should have total_races = 0
        last24h_path = api_dir / "aggregates" / "last-24h.json"
        assert last24h_path.exists()
        last24h = json.loads(last24h_path.read_text())
        assert last24h["total_races"] == 0

        # Last-7d should have total_races = 0
        last7d_path = api_dir / "aggregates" / "last-7d.json"
        assert last7d_path.exists()
        last7d = json.loads(last7d_path.read_text())
        assert last7d["total_races"] == 0


class TestAggregatorLoop:
    """Test the async aggregator_loop behavior."""

    @pytest.mark.asyncio
    async def test_aggregator_loop_first_cycle_within_30s(self, storage):
        """The loop runs its first aggregation cycle within 30s of startup."""
        stop = asyncio.Event()
        cycle_times = []

        original_run = run_all_aggregations

        def track_run(*args, **kwargs):
            cycle_times.append(time.monotonic())
            original_run(*args, **kwargs)

        start = time.monotonic()

        with patch(
            "standalone.aggregator.run_all_aggregations", side_effect=track_run
        ):
            task = asyncio.create_task(aggregator_loop(storage, stop))
            # Wait enough time for the startup delay + first cycle
            await asyncio.sleep(STARTUP_DELAY_S + 1)
            stop.set()
            await task

        # First cycle should have run
        assert len(cycle_times) >= 1
        first_cycle_elapsed = cycle_times[0] - start
        # Must be within 30s of startup (requirement R5.1)
        assert first_cycle_elapsed < 30.0

    @pytest.mark.asyncio
    async def test_aggregator_loop_survives_exception(self, storage):
        """The loop continues after run_all_aggregations raises an exception."""
        stop = asyncio.Event()
        call_count = []

        def failing_run(*args, **kwargs):
            call_count.append(1)
            if len(call_count) == 1:
                raise RuntimeError("Simulated aggregation failure")
            # Second call succeeds (no-op)

        with patch(
            "standalone.aggregator.run_all_aggregations", side_effect=failing_run
        ):
            # Use a very short cycle interval for testing
            with patch("standalone.aggregator.CYCLE_INTERVAL_S", 0.1):
                with patch("standalone.aggregator.STARTUP_DELAY_S", 0.1):
                    task = asyncio.create_task(aggregator_loop(storage, stop))
                    # Wait for at least 2 cycles
                    await asyncio.sleep(0.5)
                    stop.set()
                    await task

        # Should have been called at least twice (first fails, second succeeds)
        assert len(call_count) >= 2

    @pytest.mark.asyncio
    async def test_aggregator_loop_stops_on_signal(self, storage):
        """The loop exits cleanly when stop event is set."""
        stop = asyncio.Event()

        with patch("standalone.aggregator.STARTUP_DELAY_S", 0.1):
            task = asyncio.create_task(aggregator_loop(storage, stop))
            # Set stop during the initial delay
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, timeout=2.0)

        # Task should have completed without error
        assert task.done()
        assert task.exception() is None
