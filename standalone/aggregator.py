"""Standalone background aggregator.

Runs as an asyncio task within the standalone server process.
Calls run_all_aggregations every 5 minutes to produce daily, monthly,
recent-10/50, and last-24h/7d aggregate files.
"""

import asyncio
import logging
from datetime import datetime, timezone

from lib.aggregation import run_all_aggregations
from lib.local_store import LocalStorage

logger = logging.getLogger(__name__)

# First aggregation cycle runs within 30 seconds of startup (R5.1)
STARTUP_DELAY_S = 10  # Run fairly quickly after start (well within 30s requirement)
CYCLE_INTERVAL_S = 300  # Every 5 minutes


async def aggregator_loop(
    storage: LocalStorage,
    stop: asyncio.Event,
    trigger: asyncio.Event | None = None,
) -> None:
    """Background aggregation loop.

    Runs the first cycle within 30s of startup, then every 5 minutes.
    If a trigger event is provided, an aggregation cycle also runs
    shortly after the trigger fires (e.g., when a new race is confirmed).
    Never lets an exception kill the loop (logs and continues).
    """
    # Initial delay before first cycle (within 30s requirement)
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_DELAY_S)
        return  # Stop was signaled during startup delay
    except asyncio.TimeoutError:
        pass  # Normal — startup delay elapsed, proceed

    while not stop.is_set():
        try:
            now = datetime.now(timezone.utc)
            logger.info("Aggregation cycle starting at %s", now.strftime("%H:%M:%S"))
            run_all_aggregations(storage, now)
            logger.info("Aggregation cycle complete")
        except Exception:
            logger.exception("Aggregation cycle failed — will retry next cycle")

        # Clear the trigger so we can detect new races during the wait
        if trigger is not None:
            trigger.clear()

        # Wait for either: stop signal, trigger (new race), or 5-min timeout
        try:
            if trigger is not None:
                # Wait for whichever comes first: stop, trigger, or timeout
                done, _ = await asyncio.wait(
                    [
                        asyncio.create_task(stop.wait()),
                        asyncio.create_task(trigger.wait()),
                    ],
                    timeout=CYCLE_INTERVAL_S,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Cancel pending tasks from the wait set
                for task in _:
                    task.cancel()
                if stop.is_set():
                    return
            else:
                await asyncio.wait_for(stop.wait(), timeout=CYCLE_INTERVAL_S)
                return  # Stop was signaled
        except asyncio.TimeoutError:
            pass  # Normal — interval elapsed, run next cycle
