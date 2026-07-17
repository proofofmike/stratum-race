"""Tests for aggregation boundary-grace recompute, last-30d window, and
bounded monthly computation.

Validates the fixes for:
- Day/month boundary: races ingested after the final scheduled run of a UTC
  day/month were permanently missing from daily/monthly aggregates.
- last-30d rolling aggregate (used by the frontend 30-day preset).
- Monthly phase aborts (without writing) when out of time mid-scan.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.aggregation import BOUNDARY_GRACE_MINUTES, RaceRef, run_all_aggregations


def _make_race(height, vantage, epoch):
    return {
        "block_height": height,
        "vantage": vantage,
        "first_epoch": epoch,
        "prevhash": "00" * 32,
        "arrivals_offset_ms": {"atlaspool": 0.0, "ckpool": 42.3},
        "nonempty_arrivals_offset_ms": {"atlaspool": 0.0, "ckpool": 42.3},
        "empty_first_pools": [],
        "eligible_at_start": ["atlaspool", "ckpool"],
    }


class MemBackend:
    """In-memory StorageBackend keyed by UTC day."""

    def __init__(self):
        self.races_by_day = {}
        self.writes = {}

    def add_race(self, epoch, height, vantage="us-east-1"):
        day = datetime.fromtimestamp(epoch, tz=timezone.utc).date()
        race = _make_race(height, vantage, epoch)
        self.races_by_day.setdefault(day, []).append(race)

    def list_race_refs(self, day: date) -> List[RaceRef]:
        return [
            RaceRef(key=f"{day}/{r['block_height']}-{r['vantage']}",
                    height=r["block_height"], vantage=r["vantage"])
            for r in self.races_by_day.get(day, [])
        ]

    def read_race(self, ref: RaceRef) -> dict:
        day_str, stem = ref["key"].split("/")
        day = date.fromisoformat(day_str)
        for r in self.races_by_day.get(day, []):
            if f"{r['block_height']}-{r['vantage']}" == stem:
                return r
        raise KeyError(ref["key"])

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        self.writes[rel_path] = data


class TestBoundaryGraceRecompute:
    def test_previous_day_recomputed_during_grace_window(self):
        """A race at 23:59 (ingested after the day's final run) appears in the
        previous day's aggregate when the first post-midnight run executes."""
        backend = MemBackend()
        # Race at 2026-07-15 23:59:00 UTC
        late_epoch = datetime(2026, 7, 15, 23, 59, tzinfo=timezone.utc).timestamp()
        backend.add_race(late_epoch, 905000)

        # First run after midnight (00:02 on the 16th)
        now = datetime(2026, 7, 16, 0, 2, tzinfo=timezone.utc)
        run_all_aggregations(backend, now)

        assert "aggregates/daily/2026-07-15.json" in backend.writes
        prev_day = backend.writes["aggregates/daily/2026-07-15.json"]
        assert prev_day["total_races"] == 1

    def test_previous_day_not_recomputed_outside_grace_window(self):
        backend = MemBackend()
        now = datetime(2026, 7, 16, 0, BOUNDARY_GRACE_MINUTES, tzinfo=timezone.utc)
        run_all_aggregations(backend, now)

        assert "aggregates/daily/2026-07-15.json" not in backend.writes
        assert "aggregates/daily/2026-07-16.json" in backend.writes

    def test_previous_month_recomputed_on_first_of_month_grace(self):
        """A race at 23:58 on the last day of a month appears in that month's
        aggregate when the first run of the new month executes."""
        backend = MemBackend()
        late_epoch = datetime(2026, 7, 31, 23, 58, tzinfo=timezone.utc).timestamp()
        backend.add_race(late_epoch, 905001)

        now = datetime(2026, 8, 1, 0, 3, tzinfo=timezone.utc)
        run_all_aggregations(backend, now)

        assert "aggregates/monthly/2026-07.json" in backend.writes
        prev_month = backend.writes["aggregates/monthly/2026-07.json"]
        assert prev_month["total_races"] == 1
        # Current (new) month also written
        assert "aggregates/monthly/2026-08.json" in backend.writes

    def test_previous_month_not_recomputed_mid_month(self):
        backend = MemBackend()
        now = datetime(2026, 7, 16, 0, 2, tzinfo=timezone.utc)
        run_all_aggregations(backend, now)

        assert "aggregates/monthly/2026-06.json" not in backend.writes
        assert "aggregates/monthly/2026-07.json" in backend.writes


class TestLast30dAggregate:
    def test_last_30d_written_with_correct_window(self):
        """last-30d includes a 20-day-old race but excludes a 40-day-old one."""
        backend = MemBackend()
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        inside = (now - timedelta(days=20)).timestamp()
        outside = (now - timedelta(days=40)).timestamp()
        backend.add_race(inside, 903000)
        backend.add_race(outside, 900000)

        run_all_aggregations(backend, now)

        assert "aggregates/last-30d.json" in backend.writes
        agg = backend.writes["aggregates/last-30d.json"]
        assert agg["hours"] == 720
        assert agg["total_races"] == 1  # Only the 20-day-old race

    def test_last_30d_excludes_race_just_outside_cutoff(self):
        backend = MemBackend()
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        just_outside = (now - timedelta(hours=721)).timestamp()
        backend.add_race(just_outside, 903001)

        run_all_aggregations(backend, now)

        assert backend.writes["aggregates/last-30d.json"]["total_races"] == 0


class TestBoundedMonthlyPhase:
    def test_monthly_write_skipped_when_out_of_time(self):
        """When should_continue flips false mid-scan, the monthly file is NOT
        written (previous file kept) rather than published from partial data."""
        backend = MemBackend()
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        backend.add_race(now.timestamp() - 3600, 905002)

        # Allow the first phases, then run out of time during the monthly scan.
        # Phases check should_continue: daily(1), recent(1), time-based(1 per
        # window x3), monthly phase gate(1), then per-day inside monthly.
        calls = {"n": 0}

        def should_continue():
            calls["n"] += 1
            return calls["n"] <= 8  # Runs out during the monthly day-scan

        run_all_aggregations(backend, now, should_continue=should_continue)

        assert "aggregates/monthly/2026-07.json" not in backend.writes
        # Earlier phases completed normally
        assert "aggregates/daily/2026-07-16.json" in backend.writes
