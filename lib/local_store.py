"""Local filesystem storage for StratumRace standalone mode.

Pure-stdlib implementation (os, json, pathlib, logging, datetime) with no
async or framework dependencies. Mirrors the cloud ingest handler's storage
responsibilities against the local filesystem and implements the
StorageBackend protocol for aggregation.

Atomic writes use temp file + os.replace() so the HTTP server never reads
a partially-written file (R1.8). Write failures on recent-blocks/latest
are logged and leave existing content intact (R1.7).
"""

import json
import logging
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, List

from lib.aggregation import RaceRef
from lib.race_summary import build_latest_manifest, build_race_summary

logger = logging.getLogger(__name__)

# Standalone recent-blocks cap: 55 entries for a single vantage (R1.2)
RECENT_BLOCKS_CAP = 55


class LocalStorage:
    """Filesystem storage backend for standalone mode.

    Implements race file writing (with atomic recent-blocks and latest
    updates) and the StorageBackend protocol for aggregation.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.api_dir = self.data_dir / "api"

    # ------------------------------------------------------------------
    # Initial file creation (R3.9, R1.6)
    # ------------------------------------------------------------------

    def ensure_initial_files(self) -> None:
        """Create the data dir tree and valid empty data files if absent.

        Creates: api/races/, api/recent/, api/aggregates/daily/,
        api/aggregates/monthly/, api/config/, and empty initial JSON
        files for recent-blocks, latest, and aggregate presets.
        """
        dirs = [
            self.api_dir / "races",
            self.api_dir / "recent",
            self.api_dir / "aggregates" / "daily",
            self.api_dir / "aggregates" / "monthly",
            self.api_dir / "config",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        # recent-blocks.json: empty array
        recent_path = self.api_dir / "recent" / "recent-blocks.json"
        if not recent_path.exists():
            self._atomic_write(recent_path, [])

        # latest.json: null height/epoch
        latest_path = self.api_dir / "latest.json"
        if not latest_path.exists():
            self._atomic_write(latest_path, {"height": None, "epoch": None})

        # Empty aggregate files
        empty_aggregate = {
            "date": None,
            "generated_utc": None,
            "total_races": 0,
            "vantage_points": [],
            "pools": {},
        }
        aggregate_files = [
            self.api_dir / "aggregates" / "recent-10.json",
            self.api_dir / "aggregates" / "recent-50.json",
            self.api_dir / "aggregates" / "last-24h.json",
            self.api_dir / "aggregates" / "last-7d.json",
            self.api_dir / "aggregates" / "last-30d.json",
        ]
        for agg_path in aggregate_files:
            if not agg_path.exists():
                self._atomic_write(agg_path, empty_aggregate)

    # ------------------------------------------------------------------
    # Race writing (R1.1, R1.2, R1.3, R1.4, R1.7, R1.8)
    # ------------------------------------------------------------------

    def write_race(self, race_result: dict) -> None:
        """Write a race result to disk and update recent-blocks + latest.

        Synchronous, blocking. Called from an executor in the standalone
        server. All writes are atomic (temp file + os.replace).

        On failure writing recent-blocks or latest, logs the error and
        preserves existing content.
        """
        # Determine file path: api/races/YYYY/MM/DD/<height>-<vantage>.json
        first_epoch = race_result.get("first_epoch", 0)
        dt = datetime.fromtimestamp(first_epoch, tz=timezone.utc)
        date_parts = dt.strftime("%Y/%m/%d")

        height = race_result.get("block_height")
        vantage = race_result.get("vantage", "local")

        # Null-height races (mempool.space lag) include the epoch in the key
        # so two unknown blocks on the same day never overwrite each other.
        # Mirrors the cloud ingest handler's _compute_s3_path.
        if height is None:
            height_str = f"unknown-{int(first_epoch)}"
        else:
            height_str = str(height)

        race_filename = f"{height_str}-{vantage}.json"
        race_path = self.api_dir / "races" / date_parts / race_filename

        # Write race file (atomic)
        self._atomic_write(race_path, race_result)

        # Update recent-blocks.json (dedup by height+vantage, cap 55)
        try:
            self._update_recent_blocks(race_result)
        except Exception:
            logger.exception(
                "Failed to update recent-blocks.json; preserving existing content"
            )

        # Update latest.json
        try:
            self._update_latest(race_result)
        except Exception:
            logger.exception(
                "Failed to update latest.json; preserving existing content"
            )

        # Update vantage status with last_race_utc
        try:
            self._update_vantage_status(race_result)
        except Exception:
            logger.exception(
                "Failed to update vantages.json; preserving existing content"
            )

    def _update_recent_blocks(self, race_result: dict) -> None:
        """Update recent-blocks.json: dedup by height+vantage, prepend, cap 55."""
        recent_path = self.api_dir / "recent" / "recent-blocks.json"

        # Read existing entries
        try:
            existing = json.loads(recent_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except (OSError, json.JSONDecodeError):
            existing = []

        # Build the new summary entry
        summary = build_race_summary(race_result)
        new_height = summary.get("height")
        new_vantage = summary.get("vantage")

        # Remove any existing entry with the same height+vantage (dedup).
        # Null-height entries dedup by epoch instead, so two different
        # unknown blocks never dedupe each other (mirrors cloud ingest).
        if new_height is not None:
            deduped = [
                entry
                for entry in existing
                if not (
                    entry.get("height") == new_height
                    and entry.get("vantage") == new_vantage
                )
            ]
        else:
            new_epoch = summary.get("epoch")
            deduped = [
                entry
                for entry in existing
                if not (
                    entry.get("height") is None
                    and entry.get("vantage") == new_vantage
                    and entry.get("epoch") == new_epoch
                )
            ]

        # Prepend new entry and cap at 55
        updated = [summary] + deduped
        updated = updated[:RECENT_BLOCKS_CAP]

        self._atomic_write(recent_path, updated)

    def _update_latest(self, race_result: dict) -> None:
        """Update latest.json with the block height and epoch."""
        latest_path = self.api_dir / "latest.json"
        manifest = build_latest_manifest(race_result)
        self._atomic_write(latest_path, manifest)

    def _update_vantage_status(self, race_result: dict) -> None:
        """Update status/vantages.json with the latest race time."""
        from datetime import datetime, timezone

        status_path = self.api_dir / "status" / "vantages.json"
        if not status_path.exists():
            return  # No status file to update

        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        vantage = race_result.get("vantage", "local")
        first_epoch = race_result.get("first_epoch", 0)
        dt = datetime.fromtimestamp(first_epoch, tz=timezone.utc)
        race_utc = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if "vantages" not in status:
            status["vantages"] = {}
        if vantage not in status["vantages"]:
            status["vantages"][vantage] = {}

        status["vantages"][vantage]["last_race_utc"] = race_utc
        status["vantages"][vantage]["last_heartbeat_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        status["vantages"][vantage]["status"] = "online"

        self._atomic_write(status_path, status)

    # ------------------------------------------------------------------
    # StorageBackend protocol methods (R5.9)
    # ------------------------------------------------------------------

    def list_race_refs(self, day: date) -> List[RaceRef]:
        """List all race file references for a given UTC day.

        Scans api/races/YYYY/MM/DD/ and parses <height>-<vantage>.json
        filenames into RaceRef dicts.
        """
        day_dir = self.api_dir / "races" / day.strftime("%Y/%m/%d")
        if not day_dir.exists():
            return []

        refs: List[RaceRef] = []
        pattern = re.compile(r"^(\d+|unknown)-(.+)\.json$")

        for entry in day_dir.iterdir():
            if not entry.is_file():
                continue
            match = pattern.match(entry.name)
            if not match:
                continue
            height_str, vantage = match.groups()
            if height_str == "unknown":
                continue  # Skip unknown-height files for aggregation
            try:
                height = int(height_str)
            except ValueError:
                continue

            refs.append(
                RaceRef(key=str(entry), height=height, vantage=vantage)
            )

        return refs

    def read_race(self, ref: RaceRef) -> dict:
        """Read and return the parsed race JSON for a given reference."""
        path = Path(ref["key"])
        return json.loads(path.read_text(encoding="utf-8"))

    def write_aggregate(self, rel_path: str, data: dict) -> None:
        """Write an aggregate dict atomically to api/{rel_path}."""
        target = self.api_dir / rel_path
        self._atomic_write(target, data)

    # ------------------------------------------------------------------
    # Day bundles (optional StorageBackend extension)
    #
    # Finalized (immutable) days are consolidated into one _bundle.json
    # per day so aggregation reads one file instead of one per race.
    # The leading underscore keeps the file out of list_race_refs (its
    # name never matches the <height>-<vantage>.json pattern).
    # ------------------------------------------------------------------

    def _bundle_path(self, day: date) -> Path:
        return self.api_dir / "races" / day.strftime("%Y/%m/%d") / "_bundle.json"

    def read_day_bundle(self, day: date) -> Any:
        """Return the consolidated race list for a day, or None if absent/invalid."""
        try:
            data = json.loads(self._bundle_path(day).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, list) else None

    def write_day_bundle(self, day: date, races: List[dict]) -> None:
        """Persist the consolidated race list for a finalized day."""
        self._atomic_write(self._bundle_path(day), races)

    # ------------------------------------------------------------------
    # Atomic write helper
    # ------------------------------------------------------------------

    def _atomic_write(self, path: Path, data: Any) -> None:
        """Write JSON atomically: write to temp file in same dir, then os.replace."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data), encoding="utf-8")
            os.replace(str(tmp_path), str(path))
        except OSError:
            # Clean up temp file if replace failed
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
