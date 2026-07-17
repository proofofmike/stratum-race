"""Shared race summary builders for recent-blocks.json and latest.json.

Pure stdlib — no boto3, no filesystem, no framework dependencies.
Imported by both the cloud ingest handler and the standalone LocalStorage.
"""

from datetime import datetime, timezone


def build_race_summary(race_result: dict) -> dict:
    """Build a race summary object for recent-blocks.json.

    Returns {height, utc, epoch, miner, winner, winner_nonempty,
    empty_jumpstart, second, second_delay_ms, spread_ms, pools_seen, vantage}.
    """
    arrivals = race_result.get("arrivals_offset_ms", {})
    sorted_arrivals = sorted(arrivals.items(), key=lambda x: x[1])

    # Determine winner and second place
    winner = sorted_arrivals[0][0] if sorted_arrivals else None
    second = sorted_arrivals[1][0] if len(sorted_arrivals) > 1 else None
    second_delay_ms = sorted_arrivals[1][1] if len(sorted_arrivals) > 1 else None

    # Compute spread (max offset - min offset)
    offsets = list(arrivals.values())
    spread_ms = max(offsets) - min(offsets) if offsets else 0.0

    # Determine empty jumpstart
    empty_first_pools = race_result.get("empty_first_pools", [])
    empty_jumpstart = empty_first_pools[0] if empty_first_pools else None

    # Build UTC timestamp from epoch
    first_epoch = race_result.get("first_epoch", 0)
    dt = datetime.fromtimestamp(first_epoch, tz=timezone.utc)
    utc_str = dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}+00:00"

    return {
        "height": race_result.get("block_height"),
        "utc": utc_str,
        "epoch": first_epoch,
        "miner": race_result.get("block_miner", "Unknown"),
        "winner": winner,
        "winner_nonempty": race_result.get("winner_nonempty", winner),
        "empty_jumpstart": empty_jumpstart,
        "second": second,
        "second_delay_ms": second_delay_ms,
        "spread_ms": spread_ms,
        "pools_seen": len(arrivals),
        "vantage": race_result.get("vantage"),
    }


def build_latest_manifest(race_result: dict) -> dict:
    """Build the latest.json manifest: {height, epoch}."""
    return {
        "height": race_result.get("block_height"),
        "epoch": race_result.get("first_epoch"),
    }
