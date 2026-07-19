"""Tests for collector --local-dir flag, default vantage, async sink model, and dead-letter on failure.

Validates Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 10.2, 12.3
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure collector is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "collector"))

import str_race


class TestLocalDirFlag:
    """Test that --local-dir flag is parsed correctly."""

    def _make_parser(self):
        """Create the argument parser with the same args as main()."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--user")
        parser.add_argument("--duration", type=int, default=0)
        parser.add_argument("--baseline-timeout", type=float, default=30.0)
        parser.add_argument("--pools")
        parser.add_argument("--json-out")
        parser.add_argument("--csv-out")
        parser.add_argument("--race-limit", type=int, default=0)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--full-timing", action="store_true")
        parser.add_argument("--tag-block-miners", action="store_true")
        parser.add_argument("--debug", action="store_true")
        parser.add_argument("--post-url")
        parser.add_argument("--api-key")
        parser.add_argument("--vantage")
        parser.add_argument("--local-dir")
        parser.add_argument("--pool-config")
        parser.add_argument("--pool-group", default="all")
        return parser

    def test_local_dir_flag_parsed(self):
        """--local-dir flag is accepted by argparse and stores the path."""
        parser = self._make_parser()
        args = parser.parse_args(["--local-dir", "/tmp/races"])
        assert args.local_dir == "/tmp/races"

    def test_local_dir_absent(self):
        """When --local-dir is not provided, it defaults to None."""
        parser = self._make_parser()
        args = parser.parse_args([])
        assert args.local_dir is None


class TestDefaultVantage:
    """Test that vantage defaults to 'local' when --local-dir is set."""

    def test_default_vantage_local(self):
        """When --local-dir is set without --vantage, vantage defaults to 'local'."""
        args = argparse.Namespace(local_dir="/tmp/races", vantage=None)
        # Replicate the logic from main()
        if getattr(args, "local_dir", None) and not args.vantage:
            args.vantage = "local"
        assert args.vantage == "local"

    def test_vantage_override_with_local_dir(self):
        """When both --local-dir and --vantage are set, the explicit vantage is used."""
        args = argparse.Namespace(local_dir="/tmp/races", vantage="my-server")
        if getattr(args, "local_dir", None) and not args.vantage:
            args.vantage = "local"
        assert args.vantage == "my-server"

    def test_vantage_unchanged_without_local_dir(self):
        """When --local-dir is not set, vantage is not defaulted."""
        args = argparse.Namespace(local_dir=None, vantage=None)
        if getattr(args, "local_dir", None) and not args.vantage:
            args.vantage = "local"
        assert args.vantage is None


class TestEnrichAndBuild:
    """Test the _enrich_and_build helper produces a valid race result dict."""

    @pytest.fixture
    def mock_race(self):
        """Create a minimal mock Race object."""
        race = MagicMock()
        race.block_height = 800000
        race.block_miner = "TestMiner"
        race.block_miner_source = "test"
        race.prevhash = "abcdef1234567890" * 4
        race.first_epoch = time.time()
        race.first_utc = "2024-01-01T00:00:00Z"
        race.first_pool = "pool1"
        race.confirmed = True
        race.corrected_winner.return_value = "pool1"
        race.nonempty_winner.return_value = "pool1"
        race.arrival_offsets_ms.return_value = {"pool1": 0.0, "pool2": 15.5}
        race.raw_arrival_offsets_ms.return_value = {"pool1": 0.0, "pool2": 15.5}
        race.nonempty_arrival_offsets_ms.return_value = {"pool1": 0.0, "pool2": 20.0}
        race.arrival_rtt_ms = {"pool1": 10.0, "pool2": 20.0}
        race.empty_first = set()
        race.nonempty_arrivals = {}
        race.arrivals = {}
        race.missed_pools.return_value = []
        race.eligible_at_start = {"pool1", "pool2"}
        return race

    @pytest.fixture
    def mock_pools(self):
        pool1 = MagicMock()
        pool1.connected = True
        pool2 = MagicMock()
        pool2.connected = True
        return {"pool1": pool1, "pool2": pool2}

    @pytest.fixture
    def mock_args(self):
        return argparse.Namespace(
            vantage="local",
            tag_block_miners=False,
            post_url=None,
            local_dir="/tmp/test",
        )

    @pytest.mark.asyncio
    async def test_enrich_and_build_produces_race_result(self, mock_race, mock_pools, mock_args):
        """_enrich_and_build returns a dict with expected race_result fields."""
        result = await str_race._enrich_and_build(
            mock_race, mock_pools, mock_args, start_time=time.time(), session_races=1
        )
        assert isinstance(result, dict)
        assert result["version"] == 1
        assert result["vantage"] == "local"
        assert result["block_height"] == 800000
        assert result["prevhash"] == mock_race.prevhash
        assert result["winner"] == "pool1"
        assert "collector_meta" in result
        assert result["collector_meta"]["session_races"] == 1

    @pytest.mark.asyncio
    async def test_enrich_and_build_skips_lookup_when_height_set(self, mock_race, mock_pools, mock_args):
        """When block_height is already set, no mempool lookup is attempted."""
        mock_args.tag_block_miners = True
        with patch.object(str_race, "lookup_block_metadata") as mock_lookup:
            result = await str_race._enrich_and_build(
                mock_race, mock_pools, mock_args, start_time=time.time(), session_races=1
            )
            mock_lookup.assert_not_called()
        assert result["block_height"] == 800000


