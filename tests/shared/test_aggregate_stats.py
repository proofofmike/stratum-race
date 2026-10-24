"""Unit tests for lib/aggregate_stats.py.

Validates: Requirements 13.5, 13.8, 16.6

Tests cover:
- new_accumulator() structure
- percentile() with edge cases
- compute_stats() with empty, single, all-zero, and varied data
- compute_aggregate() with empty, single race, multi-vantage, and template modes
- compute_aggregate(allowed_pools=...) dropping removed pools and re-basing offsets
"""

import copy
import math
from datetime import datetime, timezone

from lib.aggregate_stats import (
    compute_aggregate,
    compute_stats,
    new_accumulator,
    percentile,
)


class TestNewAccumulator:
    """Tests for new_accumulator()."""

    def test_returns_correct_structure(self):
        """new_accumulator returns dict with all required keys."""
        acc = new_accumulator()
        assert acc == {
            "offsets": [],
            "wins": 0,
            "races_seen": 0,
            "races_eligible": 0,
            "empty_first_count": 0,
            "stale_samples": [],
            "gap_samples": [],
        }

    def test_returns_fresh_instance(self):
        """Each call returns a new independent instance."""
        a = new_accumulator()
        b = new_accumulator()
        a["offsets"].append(1.0)
        assert b["offsets"] == []


class TestPercentile:
    """Tests for percentile()."""

    def test_single_value(self):
        """Single value returns itself for any percentile."""
        assert percentile([42.0], 0.5) == 42.0
        assert percentile([42.0], 0.95) == 42.0

    def test_multiple_values_median(self):
        """p50 of [1,2,3,4,5] -> 3."""
        result = percentile([1, 2, 3, 4, 5], 0.5)
        assert result == 3.0

    def test_p95_with_20_values(self):
        """p95 of range(1,21) -> 20 (index = ceil(0.95*20)-1 = 18)."""
        data = list(range(1, 21))
        result = percentile(data, 0.95)
        # ceil(0.95 * 20) - 1 = ceil(19) - 1 = 18 -> data[18] = 19
        assert result == 19.0

    def test_unsorted_input(self):
        """percentile sorts the data internally."""
        data = [5, 1, 3, 2, 4]
        assert percentile(data, 0.5) == 3.0


class TestComputeStats:
    """Tests for compute_stats()."""

    def test_empty_accumulator(self):
        """Empty accumulator returns all None outputs."""
        acc = new_accumulator()
        stats = compute_stats(acc)
        assert stats["median_ms"] is None
        assert stats["avg_ms"] is None
        assert stats["p95_ms"] is None
        assert stats["win_pct"] is None
        assert stats["waste_min_day"] is None
        assert stats["wins"] == 0
        assert stats["races_seen"] == 0

    def test_single_sample(self):
        """Single sample produces valid statistics."""
        acc = new_accumulator()
        acc["offsets"] = [50.0]
        acc["races_seen"] = 1
        acc["wins"] = 0
        acc["stale_samples"] = [50.0]
        acc["gap_samples"] = [0.0]
        stats = compute_stats(acc)
        assert stats["median_ms"] == 50.0
        assert stats["avg_ms"] == 50.0
        assert stats["p95_ms"] == 50.0
        assert stats["win_pct"] == 0.0
        assert stats["waste_min_day"] is not None

    def test_all_zero_offsets(self):
        """All-zero offsets: wins = races_seen, median/avg/p95 = 0."""
        acc = new_accumulator()
        acc["offsets"] = [0.0, 0.0, 0.0]
        acc["races_seen"] = 3
        acc["wins"] = 3
        acc["stale_samples"] = [0.0, 0.0, 0.0]
        acc["gap_samples"] = [0.0, 0.0, 0.0]
        stats = compute_stats(acc)
        assert stats["median_ms"] == 0.0
        assert stats["avg_ms"] == 0.0
        assert stats["p95_ms"] == 0.0
        assert stats["win_pct"] == 100.0
        assert stats["waste_min_day"] == 0.0

    def test_varied_data(self):
        """Varied offsets produce correct median, avg, p95, win%, waste."""
        acc = new_accumulator()
        acc["offsets"] = [0.0, 10.0, 20.0, 30.0, 40.0]
        acc["races_seen"] = 5
        acc["wins"] = 1  # only the 0.0 offset is a win
        acc["stale_samples"] = [0.0, 10.0, 20.0, 30.0, 40.0]
        acc["gap_samples"] = [0.0, 5.0, 10.0, 15.0, 20.0]
        stats = compute_stats(acc)
        # Median of [0,10,20,30,40] = 20.0
        assert stats["median_ms"] == 20.0
        # Avg = (0+10+20+30+40)/5 = 20.0
        assert stats["avg_ms"] == 20.0
        # Win% = 1/5*100 = 20.0
        assert stats["win_pct"] == 20.0
        # p95 should be one of the higher values
        assert stats["p95_ms"] == 40.0
        # waste_min_day should be computed and > 0
        assert stats["waste_min_day"] is not None
        assert stats["waste_min_day"] > 0


