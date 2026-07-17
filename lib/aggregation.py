"""Storage-agnostic aggregation orchestration for StratumRace.

This module defines the StorageBackend protocol and implements
run_all_aggregations — the shared orchestration logic that produces
daily, monthly, recent-10, recent-50, last-24h, and last-7d aggregate
files by calling compute_aggregate from lib.aggregate_stats.

Both the cloud handler and the standalone aggregator call
run_all_aggregations with their respective backend implementations.
This ensures identical file-selection logic and output structure across
deployment modes, eliminating drift.

Race files are loaded once per day per invocation and cached in memory.
All phases within a single run_all_aggregations call share the same
snapshot, ensuring consistency and eliminating redundant backend reads
(~98% reduction in backend read operations vs the naive approach).

NO boto3, os.path, pathlib, or filesystem imports allowed — this module
reads and writes only through the StorageBackend protocol.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Protocol, TypedDict

from lib.aggregate_stats import compute_aggregate

logger = logging.getLogger(__name__)

# Boundary grace window: races confirmed just before a UTC day/month boundary
# can be ingested AFTER the final scheduled run of that period (collector
# enrichment adds ~6s, POST retries up to ~15s). For the first
# BOUNDARY_GRACE_MINUTES after midnight UTC, the previous day's (and on the
# 1st, the previous month's) aggregate is recomputed so trailing races are
# not permanently missing from the finalized files. With a 5-minute schedule,
# 15 minutes guarantees at least two recompute runs after the boundary.
BOUNDARY_GRACE_MINUTES = 15


class RaceRef(TypedDict):
    """Reference to a race file in any storage backend."""

    key: str  # backend-specific locator (S3 key or file path)
    height: int
    vantage: str


class StorageBackend(Protocol):
    """Protocol that storage backends must implement for aggregation.

    Backends MAY additionally implement two optional methods for the
    finalized-day bundle optimization (detected via getattr, so fakes and
    older backends keep working without them):

        def read_day_bundle(self, day: date) -> Optional[List[dict]]:
            Return the consolidated race list for a finalized day, or
            None when no bundle exists.

        def write_day_bundle(self, day: date, races: List[dict]) -> None:
            Persist the consolidated race list for a finalized day.
    """

    def list_race_refs(self, day: date) -> List[RaceRef]:
        """List all race file references for a given UTC day."""
        ...

    def read_race(self, ref: RaceRef) -> dict:
        """Read and return the parsed race JSON for a given reference."""
        ...

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        """Write an aggregate dict to the given relative path."""
        ...


def _is_day_finalized(day: date, now: datetime) -> bool:
    """True when no further races can be ingested for the given UTC day.

    Today is never finalized. Yesterday is finalized only once the
    boundary grace window has passed (late races confirmed at 23:5x can
    still be ingested during the grace window). Older days are always
    finalized.
    """
    today = now.date()
    if day >= today:
        return False
    if day == today - timedelta(days=1) and _in_boundary_grace(now):
        return False
    return True


class _DayCache:
    """Per-invocation cache for loaded race data.

    Each day's race files are loaded once from the backend and reused
    across all aggregation phases. This eliminates redundant reads
    (previously today's files were read 5x per invocation) and provides
    snapshot-consistent reads across phases.

    Cross-invocation cost optimization (day bundles): finalized days are
    immutable, so their races are persisted as a single consolidated
    bundle object. On subsequent invocations a finalized day costs ONE
    backend read instead of one per race file (~97% fewer S3 GETs at
    steady state, which is the dominant AWS cost of the platform).
    Bundle support is optional: backends that don't implement
    read_day_bundle/write_day_bundle (e.g., test fakes) fall back to
    per-file loading transparently.

    Memory usage: ~1.5 KB/race x 288 races/day x 30 days = ~13 MB at
    end-of-month — well within typical runtime memory limits.
    """

    def __init__(self, backend: StorageBackend, now: datetime) -> None:
        self._backend = backend
        self._now = now
        self._races: Dict[date, List[Dict[str, Any]]] = {}
        self._refs: Dict[date, List[RaceRef]] = {}

    def get_races(self, day: date) -> List[Dict[str, Any]]:
        """Get all valid races for a day (cached after first load)."""
        if day in self._races:
            return self._races[day]

        finalized = _is_day_finalized(day, self._now)
        read_bundle = getattr(self._backend, "read_day_bundle", None)
        write_bundle = getattr(self._backend, "write_day_bundle", None)

        # Finalized days: prefer the consolidated bundle (single read)
        if finalized and callable(read_bundle):
            try:
                bundled = read_bundle(day)
            except Exception as e:
                logger.warning("Failed to read day bundle for %s: %s", day, e)
                bundled = None
            if bundled is not None and isinstance(bundled, list):
                self._races[day] = bundled
                return bundled

        # Fall back to per-file loading
        races = _load_races_for_day(self._backend, day)
        self._races[day] = races

        # Persist a bundle for finalized days so future invocations pay
        # one read instead of len(races). Best-effort: a failed write just
        # means the next run rebuilds it.
        if finalized and callable(write_bundle):
            try:
                write_bundle(day, races)
                logger.info(
                    "Wrote day bundle for %s (%d races)", day, len(races)
                )
            except Exception as e:
                logger.warning("Failed to write day bundle for %s: %s", day, e)

        return races

    def get_refs(self, day: date) -> List[RaceRef]:
        """Get all race refs for a day (cached after first list)."""
        if day not in self._refs:
            self._refs[day] = self._backend.list_race_refs(day)
        return self._refs[day]


def run_all_aggregations(
    backend: StorageBackend,
    now: datetime,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Compute and write all aggregate files.

    Produces daily, monthly, recent-10, recent-50, last-24h, and last-7d
    aggregates by calling compute_aggregate from lib.aggregate_stats.

    Race files are loaded once per day and cached in memory for the
    duration of this call. All phases share the same snapshot, providing
    consistency and eliminating redundant backend reads.

    Args:
        backend: Storage backend implementing list/read/write operations.
        now: Current UTC datetime used for time-window calculations.
        should_continue: Callable checked before each major phase; if it
            returns False, remaining phases are skipped. The cloud handler
            uses this for timeout checks; standalone passes the default
            (always True).
    """
    cache = _DayCache(backend, now)

    # Phase 1: Daily aggregate
    if not should_continue():
        return
    _compute_daily_aggregate(cache, backend, now)

    # Phase 2: Recent-10 and Recent-50
    if not should_continue():
        return
    _compute_recent_aggregates(cache, backend, now)

    # Phase 3: Time-based aggregates (last-24h, last-7d, last-30d)
    if not should_continue():
        return
    _compute_time_based_aggregates(cache, backend, now, should_continue)

    # Phase 4: Monthly aggregate
    if not should_continue():
        return
    _compute_monthly_aggregate(cache, backend, now, should_continue)


def _load_races_for_day(
    backend: StorageBackend, day: date
) -> List[Dict[str, Any]]:
    """Load all valid race files for a given day, skipping invalid ones."""
    refs = backend.list_race_refs(day)
    races: List[Dict[str, Any]] = []
    for ref in refs:
        try:
            race = backend.read_race(ref)
            races.append(race)
        except Exception as e:
            logger.warning(
                "Skipping unreadable/invalid race file %s: %s", ref["key"], e
            )
    return races


def _in_boundary_grace(now: datetime) -> bool:
    """True during the first BOUNDARY_GRACE_MINUTES after midnight UTC."""
    return now.hour == 0 and now.minute < BOUNDARY_GRACE_MINUTES


def _last_day_of_month(day: date) -> date:
    """Return the last calendar day of the month containing `day`."""
    if day.month == 12:
        return date(day.year, 12, 31)
    return date(day.year, day.month + 1, 1) - timedelta(days=1)


def _compute_daily_aggregate(
    cache: _DayCache, backend: StorageBackend, now: datetime
) -> None:
    """Compute and write the daily aggregate for the current UTC day.

    During the boundary grace window just after midnight, the previous day's
    aggregate is also recomputed: races confirmed at 23:5x are often ingested
    after that day's final scheduled run, and without this recompute they
    would be permanently missing from the finalized daily file.
    """
    today = now.date()
    _write_daily_for(cache, backend, now, today)

    if _in_boundary_grace(now):
        _write_daily_for(cache, backend, now, today - timedelta(days=1))


def _write_daily_for(
    cache: _DayCache, backend: StorageBackend, now: datetime, day: date
) -> None:
    """Compute and write the daily aggregate for a specific UTC day."""
    date_str = day.strftime("%Y-%m-%d")
    races = cache.get_races(day)
    logger.info("Daily aggregate: %d races for %s", len(races), date_str)

    aggregate = compute_aggregate(races, date_str, now)
    backend.write_aggregate(f"aggregates/daily/{date_str}.json", aggregate)


def _compute_monthly_aggregate(
    cache: _DayCache,
    backend: StorageBackend,
    now: datetime,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Compute and write the monthly aggregate for the current UTC month.

    On the first day of a month, during the boundary grace window, the
    previous month's aggregate is also recomputed so races ingested after
    that month's final scheduled run are not permanently missing.
    """
    today = now.date()
    _write_monthly_for(cache, backend, now, today, should_continue)

    if today.day == 1 and _in_boundary_grace(now):
        _write_monthly_for(
            cache, backend, now, today - timedelta(days=1), should_continue
        )


def _write_monthly_for(
    cache: _DayCache,
    backend: StorageBackend,
    now: datetime,
    any_day_in_month: date,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Compute and write the monthly aggregate for the month containing the given day.

    Checks should_continue between each day's load; if time runs out
    mid-scan, the write is skipped entirely (the previous file is kept)
    rather than publishing an aggregate computed from partial data.
    """
    month_str = any_day_in_month.strftime("%Y-%m")
    first_day = date(any_day_in_month.year, any_day_in_month.month, 1)
    end_day = min(now.date(), _last_day_of_month(any_day_in_month))

    all_races: List[Dict[str, Any]] = []
    current_day = first_day
    while current_day <= end_day:
        if not should_continue():
            logger.warning(
                "Monthly aggregate %s aborted mid-scan (out of time); "
                "keeping previous file",
                month_str,
            )
            return
        all_races.extend(cache.get_races(current_day))
        current_day += timedelta(days=1)

    logger.info("Monthly aggregate: %d races for %s", len(all_races), month_str)

    aggregate = compute_aggregate(all_races, month_str, now)
    backend.write_aggregate(f"aggregates/monthly/{month_str}.json", aggregate)


def _compute_recent_aggregates(
    cache: _DayCache, backend: StorageBackend, now: datetime
) -> None:
    """Compute recent-10 and recent-50 aggregates from the most recent unique blocks.

    Lists race refs from the last 3 days (cached), sorts by height descending,
    finds the top N unique heights, and uses cached race data for computation.
    """
    # Collect all race refs from the last 3 days (using cached refs)
    all_refs: List[RaceRef] = []
    for days_back in range(3):
        day = now.date() - timedelta(days=days_back)
        all_refs.extend(cache.get_refs(day))

    if not all_refs:
        # Write empty-but-valid aggregates for both targets
        for target_count in [10, 50]:
            label = f"recent-{target_count}"
            aggregate = compute_aggregate([], label, now)
            aggregate["blocks"] = 0
            aggregate["type"] = label
            backend.write_aggregate(f"aggregates/{label}.json", aggregate)
        return

    # Sort by height descending (most recent blocks first)
    all_refs.sort(key=lambda r: r["height"], reverse=True)

    # Find unique block heights in order
    seen_heights: List[int] = []
    seen_set: set = set()
    for ref in all_refs:
        h = ref["height"]
        if h not in seen_set:
            seen_set.add(h)
            seen_heights.append(h)

    # Build a lookup from the cached races for the last 3 days
    all_races_by_key: Dict[str, Dict[str, Any]] = {}
    for days_back in range(3):
        day = now.date() - timedelta(days=days_back)
        for race in cache.get_races(day):
            h = race.get("block_height")
            v = race.get("vantage", "unknown")
            all_races_by_key[f"{h}-{v}"] = race

    # For each target (10 and 50), compute an aggregate
    for target_count in [10, 50]:
        target_block_heights = set(seen_heights[:target_count])
        actual_blocks = len(target_block_heights)

        # Get races matching the target heights from cached data
        target_refs = [r for r in all_refs if r["height"] in target_block_heights]
        races: List[Dict[str, Any]] = []
        for ref in target_refs:
            key = f"{ref['height']}-{ref['vantage']}"
            if key in all_races_by_key:
                races.append(all_races_by_key[key])

        label = f"recent-{target_count}"
        aggregate = compute_aggregate(races, label, now)
        aggregate["blocks"] = actual_blocks
        aggregate["type"] = label

        backend.write_aggregate(f"aggregates/{label}.json", aggregate)
        logger.info(
            "Wrote %s (%d races from %d blocks)", label, len(races), actual_blocks
        )


def _compute_time_based_aggregates(
    cache: _DayCache,
    backend: StorageBackend,
    now: datetime,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Compute last-24h, last-7d, and last-30d aggregates from race files
    within rolling time windows.

    Loads races from the appropriate date range (cached), filters by
    first_epoch >= cutoff_epoch, and computes aggregates. Checks
    should_continue before each window; skipped windows keep their
    previous file.
    """
    for label, hours in [("last-24h", 24), ("last-7d", 168), ("last-30d", 720)]:
        if not should_continue():
            logger.warning(
                "Time-based aggregate %s skipped (out of time); "
                "keeping previous file",
                label,
            )
            return
        cutoff = now - timedelta(hours=hours)
        cutoff_epoch = cutoff.timestamp()

        # Determine which days to scan (covers the time window + buffer)
        days_to_scan = (hours // 24) + 2

        all_races: List[Dict[str, Any]] = []
        for days_back in range(days_to_scan):
            day = now.date() - timedelta(days=days_back)
            for race in cache.get_races(day):
                # Filter by timestamp: only include races within the window
                race_epoch = race.get("first_epoch", 0)
                if race_epoch >= cutoff_epoch:
                    all_races.append(race)

        aggregate = compute_aggregate(all_races, label, now)
        aggregate["type"] = label
        aggregate["hours"] = hours

        backend.write_aggregate(f"aggregates/{label}.json", aggregate)
        logger.info(
            "Wrote %s (%d races from last %d hours)", label, len(all_races), hours
        )
