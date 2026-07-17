"""StratumRace standalone mode — single-process orchestration.

Ties together the HTTP server, collector, and aggregator into one asyncio
process. Handles startup validation, collector supervision, status reporting,
and graceful shutdown.
"""

import argparse
import asyncio
import json
import logging
import signal
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from lib.local_store import LocalStorage
from standalone.aggregator import aggregator_loop
from standalone.server import StandaloneServer

logger = logging.getLogger("stratumrace")

# Collector supervision constants
COLLECTOR_RESTART_BASE_S = 5
COLLECTOR_MAX_RESTARTS = 5
COLLECTOR_STABLE_S = 60  # If runs longer than this, reset restart counter

# Startup connection window
CONNECTION_WINDOW_S = 60

# Shutdown grace period
SHUTDOWN_GRACE_S = 30


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments for the standalone server."""
    parser = argparse.ArgumentParser(
        prog="stratumrace",
        description="StratumRace standalone mode — measure Bitcoin pool block notification speed locally.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    parser.add_argument("--data-dir", default="./data", help="Data directory path (default: ./data)")
    parser.add_argument("--pools", default="config/pools.json", help="Path to pools.json (default: config/pools.json)")
    parser.add_argument("--pool-group", default="all", help="Pool group filter (default: all)")
    parser.add_argument("--vantage", default="local", help="Vantage point label (default: local)")
    parser.add_argument("--frontend-dir", default=None, help="Path to built frontend (default: auto-detect)")
    return parser.parse_args(argv)


def validate_config(args: argparse.Namespace) -> list:
    """Validate configuration and return pool configs.

    Exits non-zero with clear message on:
    - Missing pools file
    - Invalid pools JSON
    - Missing required fields
    - Invalid port range
    - Pool group not found
    """
    # Validate port
    if not (1 <= args.port <= 65535):
        print(f"Error: Port must be between 1 and 65535, got {args.port}", file=sys.stderr)
        sys.exit(1)

    # Validate pools file exists
    pools_path = Path(args.pools)
    if not pools_path.exists():
        print(f"Error: Pool configuration file not found: {args.pools}", file=sys.stderr)
        sys.exit(1)

    # Validate pools JSON
    try:
        raw = json.loads(pools_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: Failed to read pool configuration: {e}", file=sys.stderr)
        sys.exit(1)

    # Validate structure
    pools_list = raw.get("pools") if isinstance(raw, dict) else raw
    if not isinstance(pools_list, list) or len(pools_list) == 0:
        print("Error: Pool configuration must contain a non-empty 'pools' array", file=sys.stderr)
        sys.exit(1)

    required_fields = {"name", "display_name", "host", "port", "groups"}
    for i, pool in enumerate(pools_list):
        missing = required_fields - set(pool.keys())
        if missing:
            print(f"Error: Pool entry {i + 1} missing required fields: {', '.join(sorted(missing))}", file=sys.stderr)
            sys.exit(1)

    # Filter by pool group
    if args.pool_group != "all":
        filtered = [p for p in pools_list if args.pool_group in p.get("groups", [])]
        if not filtered:
            available = sorted(set(g for p in pools_list for g in p.get("groups", [])))
            print(
                f"Error: No pools match group filter '{args.pool_group}'. "
                f"Available groups: {', '.join(available)}",
                file=sys.stderr,
            )
            sys.exit(1)
        pools_list = filtered

    return pools_list


def find_frontend_dir(args: argparse.Namespace) -> Path:
    """Find the built frontend directory."""
    if args.frontend_dir:
        return Path(args.frontend_dir)
    # Auto-detect: look relative to the script location
    candidates = [
        Path(__file__).parent.parent / "frontend" / "dist",
        Path("frontend") / "dist",
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "index.html").exists():
            return candidate
    # Return a non-existent path — server will still work without SPA
    return Path("frontend/dist")


async def supervisor(
    args: argparse.Namespace,
    pools_list: list,
    server: StandaloneServer,
    storage: LocalStorage,
    stop_event: asyncio.Event,
    aggregation_trigger: asyncio.Event | None = None,
) -> None:
    """Supervise the collector task with exponential backoff restart."""
    # Import collector
    collector_path = str(Path(__file__).parent.parent / "collector")
    if collector_path not in sys.path:
        sys.path.insert(0, collector_path)

    from str_race import run as collector_run, generate_throwaway_stratum_user

    # Build collector args
    collector_args = argparse.Namespace(
        user=generate_throwaway_stratum_user(),
        duration=0,
        baseline_timeout=30.0,
        pools=None,
        pool_config=args.pools,
        pool_group=args.pool_group,
        vantage=args.vantage,
        local_dir=None,  # Server handles storage via sink
        post_url=None,
        api_key=None,
        tag_block_miners=True,
        json_out=None,
        csv_out=None,
        race_limit=0,
        verbose=False,
        full_timing=False,
        debug=False,
    )

    # Create the race sink from the server
    race_sink = server.make_race_sink(storage, aggregation_trigger=aggregation_trigger)

    restart_count = 0
    backoff = COLLECTOR_RESTART_BASE_S

    while not stop_event.is_set() and restart_count < COLLECTOR_MAX_RESTARTS:
        start_time = _time.monotonic()
        try:
            logger.info("Starting collector (attempt %d)", restart_count + 1)
            await collector_run(collector_args, race_sink=race_sink, stop_event=stop_event)
            if stop_event.is_set():
                return  # Clean shutdown
        except Exception as e:
            elapsed = _time.monotonic() - start_time
            if elapsed >= COLLECTOR_STABLE_S:
                # Ran long enough — reset restart counter
                restart_count = 0
                backoff = COLLECTOR_RESTART_BASE_S
                logger.warning("Collector exited after %ds, restarting (counter reset): %s", int(elapsed), e)
            else:
                restart_count += 1
                logger.error(
                    "Collector failed after %ds (attempt %d/%d): %s",
                    int(elapsed), restart_count, COLLECTOR_MAX_RESTARTS, e,
                )

            if restart_count >= COLLECTOR_MAX_RESTARTS:
                logger.error(
                    "Collector has failed permanently after %d attempts. "
                    "Server will continue serving existing data.",
                    COLLECTOR_MAX_RESTARTS,
                )
                return

            if not stop_event.is_set():
                logger.info("Restarting collector in %ds...", backoff)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                    return  # Stop signaled during backoff
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 160)  # Double up to a cap


async def startup_status(pools_list: list, stop_event: asyncio.Event) -> None:
    """After the 60s connection window, print connection status."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=CONNECTION_WINDOW_S)
        return  # Stopped before window expired
    except asyncio.TimeoutError:
        pass

    # Print pool count (full pool-state integration comes when the collector
    # exposes its connection map)
    pool_count = len(pools_list)
    logger.info("Ready — monitoring %d pool(s)", pool_count)
    print(f"\n✓ StratumRace is ready — monitoring {pool_count} pool(s)", flush=True)


