#!/usr/bin/env python3
"""Lightweight pass-through stratum proxy that timestamps mining.notify.

Point a real miner at this proxy and it relays every byte unmodified to the
upstream pool, recording (a) when each mining.notify arrived and (b) whether
the pool accepted the miner's shares. Because the connection is doing real
work, pools that deprioritize idle connections serve it in their fast
tranche — giving honest "active" timings that can be compared per block
against str_race.py's idle listeners running on the same machine.

Modes:
  Rotate (default): one listen port; the upstream pool advances through the
  pools file after --races-per-pool observed blocks (or --max-minutes). On
  each switch the miner is disconnected and renegotiates cleanly with the
  next pool when it auto-reconnects.

      python3 stratum_proxy.py --pools ../pools/web-pools.json --listen 3333

  Park: pin a single pool forever (for pools certified as tranching).

      python3 stratum_proxy.py --pools ../pools/web-pools.json --listen 3401 --pool ocean

Events are appended as JSON lines to --out-dir/active-<pool>.jsonl:
  {"t": epoch_seconds, "utc": ..., "pool": ..., "prevhash": ..., "clean": bool,
   "empty": bool}                          # one per mining.notify
  {"t": ..., "pool": ..., "share": "accepted"|"rejected"}   # one per submit reply

Stdlib only. The prevhash transform matches str_race.py so events join
against idle races by block hash.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

CONNECT_TIMEOUT = 15.0
IDLE_TIMEOUT = 180.0  # no upstream traffic for this long -> drop and renegotiate
DEFAULT_RACES_PER_POOL = 20
DEFAULT_MAX_MINUTES = 300.0


def utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def stratum_prevhash_to_blockhash(ph_hex: str) -> str:
    """Same byte transform as str_race.py: stratum prevhash -> explorer hash."""
    if len(ph_hex) != 64:
        raise ValueError("prevhash must be 64 hex chars")
    words = [ph_hex[i : i + 8] for i in range(0, 64, 8)]
    swapped = b"".join(bytes.fromhex(w)[::-1] for w in words)
    return swapped[::-1].hex()


class EventWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)

    def write(self, pool: str, event: Dict[str, Any]) -> None:
        event = {"t": time.time(), "utc": utc_iso(), "pool": pool, **event}
        path = self.out_dir / f"active-{pool}.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(event, separators=(",", ":")) + "\n")


class PoolSession:
    """One miner<->pool relay session. Counts new-prevhash blocks observed."""

    def __init__(self, pool: Dict[str, Any], writer: EventWriter) -> None:
        self.pool = pool
        self.writer = writer
        self.blocks_seen = 0
        self.accepted = 0
        self.rejected = 0
        self.last_prevhash: Optional[str] = None
        self.submit_ids: set = set()

    def _tap_upstream_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(msg, dict):
            return
        name = self.pool["name"]

        if msg.get("method") == "mining.notify":
            params = msg.get("params", [])
            if len(params) < 2 or not isinstance(params[1], str):
                return
            try:
                prevhash = stratum_prevhash_to_blockhash(params[1])
            except ValueError:
                return
            clean = bool(params[8]) if len(params) > 8 else False
            merkle = params[4] if len(params) > 4 else None
            empty = isinstance(merkle, list) and len(merkle) == 0
            # A "new block" is a clean-jobs prevhash change after baseline —
            # the only notifies that are valid race arrivals. The first job at
            # connect references a minutes-old block and must never be joined.
            new_block = (
                clean
                and self.last_prevhash is not None
                and prevhash != self.last_prevhash
            )
            self.writer.write(
                name,
                {"prevhash": prevhash, "clean": clean, "empty": empty, "new_block": new_block},
            )
            if clean and prevhash != self.last_prevhash:
                if self.last_prevhash is not None:
                    self.blocks_seen += 1
                    log(f"{name}: block #{self.blocks_seen} {prevhash[:16]}…")
                self.last_prevhash = prevhash
            return

        # Reply to one of the miner's submits.
        mid = msg.get("id")
        if mid is not None and mid in self.submit_ids:
            self.submit_ids.discard(mid)
            ok = msg.get("result") is True and msg.get("error") is None
            if ok:
                self.accepted += 1
            else:
                self.rejected += 1
            self.writer.write(self.pool["name"], {"share": "accepted" if ok else "rejected"})

    def _tap_miner_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(msg, dict) and msg.get("method") == "mining.submit":
            mid = msg.get("id")
            if mid is not None:
                self.submit_ids.add(mid)

    async def run(
        self,
        miner_r: asyncio.StreamReader,
        miner_w: asyncio.StreamWriter,
        stop_after_blocks: Optional[int],
        deadline: Optional[float],
        writer_registry: Optional[List[asyncio.StreamWriter]] = None,
    ) -> str:
        """Relay until a side disconnects, the block quota is met, or the
        deadline passes. Returns the reason the session ended."""
        name, host, port = self.pool["name"], self.pool["host"], self.pool["port"]
        try:
            pool_r, pool_w = await asyncio.wait_for(
                asyncio.open_connection(host, port), CONNECT_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError) as exc:
            log(f"{name}: upstream connect failed ({exc})")
            return "connect_failed"
        if writer_registry is not None:
            writer_registry.append(pool_w)
        log(f"{name}: relaying miner <-> {host}:{port}")

        reason = "closed"
        last_activity = time.monotonic()

        async def pump(reader, writer, tap) -> str:
            nonlocal last_activity
            buf = b""
            while True:
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    return "deadline"
                # Session-level idleness: traffic in EITHER direction keeps the
                # session alive, so a quiet pool doesn't kill a working miner
                # (and vice versa). Only a fully silent session is reaped.
                if now - last_activity > IDLE_TIMEOUT:
                    return "timeout"
                try:
                    data = await asyncio.wait_for(reader.read(65536), 15.0)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    return "closed"
                last_activity = time.monotonic()
                writer.write(data)
                await writer.drain()
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        tap(line)
                if stop_after_blocks and self.blocks_seen >= stop_after_blocks:
                    return "quota"

        up_task = asyncio.create_task(pump(pool_r, miner_w, self._tap_upstream_line))
        down_task = asyncio.create_task(pump(miner_r, pool_w, self._tap_miner_line))
        done, pending = await asyncio.wait(
            {up_task, down_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for t in done:
            exc = t.exception()
            if exc:
                log(f"{name}: relay error: {exc!r}")
                reason = "error"
            else:
                reason = t.result()

        for w in (pool_w, miner_w):
            try:
                w.close()
            except Exception:
                pass
        log(
            f"{name}: session ended ({reason}) — blocks {self.blocks_seen}, "
            f"shares {self.accepted} accepted / {self.rejected} rejected"
        )
        self.writer.write(
            name,
            {
                "session_end": reason,
                "blocks_seen": self.blocks_seen,
                "shares_accepted": self.accepted,
                "shares_rejected": self.rejected,
            },
        )
        return reason


class Rotation:
    def __init__(self, pools: List[Dict[str, Any]], state_file: Path) -> None:
        self.pools = pools
        self.state_file = state_file
        self.index = 0
        if state_file.exists():
            try:
                self.index = int(state_file.read_text().strip()) % len(pools)
            except ValueError:
                pass
        # Carry-over per-pool progress within the current rotation stop.
        self.blocks_done = 0
        self.dead_sessions = 0  # consecutive sessions with no blocks and no shares

    def current(self) -> Dict[str, Any]:
        return self.pools[self.index]

    def advance(self) -> None:
        self.index = (self.index + 1) % len(self.pools)
        self.blocks_done = 0
        self.dead_sessions = 0
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(str(self.index) + "\n")
        log(f"rotation: next pool -> {self.current()['name']}")


async def serve(args: argparse.Namespace) -> None:
    pools = json.loads(Path(args.pools).read_text())
    writer = EventWriter(Path(args.out_dir))

    if args.pool:
        matches = [p for p in pools if p["name"] == args.pool]
        if not matches:
            sys.exit(f"pool {args.pool!r} not found in {args.pools}")
        rotation = None
        parked = matches[0]
        log(f"park mode: {parked['name']} ({parked['host']}:{parked['port']})")
    else:
        rotation = Rotation(pools, Path(args.state_file))
        parked = None
        log(
            f"rotate mode: {len(pools)} pools, {args.races_per_pool} blocks or "
            f"{args.max_minutes:g} min per pool; starting at {rotation.current()['name']}"
        )

    lock = asyncio.Lock()  # one miner session at a time
    current_writers: List[asyncio.StreamWriter] = []

    async def handle(miner_r: asyncio.StreamReader, miner_w: asyncio.StreamWriter) -> None:
        # ASIC firmwares commonly reconnect before closing the old socket, or
        # probe with a second connection. The newest connection wins: kick the
        # active session and take its slot.
        if lock.locked():
            log("new miner connection — replacing the active session")
            for w in current_writers:
                try:
                    w.close()
                except Exception:
                    pass
        async with lock:
            current_writers.clear()
            current_writers.append(miner_w)
            pool = parked if parked else rotation.current()
            session = PoolSession(pool, writer)
            if rotation:
                # Capture the block count at the START of this session so we
                # can later determine how many blocks arrived *during* this
                # session vs. carried over from prior sessions.
                session_start_blocks = rotation.blocks_done
                session.blocks_seen = rotation.blocks_done
                deadline = time.monotonic() + args.max_minutes * 60.0
                reason = await session.run(
                    miner_r, miner_w, args.races_per_pool, deadline,
                    writer_registry=current_writers,
                )
                # How many blocks were observed in THIS session only
                # (not carried over from prior sessions).
                new_blocks_this_session = session.blocks_seen - session_start_blocks

                if reason in ("quota", "deadline"):
                    rotation.blocks_done = session.blocks_seen
                    rotation.advance()
                elif reason in ("timeout", "connect_failed", "error"):
                    # Dead check: this session produced zero blocks AND zero
                    # accepted shares — the pool was truly unresponsive during
                    # this connection attempt.
                    dead = session.accepted == 0 and new_blocks_this_session == 0
                    if session.accepted > 0 or new_blocks_this_session > 0:
                        rotation.dead_sessions = 0
                    elif dead:
                        rotation.dead_sessions += 1
                        log(
                            f"{pool['name']}: dead session "
                            f"{rotation.dead_sessions}/3 before skipping"
                        )
                    if rotation.dead_sessions >= 3:
                        log(f"{pool['name']}: unresponsive across 3 sessions — moving on")
                        rotation.advance()
                    else:
                        # Update blocks_done AFTER the dead check to avoid
                        # the tautology where blocks_done == blocks_seen
                        # because we just set it.
                        rotation.blocks_done = session.blocks_seen
                else:
                    # miner-driven disconnect/takeover: same pool continues
                    if session.accepted > 0 or new_blocks_this_session > 0:
                        rotation.dead_sessions = 0
                    rotation.blocks_done = session.blocks_seen
            else:
                await session.run(
                    miner_r, miner_w, None, None, writer_registry=current_writers
                )

    server = await asyncio.start_server(handle, args.bind, args.listen)
    log(f"listening on {args.bind}:{args.listen} — point your miner at stratum+tcp://<this-ip>:{args.listen}")
    async with server:
        await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="Pass-through stratum proxy with notify timestamping")
    ap.add_argument("--pools", required=True, help="pools JSON file (name/host/port)")
    ap.add_argument("--listen", type=int, default=3333, help="local port for the miner")
    ap.add_argument("--bind", default="0.0.0.0", help="listen address")
    ap.add_argument("--pool", help="park mode: pin this pool instead of rotating")
    ap.add_argument("--races-per-pool", type=int, default=DEFAULT_RACES_PER_POOL,
                    help="rotate after this many observed blocks (default 20)")
    ap.add_argument("--max-minutes", type=float, default=DEFAULT_MAX_MINUTES,
                    help="rotate after this long even if the block quota isn't met")
    ap.add_argument("--out-dir", default="/var/lib/stratum-race/active",
                    help="directory for active-<pool>.jsonl event logs")
    ap.add_argument("--state-file", default="/var/lib/stratum-race/proxy-rotation.state",
                    help="rotation position, survives restarts")
    args = ap.parse_args()
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