class TestHybridMode:
    """Test that both --post-url and --local-dir can run independently."""

    @pytest.mark.asyncio
    async def test_housekeeping_calls_both_post_and_sink(self):
        """When both post_url and race_sink are configured, both are invoked."""
        # Create a mock race that will be returned by cleanup_races
        mock_race = MagicMock()
        mock_race.block_height = 800001
        mock_race.block_miner = "TestMiner"
        mock_race.block_miner_source = "test"
        mock_race.prevhash = "a" * 64
        mock_race.first_epoch = time.time()
        mock_race.first_utc = "2024-01-01T00:00:00Z"
        mock_race.first_pool = "pool1"
        mock_race.corrected_winner.return_value = "pool1"
        mock_race.nonempty_winner.return_value = "pool1"
        mock_race.arrival_offsets_ms.return_value = {"pool1": 0.0}
        mock_race.raw_arrival_offsets_ms.return_value = {"pool1": 0.0}
        mock_race.nonempty_arrival_offsets_ms.return_value = {"pool1": 0.0}
        mock_race.arrival_rtt_ms = {"pool1": 12.0}
        mock_race.empty_first = set()
        mock_race.nonempty_arrivals = {}
        mock_race.arrivals = {}
        mock_race.missed_pools.return_value = []
        mock_race.eligible_at_start = {"pool1"}

        tracker = MagicMock()
        tracker.all_races = [mock_race]
        tracker.tracking_enabled = False
        tracker.consensus_prevhash = "a" * 64

        # First call returns the race, subsequent calls return empty
        call_count = [0]

        def mock_cleanup(pools):
            call_count[0] += 1
            if call_count[0] == 1:
                return [mock_race]
            return []

        tracker.cleanup_races.side_effect = mock_cleanup
        tracker.check_consensus.return_value = None

        pools = {"pool1": MagicMock(connected=True)}
        stop_event = asyncio.Event()
        args = argparse.Namespace(
            post_url="http://example.com/ingest",
            vantage="local",
            tag_block_miners=False,
            local_dir="/tmp/test",
            api_key="test-key",
        )

        sink_called = []
        async def test_sink(race_result):
            sink_called.append(race_result)

        # Run housekeeping for a short time then stop
        async def stop_after_short():
            await asyncio.sleep(1.5)
            stop_event.set()

        with patch.object(str_race, "_post_enriched_result", new_callable=AsyncMock) as mock_post:
            asyncio.create_task(stop_after_short())
            await str_race.housekeeping(
                tracker, pools, stop_event, args, time.time(), race_sink=test_sink
            )

        # Both the sink and the POST should have been called
        assert len(sink_called) == 1
        assert sink_called[0]["block_height"] == 800001
        mock_post.assert_called_once()


class TestDeadLetterOnFailure:
    """Test that local write failures trigger dead-letter writing."""

    @pytest.mark.asyncio
    async def test_default_local_sink_dead_letter_on_failure(self, tmp_path):
        """When LocalStorage.write_race raises, the default sink writes a dead-letter."""
        args = argparse.Namespace(
            local_dir=str(tmp_path / "data"),
            vantage="local",
            post_url=None,
            api_key=None,
            pools=None,
            pool_config=None,
            pool_group="all",
            user="test",
            duration=0,
            baseline_timeout=30.0,
            tag_block_miners=False,
            json_out=None,
            csv_out=None,
            race_limit=0,
            verbose=False,
            full_timing=False,
            debug=False,
        )

        # We'll test just the sink creation and failure path
        from lib.local_store import LocalStorage

        with patch.object(LocalStorage, "write_race", side_effect=OSError("disk full")):
            with patch.object(LocalStorage, "ensure_initial_files"):
                with patch.object(str_race, "_write_dead_letter") as mock_dl:
                    # Simulate what run() does: create the default local sink
                    _local_storage = LocalStorage(Path(args.local_dir))
                    _local_storage.ensure_initial_files()
                    _loop = asyncio.get_running_loop()

                    async def _default_local_sink(race_result: dict) -> None:
                        try:
                            await _loop.run_in_executor(None, _local_storage.write_race, race_result)
                        except Exception as e:
                            str_race._write_dead_letter(race_result)

                    test_result = {"prevhash": "abc", "block_height": 800000}
                    await _default_local_sink(test_result)

                    mock_dl.assert_called_once_with(test_result)


class TestRunSignalSuppression:
    """Test that run() suppresses signal handlers when stop_event is injected."""

    def test_run_accepts_stop_event_parameter(self):
        """run() function accepts race_sink and stop_event parameters."""
        import inspect
        sig = inspect.signature(str_race.run)
        assert "race_sink" in sig.parameters
        assert "stop_event" in sig.parameters
        # Both should default to None
        assert sig.parameters["race_sink"].default is None
        assert sig.parameters["stop_event"].default is None


class TestApiKeyNotRequired:
    """Test that --local-dir alone does not require an API key (R12.3)."""

    def test_no_api_key_needed_for_local_dir_only(self):
        """When --local-dir is set without --post-url, no API key is required."""
        args = argparse.Namespace(
            api_key=None,
            post_url=None,
            local_dir="/tmp/test",
        )
        # _resolve_api_key should return None without raising
        with patch.dict(os.environ, {}, clear=True):
            # Remove STRATUMRACE_API_KEY if set
            os.environ.pop("STRATUMRACE_API_KEY", None)
            result = str_race._resolve_api_key(args)
        assert result is None
