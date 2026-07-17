"""Parity test: Shared_Stats_Module produces identical output regardless of backend.

Validates: Requirements 13.8

Feeds identical race fixtures through the S3Backend (moto) and a fake in-memory
backend, calling run_all_aggregations with a fixed `now`, and verifies that the
written aggregates are JSON-equivalent.
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest

from lib.aggregation import RaceRef, StorageBackend, run_all_aggregations


# ---------------------------------------------------------------------------
# In-Memory Backend
# ---------------------------------------------------------------------------


class InMemoryBackend:
    """Fake storage backend that stores everything in dicts."""

    def __init__(self):
        self.races: Dict[str, Dict[str, list]] = {}  # day_str -> list of (ref, data)
        self.aggregates: Dict[str, dict] = {}

    def add_race(self, day: date, ref: RaceRef, data: dict):
        """Add a race to the in-memory store."""
        day_str = day.isoformat()
        if day_str not in self.races:
            self.races[day_str] = []
        self.races[day_str].append((ref, data))

    def list_race_refs(self, day: date) -> List[RaceRef]:
        """List all race refs for a given day."""
        day_str = day.isoformat()
        return [ref for ref, _ in self.races.get(day_str, [])]

    def read_race(self, ref: RaceRef) -> dict:
        """Read a race by its ref key."""
        for day_entries in self.races.values():
            for r, data in day_entries:
                if r["key"] == ref["key"]:
                    return data
        raise KeyError(f"Race not found: {ref['key']}")

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        """Write aggregate to in-memory dict."""
        self.aggregates[rel_path] = data


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXED_NOW = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
FIXED_DAY = date(2025, 1, 15)


def _make_race_fixtures():
    """Create a set of realistic race fixtures for testing."""
    return [
        {
            "block_height": 878430,
            "vantage": "local",
            "prevhash": "0000000000000000000aaa01",
            "first_epoch": 1736920800.0,  # 2025-01-15T02:00:00Z
            "arrivals_offset_ms": {
                "poolA": 0.0,
                "poolB": 42.3,
                "poolC": 100.5,
            },
            "nonempty_arrivals_offset_ms": {
                "poolA": 0.0,
                "poolB": 45.0,
                "poolC": 105.0,
            },
            "empty_first_pools": ["poolC"],
            "eligible_at_start": ["poolA", "poolB", "poolC"],
            "empty_to_full_ms": {"poolC": 200.0},
            "block_miner": "Foundry USA",
        },
        {
            "block_height": 878431,
            "vantage": "local",
            "prevhash": "0000000000000000000aaa02",
            "first_epoch": 1736924400.0,  # 2025-01-15T03:00:00Z
            "arrivals_offset_ms": {
                "poolA": 15.0,
                "poolB": 0.0,
                "poolC": 80.0,
            },
            "nonempty_arrivals_offset_ms": {
                "poolA": 20.0,
                "poolB": 0.0,
                "poolC": 85.0,
            },
            "empty_first_pools": [],
            "eligible_at_start": ["poolA", "poolB", "poolC"],
            "empty_to_full_ms": {},
            "block_miner": "AntPool",
        },
        {
            "block_height": 878432,
            "vantage": "local",
            "prevhash": "0000000000000000000aaa03",
            "first_epoch": 1736928000.0,  # 2025-01-15T04:00:00Z
            "arrivals_offset_ms": {
                "poolA": 5.0,
                "poolB": 10.0,
                "poolC": 0.0,
            },
            "nonempty_arrivals_offset_ms": {
                "poolA": 8.0,
                "poolB": 12.0,
                "poolC": 0.0,
            },
            "empty_first_pools": ["poolA"],
            "eligible_at_start": ["poolA", "poolB", "poolC"],
            "empty_to_full_ms": {"poolA": 150.0},
            "block_miner": "ViaBTC",
        },
    ]


def _populate_backend(backend, races, day):
    """Populate a backend with test race data."""
    for race in races:
        height = race["block_height"]
        vantage = race["vantage"]
        key = f"api/races/{day.strftime('%Y/%m/%d')}/{height}-{vantage}.json"
        ref = RaceRef(key=key, height=height, vantage=vantage)
        backend.add_race(day, ref, race)


# ---------------------------------------------------------------------------
# Moto S3 Backend (wraps the real S3Backend from the cloud handler)
# ---------------------------------------------------------------------------


def _load_s3_backend_class():
    """Import S3Backend from lambda/aggregate/handler.py directly."""
    import importlib.util

    _handler_path = (
        Path(__file__).parent.parent.parent / "lambda" / "aggregate" / "handler.py"
    )
    spec = importlib.util.spec_from_file_location(
        "aggregate_handler", str(_handler_path)
    )
    agg_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agg_mod)
    return agg_mod.S3Backend


class MotoS3BackendWrapper:
    """Wrapper that implements the same add_race interface as InMemoryBackend."""

    def __init__(self, s3_backend, s3_client, bucket_name):
        self._backend = s3_backend
        self._s3 = s3_client
        self._bucket = bucket_name
        self._aggregates: Dict[str, dict] = {}

    def add_race(self, day: date, ref: RaceRef, data: dict):
        """Upload race JSON to moto S3."""
        self._s3.put_object(
            Bucket=self._bucket,
            Key=ref["key"],
            Body=json.dumps(data),
            ContentType="application/json",
        )

    def list_race_refs(self, day: date) -> List[RaceRef]:
        return self._backend.list_race_refs(day)

    def read_race(self, ref: RaceRef) -> dict:
        return self._backend.read_race(ref)

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        self._aggregates[rel_path] = data


# ---------------------------------------------------------------------------
# Parity Test
# ---------------------------------------------------------------------------


class TestAggregateParity:
    """Verify identical output from both backends for same input data."""

    def test_aggregate_output_is_identical_across_backends(self):
        """R13.8: Shared_Stats_Module produces identical output regardless of backend.

        Feeds the same race fixtures through an InMemoryBackend and a MotoS3Backend,
        both calling run_all_aggregations with the same fixed `now`, and verifies
        the written aggregates are JSON-equivalent.
        """
        import boto3
        from moto import mock_aws

        races = _make_race_fixtures()

        # --- In-Memory Backend ---
        mem_backend = InMemoryBackend()
        _populate_backend(mem_backend, races, FIXED_DAY)
        run_all_aggregations(mem_backend, FIXED_NOW)

        # --- Moto S3 Backend ---
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            bucket_name = "test-parity-bucket"
            s3.create_bucket(Bucket=bucket_name)

            # Import S3Backend from lambda/aggregate/handler.py directly
            S3Backend = _load_s3_backend_class()

            s3_wrapper = MotoS3BackendWrapper(
                S3Backend(bucket_name, s3), s3, bucket_name
            )
            _populate_backend(s3_wrapper, races, FIXED_DAY)
            run_all_aggregations(s3_wrapper, FIXED_NOW)

            # --- Compare outputs ---
            assert len(mem_backend.aggregates) > 0
            assert len(s3_wrapper._aggregates) > 0

            # Both backends should have produced the same set of paths
            mem_paths = set(mem_backend.aggregates.keys())
            s3_paths = set(s3_wrapper._aggregates.keys())
            assert mem_paths == s3_paths, (
                f"Path mismatch: "
                f"mem_only={mem_paths - s3_paths}, "
                f"s3_only={s3_paths - mem_paths}"
            )

            # Each aggregate should be JSON-equivalent
            for path in mem_paths:
                mem_data = mem_backend.aggregates[path]
                s3_data = s3_wrapper._aggregates[path]
                # Normalize by round-tripping through JSON
                mem_json = json.loads(json.dumps(mem_data, sort_keys=True))
                s3_json = json.loads(json.dumps(s3_data, sort_keys=True))
                assert mem_json == s3_json, (
                    f"Aggregate mismatch at {path}:\n"
                    f"Memory: {json.dumps(mem_data, indent=2)[:500]}\n"
                    f"S3:     {json.dumps(s3_data, indent=2)[:500]}"
                )