async def run_standalone(args: argparse.Namespace) -> None:
    """Main async entry point — orchestrates all components."""
    # Validate configuration
    pools_list = validate_config(args)

    # Setup storage
    data_dir = Path(args.data_dir)
    storage = LocalStorage(data_dir)
    storage.ensure_initial_files()

    # Write pools.json to api/config/
    pools_config_path = data_dir / "api" / "config" / "pools.json"
    pools_raw = json.loads(Path(args.pools).read_text(encoding="utf-8"))
    pools_config_path.parent.mkdir(parents=True, exist_ok=True)
    pools_config_path.write_text(json.dumps(pools_raw), encoding="utf-8")

    # Write initial vantages.json so the frontend Vantages panel shows the local vantage
    vantages_status_path = data_dir / "api" / "status" / "vantages.json"
    vantages_status_path.parent.mkdir(parents=True, exist_ok=True)
    vantages_status = {
        "vantages": {
            args.vantage: {
                "status": "online",
                "last_heartbeat_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_race_utc": None,
                "connected_pools": len(pools_list),
                "eligible_pools": len(pools_list),
            }
        }
    }
    vantages_status_path.write_text(json.dumps(vantages_status), encoding="utf-8")

    # Find frontend
    frontend_dir = find_frontend_dir(args)

    # Create server
    server = StandaloneServer(
        data_dir=data_dir,
        frontend_dir=frontend_dir,
        host=args.host,
        port=args.port,
        vantage=args.vantage,
    )

    # Stop event (shared across all components)
    stop_event = asyncio.Event()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    # Print startup banner
    url = f"http://{args.host}:{args.port}" if args.host != "0.0.0.0" else f"http://localhost:{args.port}"
    print(f"\n{'=' * 50}", flush=True)
    print("  StratumRace Standalone", flush=True)
    print(f"  UI: {url}", flush=True)
    print(f"  Pools: {len(pools_list)} ({args.pool_group} group)", flush=True)
    if args.host != "127.0.0.1":
        print(f"  ⚠ Bound to {args.host} — dashboard is reachable by any", flush=True)
        print(f"    host that can reach port {args.port}.", flush=True)
        print("    Use --host 127.0.0.1 for local-only access.", flush=True)
    print(f"{'=' * 50}\n", flush=True)

    # Launch background tasks
    aggregation_trigger = asyncio.Event()
    aggregator_task = asyncio.create_task(aggregator_loop(storage, stop_event, trigger=aggregation_trigger))
    supervisor_task = asyncio.create_task(supervisor(args, pools_list, server, storage, stop_event, aggregation_trigger=aggregation_trigger))
    status_task = asyncio.create_task(startup_status(pools_list, stop_event))

    # Run the HTTP server (blocks until stop)
    try:
        await server.serve()
    except asyncio.CancelledError:
        pass
    finally:
        # Graceful shutdown
        stop_event.set()

        # Wait for collector to finalize (up to SHUTDOWN_GRACE_S)
        _, pending = await asyncio.wait(
            [supervisor_task, aggregator_task, status_task],
            timeout=SHUTDOWN_GRACE_S,
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(supervisor_task, aggregator_task, status_task, return_exceptions=True)

        # Close WebSocket connections
        for client in list(server.ws_clients):
            try:
                await client.close()
            except Exception:
                pass

        print("\nStratumRace stopped.", flush=True)


def main(argv=None) -> None:
    """CLI entry point."""
    # Configure logging to stdout/stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    args = parse_args(argv)

    try:
        asyncio.run(run_standalone(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