class TestComputeAggregate:
    """Tests for compute_aggregate()."""

    def test_empty_races_list(self):
        """Empty races list returns valid structure with zero totals."""
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = compute_aggregate([], "2025-01-15", now)
        assert result["date"] == "2025-01-15"
        assert result["total_races"] == 0
        assert result["vantage_points"] == []
        assert result["pools"] == {}
        assert "generated_utc" in result

    def test_single_race(self):
        """Single race produces stats for observed pools."""
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        race = {
            "vantage": "local",
            "nonempty_arrivals_offset_ms": {"poolA": 0.0, "poolB": 50.0},
            "arrivals_offset_ms": {"poolA": 0.0, "poolB": 50.0},
            "empty_first_pools": [],
            "eligible_at_start": ["poolA", "poolB"],
        }
        result = compute_aggregate([race], "2025-01-15", now)
        assert result["total_races"] == 1
        assert "poolA" in result["pools"]
        assert "poolB" in result["pools"]
        # poolA won (offset=0), poolB did not
        pool_a = result["pools"]["poolA"]
        assert pool_a["combined"]["wins"] == 1
        assert pool_a["combined"]["median_ms"] == 0.0
        pool_b = result["pools"]["poolB"]
        assert pool_b["combined"]["wins"] == 0
        assert pool_b["combined"]["median_ms"] == 50.0

    def test_multiple_vantages(self):
        """Multiple vantages appear in by_vantage breakdown."""
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        races = [
            {
                "vantage": "us-east",
                "nonempty_arrivals_offset_ms": {"poolA": 0.0},
                "arrivals_offset_ms": {"poolA": 0.0},
                "empty_first_pools": [],
                "eligible_at_start": ["poolA"],
            },
            {
                "vantage": "eu-central",
                "nonempty_arrivals_offset_ms": {"poolA": 30.0},
                "arrivals_offset_ms": {"poolA": 30.0},
                "empty_first_pools": [],
                "eligible_at_start": ["poolA"],
            },
        ]
        result = compute_aggregate(races, "2025-01-15", now)
        assert result["total_races"] == 2
        assert sorted(result["vantage_points"]) == ["eu-central", "us-east"]
        pool_a = result["pools"]["poolA"]
        assert "us-east" in pool_a["by_vantage"]
        assert "eu-central" in pool_a["by_vantage"]
        # Combined wins = 1 (only the 0.0 offset)
        assert pool_a["combined"]["wins"] == 1

    def test_any_template_vs_full_template(self):
        """any_combined uses arrivals_offset_ms, combined uses nonempty."""
        now = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        race = {
            "vantage": "local",
            "nonempty_arrivals_offset_ms": {"poolA": 10.0},
            "arrivals_offset_ms": {"poolA": 0.0, "poolB": 5.0},
            "empty_first_pools": [],
            "eligible_at_start": ["poolA", "poolB"],
        }
        result = compute_aggregate([race], "2025-01-15", now)
        # Full template: only poolA appears (from nonempty)
        pool_a = result["pools"]["poolA"]
        assert pool_a["combined"]["median_ms"] == 10.0
        # Any template: poolA has 0.0, poolB has 5.0
        assert pool_a["any_combined"]["median_ms"] == 0.0
        pool_b = result["pools"]["poolB"]
        assert pool_b["any_combined"]["median_ms"] == 5.0


NOW = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
ALLOWED = {"poolA", "poolB"}


