"""Tests for the finalized-day bundle optimization.

Finalized (immutable) days are consolidated into one bundle object so each
aggregation run costs one backend read per past day instead of one per race
file (~97% fewer S3 GETs — the dominant AWS cost of the platform).

Covers: bundle write on first load of a finalized day, bundle read on
subsequent loads, no bundling for today or for yesterday during the boundary
grace window, fallback on invalid bundles, backends without bundle support,
and the LocalStorage bundle implementation.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.aggregation import RaceRef, _DayCache, _is_day_finalized
from lib.local_store import LocalStorage

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
GRACE_NOW = datetime(2026, 7, 16, 0, 5, tzinfo=timezone.utc)


def _make_race(height, vantage="us-east-1", epoch=1789000000.0):
    return {
        "block_height": height,
        "vantage": vantage,
        "first_epoch": epoch,
        "arrivals_offset_ms": {"atlaspool": 0.0},
        "nonempty_arrivals_offset_ms": {"atlaspool": 0.0},
    }


class BundleBackend:
    """In-memory backend WITH bundle support, instrumented with counters."""

    def __init__(self):
        self.races_by_day = {}
        self.bundles = {}
        self.race_reads = 0
        self.bundle_reads = 0
        self.bundle_writes = 0

    def add_race(self, day: date, race: dict):
        self.races_by_day.setdefault(day, []).append(race)

    def list_race_refs(self, day: date) -> List[RaceRef]:
        return [
            RaceRef(key=f"{day}/{i}", height=r["block_height"], vantage=r["vantage"])
            for i, r in enumerate(self.races_by_day.get(day, []))
        ]

    def read_race(self, ref: RaceRef) -> dict:
        self.race_reads += 1
        day_str, idx = ref["key"].split("/")
        return self.races_by_day[date.fromisoformat(day_str)][int(idx)]

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        pass

    def read_day_bundle(self, day: date) -> Optional[List[dict]]:
        self.bundle_reads += 1
        return self.bundles.get(day)

    def write_day_bundle(self, day: date, races: List[dict]) -> None:
        self.bundle_writes += 1
        self.bundles[day] = list(races)


class NoBundleBackend:
    """In-memory backend WITHOUT bundle support (protocol minimum)."""

    def __init__(self):
        self.races_by_day = {}
        self.race_reads = 0

    def list_race_refs(self, day: date) -> List[RaceRef]:
        return [
            RaceRef(key=f"{day}/{i}", height=r["block_height"], vantage=r["vantage"])
            for i, r in enumerate(self.races_by_day.get(day, []))
        ]

    def read_race(self, ref: RaceRef) -> dict:
        self.race_reads += 1
        day_str, idx = ref["key"].split("/")
        return self.races_by_day[date.fromisoformat(day_str)][int(idx)]

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        pass


class TestIsDayFinalized:
    def test_today_never_finalized(self):
        assert not _is_day_finalized(NOW.date(), NOW)

    def test_yesterday_finalized_after_grace(self):
        assert _is_day_finalized(NOW.date() - timedelta(days=1), NOW)

    def test_yesterday_not_finalized_during_grace(self):
        assert not _is_day_finalized(GRACE_NOW.date() - timedelta(days=1), GRACE_NOW)

    def test_older_day_finalized_even_during_grace(self):
        assert _is_day_finalized(GRACE_NOW.date() - timedelta(days=2), GRACE_NOW)


class TestDayBundleLifecycle:
    def test_finalized_day_writes_bundle_on_first_load(self):
        backend = BundleBackend()
        past = NOW.date() - timedelta(days=3)
        backend.add_race(past, _make_race(905000))
        backend.add_race(past, _make_race(905000, vantage="eu-central-1"))

        cache = _DayCache(backend, NOW)
        races = cache.get_races(past)

        assert len(races) == 2
        assert backend.race_reads == 2  # Per-file this first time
        assert backend.bundle_writes == 1
        assert past in backend.bundles

    def test_subsequent_invocation_uses_bundle_not_files(self):
        backend = BundleBackend()
        past = NOW.date() - timedelta(days=3)
        backend.add_race(past, _make_race(905000))
        backend.bundles[past] = [_make_race(905000)]  # Pre-existing bundle

        cache = _DayCache(backend, NOW)  # Fresh invocation
        races = cache.get_races(past)

        assert len(races) == 1
        assert backend.race_reads == 0  # No per-file reads
        assert backend.bundle_reads == 1

    def test_today_is_never_bundled(self):
        backend = BundleBackend()
        today = NOW.date()
        backend.add_race(today, _make_race(905001))

        cache = _DayCache(backend, NOW)
        races = cache.get_races(today)

        assert len(races) == 1
        assert backend.race_reads == 1
        assert backend.bundle_reads == 0
        assert backend.bundle_writes == 0

    def test_yesterday_not_bundled_during_grace_window(self):
        """Late races can still arrive during the grace window — bundling
        yesterday too early would freeze an incomplete day forever."""
        backend = BundleBackend()
        yesterday = GRACE_NOW.date() - timedelta(days=1)
        backend.add_race(yesterday, _make_race(905002))

        cache = _DayCache(backend, GRACE_NOW)
        cache.get_races(yesterday)

        assert backend.bundle_writes == 0

    def test_invalid_bundle_falls_back_to_files(self):
        backend = BundleBackend()
        past = NOW.date() - timedelta(days=3)
        backend.add_race(past, _make_race(905003))
        backend.bundles[past] = "not-a-list"  # type: ignore[assignment]

        cache = _DayCache(backend, NOW)
        races = cache.get_races(past)

        assert len(races) == 1
        assert backend.race_reads == 1  # Fell back to per-file

    def test_backend_without_bundle_support_still_works(self):
        backend = NoBundleBackend()
        past = NOW.date() - timedelta(days=3)
        backend.races_by_day[past] = [_make_race(905004)]

        cache = _DayCache(backend, NOW)
        races = cache.get_races(past)

        assert len(races) == 1
        assert backend.race_reads == 1


class TestLocalStorageBundles:
    def test_bundle_round_trip(self, tmp_path):
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()
        day = date(2026, 7, 10)
        races = [_make_race(905005), _make_race(905005, vantage="eu-central-1")]

        storage.write_day_bundle(day, races)
        assert storage.read_day_bundle(day) == races

    def test_missing_bundle_returns_none(self, tmp_path):
        storage = LocalStorage(tmp_path)
        assert storage.read_day_bundle(date(2026, 7, 10)) is None

    def test_bundle_file_excluded_from_race_refs(self, tmp_path):
        """_bundle.json must never be picked up as a race file."""
        storage = LocalStorage(tmp_path)
        storage.ensure_initial_files()

        # Write a real race and a bundle into the same day directory.
        # epoch 1700000000 => 2023-11-14 UTC
        race = _make_race(800001, vantage="local", epoch=1700000000.0)
        race["winner"] = "atlaspool"
        race["winner_nonempty"] = "atlaspool"
        race["empty_first_pools"] = []
        storage.write_race(race)
        storage.write_day_bundle(date(2023, 11, 14), [race])

        refs = storage.list_race_refs(date(2023, 11, 14))
        assert len(refs) == 1
        assert refs[0]["height"] == 800001