def _removed_pool_won_race():
    """A race the removed pool won in both modes.

    Full template: removed 0, poolA 40, poolB 100.
    Any template:  removed 0, poolB 5,  poolA 10 (a different order on purpose,
    so each map has to be re-based to its own fastest remaining pool).
    """
    return {
        "vantage": "us-east-1",
        "nonempty_arrivals_offset_ms": {"removed": 0.0, "poolA": 40.0, "poolB": 100.0},
        "arrivals_offset_ms": {"removed": 0.0, "poolB": 5.0, "poolA": 10.0},
        "empty_first_pools": ["removed", "poolB"],
        "empty_to_full_ms": {"removed": 300.0, "poolB": 95.0},
        "eligible_at_start": ["poolA", "poolB", "removed"],
    }


class TestComputeAggregateAllowedPools:
    """compute_aggregate(allowed_pools=...) reads as if removed pools never raced."""

    def test_removed_winner_hands_win_to_runner_up_and_rebases(self):
        result = compute_aggregate([_removed_pool_won_race()], "d", NOW, ALLOWED)

        assert set(result["pools"]) == ALLOWED
        a = result["pools"]["poolA"]
        b = result["pools"]["poolB"]

        # Full template: poolA is fastest remaining -> 0.0 and the win
        assert a["combined"]["wins"] == 1
        assert a["combined"]["median_ms"] == 0.0
        assert a["by_vantage"]["us-east-1"]["wins"] == 1
        assert b["combined"]["wins"] == 0
        assert b["combined"]["median_ms"] == 60.0
        assert b["by_vantage"]["us-east-1"]["median_ms"] == 60.0

        # Any template re-based independently: poolB is fastest remaining
        assert b["any_combined"]["wins"] == 1
        assert b["any_combined"]["median_ms"] == 0.0
        assert a["any_combined"]["wins"] == 0
        assert a["any_combined"]["median_ms"] == 5.0
        assert a["any_by_vantage"]["us-east-1"]["median_ms"] == 5.0

    def test_rebased_win_matches_race_without_removed_pool(self):
        """Re-based output equals the output for the race minus the removed pool."""
        race = _removed_pool_won_race()
        without = {
            "vantage": "us-east-1",
            "nonempty_arrivals_offset_ms": {"poolA": 0.0, "poolB": 60.0},
            "arrivals_offset_ms": {"poolB": 0.0, "poolA": 5.0},
            "empty_first_pools": ["poolB"],
            "empty_to_full_ms": {"poolB": 95.0},
            "eligible_at_start": ["poolA", "poolB"],
        }
        assert (
            compute_aggregate([race], "d", NOW, ALLOWED)["pools"]
            == compute_aggregate([without], "d", NOW)["pools"]
        )

    def test_removed_pool_that_did_not_win_changes_nothing(self):
        race = {
            "vantage": "us-east-1",
            "nonempty_arrivals_offset_ms": {"poolA": 0.0, "poolB": 25.5, "removed": 70.0},
            "arrivals_offset_ms": {"poolB": 0.0, "poolA": 3.25, "removed": 9.0},
            "empty_first_pools": ["poolB"],
            "empty_to_full_ms": {"poolB": 25.5},
            "eligible_at_start": ["poolA", "poolB", "removed"],
        }
        filtered = compute_aggregate([race], "d", NOW, ALLOWED)
        unfiltered = compute_aggregate([race], "d", NOW)

        assert "removed" not in filtered["pools"]
        for pool in ALLOWED:
            assert filtered["pools"][pool] == unfiltered["pools"][pool]

    def test_allowed_pools_none_is_unchanged(self):
        """With no roster, the removed pool keeps its win and nothing is re-based."""
        result = compute_aggregate([_removed_pool_won_race()], "d", NOW, None)

        assert set(result["pools"]) == {"poolA", "poolB", "removed"}
        assert result["pools"]["removed"]["combined"]["wins"] == 1
        assert result["pools"]["removed"]["any_combined"]["wins"] == 1
        assert result["pools"]["poolA"]["combined"]["wins"] == 0
        assert result["pools"]["poolA"]["combined"]["median_ms"] == 40.0
        assert result["pools"]["poolB"]["combined"]["median_ms"] == 100.0
        assert result["pools"]["poolB"]["any_combined"]["median_ms"] == 5.0
        assert result["pools"]["poolA"]["any_combined"]["median_ms"] == 10.0
        # Default argument behaves the same as an explicit None
        assert compute_aggregate([_removed_pool_won_race()], "d", NOW) == result

    def test_input_races_are_not_mutated(self):
        """Races are shared across phases via the day cache, so must stay intact."""
        races = [_removed_pool_won_race(), _removed_pool_won_race()]
        races[1]["vantage"] = "eu-central-1"
        snapshot = copy.deepcopy(races)

        compute_aggregate(races, "d", NOW, ALLOWED)

        assert races == snapshot
        # A later unfiltered phase over the same objects still sees the originals
        later = compute_aggregate(races, "d", NOW)
        assert later["pools"]["removed"]["combined"]["wins"] == 2

    def test_empty_first_gap_and_eligible_are_filtered(self):
        result = compute_aggregate([_removed_pool_won_race()], "d", NOW, ALLOWED)
        a = result["pools"]["poolA"]
        b = result["pools"]["poolB"]

        assert "removed" not in result["pools"]
        assert a["combined"]["races_eligible"] == 1
        assert b["combined"]["races_eligible"] == 1
        assert a["combined"]["empty_first_pct"] == 0.0
        assert b["combined"]["empty_first_pct"] == 100.0
        assert b["any_combined"]["empty_first_pct"] == 100.0
        # poolB's own empty->full gap still feeds its waste figure:
        # (60 stale + 0.02 * 95 gap) / 600000 * 1440
        assert b["combined"]["waste_min_day"] == round((60.0 + 0.02 * 95.0) / 600_000 * 1440, 2)

    def test_fallback_eligible_list_is_filtered(self):
        """Without eligible_at_start, the key-union fallback excludes removed pools."""
        race = _removed_pool_won_race()
        del race["eligible_at_start"]
        result = compute_aggregate([race], "d", NOW, ALLOWED)

        assert set(result["pools"]) == ALLOWED
        assert result["pools"]["poolA"]["combined"]["races_eligible"] == 1
        assert result["pools"]["poolB"]["any_combined"]["races_eligible"] == 1

    def test_race_with_every_pool_removed_contributes_nothing(self):
        only_removed = {
            "vantage": "us-east-1",
            "nonempty_arrivals_offset_ms": {"removed": 0.0},
            "arrivals_offset_ms": {"removed": 0.0},
            "empty_first_pools": ["removed"],
            "empty_to_full_ms": {"removed": 300.0},
            "eligible_at_start": ["removed"],
        }
        assert compute_aggregate([only_removed], "d", NOW, ALLOWED)["pools"] == {}

        normal = {
            "vantage": "us-east-1",
            "nonempty_arrivals_offset_ms": {"poolA": 0.0, "poolB": 20.0},
            "arrivals_offset_ms": {"poolA": 0.0, "poolB": 20.0},
            "empty_first_pools": [],
            "eligible_at_start": ["poolA", "poolB"],
        }
        mixed = compute_aggregate([only_removed, normal], "d", NOW, ALLOWED)
        alone = compute_aggregate([normal], "d", NOW, ALLOWED)
        assert mixed["pools"] == alone["pools"]
        # The race still counts toward races tracked
        assert mixed["total_races"] == 2

    def test_tied_fastest_remaining_pools_all_win(self):
        race = {
            "vantage": "us-east-1",
            "nonempty_arrivals_offset_ms": {"removed": 0.0, "poolA": 12.5, "poolB": 12.5},
            "arrivals_offset_ms": {"removed": 0.0, "poolA": 12.5, "poolB": 12.5},
            "empty_first_pools": [],
            "eligible_at_start": ["poolA", "poolB", "removed"],
        }
        result = compute_aggregate([race], "d", NOW, ALLOWED)
        for pool in ALLOWED:
            assert result["pools"][pool]["combined"]["wins"] == 1
            assert result["pools"][pool]["any_combined"]["wins"] == 1

    def test_non_numeric_offsets_are_skipped(self):
        race = _removed_pool_won_race()
        race["nonempty_arrivals_offset_ms"]["poolC"] = None
        race["arrivals_offset_ms"]["poolC"] = "bad"
        result = compute_aggregate([race], "d", NOW, ALLOWED | {"poolC"})

        assert result["pools"]["poolA"]["combined"]["median_ms"] == 0.0
        assert result["pools"]["poolB"]["any_combined"]["median_ms"] == 0.0
        # Same as the unfiltered path: a pool with only bad values never appears
        assert "poolC" not in result["pools"]
