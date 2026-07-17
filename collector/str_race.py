#!/usr/bin/env python3

"""
Stratum Prevhash Race Timer
Credit: @proofofmike / ProofOfMike.com  and  @AtlasPool_io / AtlasPool.io

Async Stratum prevhash race timer for comparing solo mining pool notify timing
from one client vantage point.

Measures which solo mining pool announces a new prevhash first.

Timing model:
  - Single asyncio event loop.
  - Timestamp is taken immediately after readline() returns.
  - Race signal is mining.notify where clean_jobs is true and prevhash changed.
  - First notify after connect/reconnect is a baseline only, even when clean=true.

Important accounting model:
  - Reconnects are counted in one centralized path.
  - Read timeouts and remote closes are reconnect events.
  - Connect failures/timeouts are connection failures, not established-session closes.
  - A pool can match a race after reconnect, but cannot start a new race until it has
    re-synced by matching a real confirmed race.

Methodology note:
  - A "win" means this client observed that pool first from this network/location.
  - It is not proof that the pool globally won block-template propagation.
"""

import argparse
import asyncio
import csv
import json
import os
import platform
import secrets
import signal
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


DEFAULT_POOLS: List[Tuple[str, str, int]] = [
    ("ckpool",       "solo.ckpool.org",            3333),
    ("atlaspool",    "solo.atlaspool.io",          3333),
    ("public_pool",  "public-pool.io",             3333),
    ("solofury",     "btc.solofury.com",           6060),
    ("solo_cat",     "solo.cat",                   3333),
    ("helios",       "btc.heliospool.com",         3333),
    ("solopool_com", "stratum.solopool.com",       3333),
    ("us_solohash",  "solo-ca.solohash.co.uk",     3333),
    ("braiins_solo", "solo.stratum.braiins.com",   3333),
]

CONFIRM_WINDOW = 15.0
RECONNECT_DELAY = 10.0
WARMUP_AFTER_CONSENSUS = 10.0
CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 180.0
MIN_DIRECTIONAL_RACES = 20
SHUTDOWN_GRACE = 2.0
SHUTDOWN_DRAIN_GRACE = 45.0  # Max wait for in-flight enrichment/POST tasks at shutdown
                             # (must stay under systemd's TimeoutStopSec, default 90s)
DEFAULT_BASELINE_TIMEOUT = 120.0
BLOCK_MINER_LOOKUP_TIMEOUT = 8.0
MEMPOOL_API_BASE = "https://mempool.space/api"

CLIENT_VERSION = "stratum-race/0.5"

# POST retry constants
POST_TIMEOUT = 5.0           # Must complete within 5 seconds of race confirmation
POST_MAX_RETRIES = 3         # 3 attempts total (initial + 2 retries)
POST_BACKOFF_BASE = 1.0      # 1s, 2s, 4s exponential backoff
DEAD_LETTER_DIR = Path.home() / ".str_race_deadletter"


class ReconnectSession(Exception):
    """Raised internally when an established connection should reconnect."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def wall_clock() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def loop_time() -> float:
    return asyncio.get_running_loop().time()


def ms(seconds: float) -> float:
    return round(seconds * 1000.0, 3)


def _print(pool_name: str, msg: str) -> None:
    print(f"[{wall_clock()}] {pool_name:<12} {msg}", flush=True)


def pct(values: Sequence[float], percentile: float) -> Optional[float]:
    """Nearest-rank percentile for small sample sizes."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = round((percentile / 100.0) * (len(ordered) - 1))
    return ordered[int(rank)]


def fnum(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def stratum_prevhash_to_blockhash(stratum_hex: str) -> str:
    """Convert a stratum mining.notify prevhash into the canonical display block
    hash used by explorers / mempool.space.

    The stratum prevhash is byte-mangled: each 4-byte word is reversed, and the
    whole 32-byte value is in internal (little-endian) order. To get the display
    hash we reverse each 4-byte word, then reverse the full 32 bytes.

    Verified against the genesis block in the test suite. All compliant pools use
    the same encoding, which is why cross-pool matching works on the raw value;
    that raw value is NOT the explorer hash, so it must be transformed before any
    block lookup.
    """
    h = stratum_hex.strip().lower()
    if len(h) != 64:
        raise ValueError(f"prevhash must be 64 hex chars, got {len(h)}")
    raw = bytes.fromhex(h)
    word_swapped = b"".join(raw[i:i + 4][::-1] for i in range(0, 32, 4))
    return word_swapped[::-1].hex()


def short_hash(h: Optional[str]) -> str:
    """Distinguishing short form for logs. Real block hashes lead with ~18 zero
    chars, so the leading slice is useless; the tail is what differs."""
    if not h:
        return "?"
    return "\u2026" + h[-12:]


BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values: Sequence[int]) -> int:
    generator = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= generator[i]
    return chk


def _bech32_hrp_expand(hrp: str) -> List[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_create_checksum(hrp: str, data: Sequence[int]) -> List[int]:
    values = _bech32_hrp_expand(hrp) + list(data)
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _bech32_encode(hrp: str, data: Sequence[int]) -> str:
    combined = list(data) + _bech32_create_checksum(hrp, data)
    return hrp + "1" + "".join(BECH32_CHARSET[d] for d in combined)


def _convertbits(data: bytes, from_bits: int, to_bits: int, pad: bool = True) -> List[int]:
    acc = 0
    bits = 0
    ret: List[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise ValueError("invalid value while converting bits")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise ValueError("invalid padding while converting bits")
    return ret


def generate_throwaway_stratum_user() -> str:
    """Generate a valid random mainnet bech32 P2WPKH address for test auth only."""
    witness_program = secrets.token_bytes(20)
    address = _bech32_encode("bc", [0] + _convertbits(witness_program, 8, 5))
    return f"{address}.race"


def format_stratum_error(err: Any) -> str:
    """Stratum errors are usually [code, message, traceback] or a string."""
    if isinstance(err, list) and len(err) >= 2:
        return f"{err[1]} (code {err[0]})"
    return str(err)


@dataclass
class PoolConfig:
    name: str
    host: str
    port: int


@dataclass
class PoolState:
    name: str
    host: str
    port: int
    user: str

    connected: bool = False
    current_prevhash: Optional[str] = None
    eligible: bool = False

    # Baseline gating.
    excluded_at_baseline: bool = False
    exclude_reason: Optional[str] = None

    # Auth/subscribe health.
    auth_failed: bool = False
    auth_error: Optional[str] = None
    subscribe_failed: bool = False
    subscribe_error: Optional[str] = None

    # Race results. These are confirmed-race arrivals only.
    wins: int = 0
    losses: int = 0
    seen: int = 0
    missed: int = 0
    delays: List[float] = field(default_factory=list)         # non-winner (chase) delays only
    all_arrival_offsets: List[float] = field(default_factory=list)  # includes winner 0.0 and losses

    # Race/state anomalies.
    unmatched: int = 0          # observed notifies that never matched a second pool
    stale_repeats: int = 0      # clean=true prevhash already seen/closed
    unstable: int = 0           # clean=true prevhash change while not eligible to start

    # Connection health.
    connect_attempts: int = 0
    connections: int = 0
    reconnects: int = 0         # established session ended and will reconnect
    read_timeouts: int = 0
    remote_closes: int = 0
    connect_timeouts: int = 0
    connect_errors: int = 0
    other_disconnects: int = 0

    # Session timing.
    connected_at_wall: Optional[str] = None
    first_notify_at_wall: Optional[str] = None
    last_notify_at_wall: Optional[str] = None

    # Notify accounting.
    notify_total: int = 0
    clean_true: int = 0
    clean_false: int = 0
    noise_repeats: int = 0      # clean=false same-prevhash refreshes
    noise_prevhash_changes: int = 0
    parse_errors: int = 0
    bad_notify: int = 0
    empty_notifies: int = 0     # notifies whose template had no transactions

    def record_reconnect(self, reason: str) -> None:
        """Count an established-session reconnect in one place."""
        self.reconnects += 1

        if reason == "read_timeout":
            self.read_timeouts += 1
        elif reason == "remote_closed":
            self.remote_closes += 1
        else:
            self.other_disconnects += 1

    def reset_connection_state(self) -> None:
        self.connected = False
        self.current_prevhash = None
        self.eligible = False
        self.connected_at_wall = None
        self.first_notify_at_wall = None


@dataclass
class Race:
    index: int
    prevhash: str
    first_pool: str
    first_ts: float
    first_wall: str
    first_epoch: float
    first_utc: str
    eligible_at_start: Set[str]
    arrivals: Dict[str, float] = field(default_factory=dict)
    arrival_wall: Dict[str, str] = field(default_factory=dict)
    # First arrival per pool that carried a non-empty template. Pools that led
    # with an empty template appear here only once their full template lands
    # within the confirm window.
    nonempty_arrivals: Dict[str, float] = field(default_factory=dict)
    empty_first: Set[str] = field(default_factory=set)  # pools whose first notify was empty
    confirmed: bool = False
    closed: bool = False
    counted: Set[str] = field(default_factory=set)
    missed_counted: bool = False

    # Optional post-run enrichment only. These fields are never populated from
    # the live timing path.
    block_height: Optional[int] = None
    block_miner: Optional[str] = None
    block_miner_source: Optional[str] = None

    def arrival_offsets_ms(self) -> Dict[str, float]:
        return {
            pool_name: ms(arrival_ts - self.first_ts)
            for pool_name, arrival_ts in self.arrivals.items()
        }

    def nonempty_arrival_offsets_ms(self) -> Dict[str, float]:
        if not self.nonempty_arrivals:
            return {}
        base = min(self.nonempty_arrivals.values())
        return {
            pool_name: ms(arrival_ts - base)
            for pool_name, arrival_ts in self.nonempty_arrivals.items()
        }

    def nonempty_winner(self) -> Optional[str]:
        if not self.nonempty_arrivals:
            return None
        return min(self.nonempty_arrivals, key=self.nonempty_arrivals.get)

    def missed_pools(self) -> List[str]:
        return sorted(self.eligible_at_start - set(self.arrivals))


class RaceTracker:
    def __init__(self, baseline_timeout: float = DEFAULT_BASELINE_TIMEOUT) -> None:
        self.active: Dict[str, Race] = {}
        self.all_races: List[Race] = []
        self.seen_prevhashes: Set[str] = set()

        self.consensus_prevhash: Optional[str] = None
        self.consensus_ts: Optional[float] = None
        self.tracking_enabled: bool = False

        self.baseline_timeout: float = baseline_timeout
        self.baseline_started_ts: Optional[float] = None
        self.baseline_via_quorum: bool = False

        self.last_wait_print: float = 0.0

    def _count_arrival(self, race: Race, pool_name: str, pools: Dict[str, PoolState]) -> None:
        if pool_name in race.counted:
            return

        p = pools[pool_name]
        p.seen += 1
        offset = ms(race.arrivals[pool_name] - race.first_ts)
        p.all_arrival_offsets.append(offset)

        if pool_name == race.first_pool:
            p.wins += 1
        else:
            p.losses += 1
            p.delays.append(offset)

        race.counted.add(pool_name)

    def _count_misses(self, race: Race, pools: Dict[str, PoolState]) -> None:
        if race.missed_counted:
            return

        if not race.confirmed:
            return

        for pool_name in race.missed_pools():
            pools[pool_name].missed += 1

        race.missed_counted = True

    def cleanup_races(self, pools: Dict[str, PoolState], force: bool = False) -> List[Race]:
        now = loop_time()
        just_closed_confirmed: List[Race] = []

        for ph, race in list(self.active.items()):
            if not force and now - race.first_ts <= CONFIRM_WINDOW:
                continue

            if not race.confirmed:
                pools[race.first_pool].unmatched += 1
            else:
                self._count_misses(race, pools)
                just_closed_confirmed.append(race)

            race.closed = True
            del self.active[ph]

        return just_closed_confirmed

    def finalize(self, pools: Dict[str, PoolState]) -> None:
        self.cleanup_races(pools, force=True)

        # Also count misses for confirmed races that were already removed before the
        # final report. This is idempotent because each Race has missed_counted.
        for race in self.all_races:
            self._count_misses(race, pools)

    def _all_have_same_prevhash(self, pools: Dict[str, PoolState]) -> Tuple[bool, Optional[str]]:
        if any(p.current_prevhash is None for p in pools.values()):
            return False, None

        vals = {p.current_prevhash for p in pools.values()}

        if len(vals) != 1:
            return False, None

        return True, next(iter(vals))

    def _consensus_values(self, pools: Dict[str, PoolState]) -> List[str]:
        return sorted({short_hash(p.current_prevhash) for p in pools.values() if p.current_prevhash})

    def _eligible_starters(self, pools: Dict[str, PoolState]) -> Set[str]:
        return {
            name
            for name, p in pools.items()
            if p.eligible and p.current_prevhash == self.consensus_prevhash
        }

    def handle_notify(
        self,
        pool_name: str,
        recv_ts: float,
        prevhash: str,
        clean: bool,
        pools: Dict[str, PoolState],
        empty: bool = False,
    ) -> None:
        pool = pools[pool_name]
        pool.notify_total += 1
        if empty:
            pool.empty_notifies += 1
        pool.last_notify_at_wall = local_iso()
        if pool.first_notify_at_wall is None:
            pool.first_notify_at_wall = pool.last_notify_at_wall

        if clean:
            pool.clean_true += 1
        else:
            pool.clean_false += 1

        old_ph = pool.current_prevhash

        # First notify after connect/reconnect. This establishes only a baseline.
        # Some pools send clean=false for this; that is fine for sync, not a race.
        if old_ph is None:
            pool.current_prevhash = prevhash
            pool.eligible = False
            _print(pool_name, f"baseline {short_hash(prevhash)} clean={clean}")
            return

        # Same prevhash. clean=false is expected template-refresh noise, but a
        # same-prevhash refresh is also where an empty-first pool delivers its
        # full template — record that as the pool's non-empty arrival.
        if prevhash == old_ph:
            race = self.active.get(prevhash)
            if (
                race is not None
                and pool_name in race.arrivals
                and pool_name not in race.nonempty_arrivals
                and not empty
            ):
                race.nonempty_arrivals[pool_name] = recv_ts
                # Console output: show full-template perspective only.
                # If this is the first full template in the race, announce it
                # as the race start. Otherwise show it as a match with offset
                # relative to the first full template.
                if not race.nonempty_arrivals or len(race.nonempty_arrivals) == 1:
                    # This pool delivered the first full template in this race
                    _print(pool_name, f"OBSERVED block start {prevhash} (height resolved post-run)")
                else:
                    base = min(race.nonempty_arrivals.values())
                    delay = ms(recv_ts - base)
                    _print(pool_name, f"match {short_hash(prevhash)} delay={fnum(delay)} ms")
            if not clean:
                pool.noise_repeats += 1
            return

        # Prevhash changed.
        pool.current_prevhash = prevhash

        # clean=false prevhash changes are tracked, but ignored as race signals.
        if not clean:
            pool.noise_prevhash_changes += 1
            _print(pool_name, f"prevhash changed clean=false ignored {short_hash(prevhash)}")
            return

        # From here: clean=true + prevhash changed only.
        if not self.tracking_enabled:
            _print(pool_name, f"baseline {short_hash(prevhash)} clean=true")
            return

        race = self.active.get(prevhash)

        if race is not None:
            if pool_name not in race.arrivals:
                race.arrivals[pool_name] = recv_ts
                race.arrival_wall[pool_name] = local_iso()
                if empty:
                    race.empty_first.add(pool_name)
                else:
                    race.nonempty_arrivals[pool_name] = recv_ts
                # Console output: full-template perspective only.
                # If this pool arrived with an empty template, suppress output
                # (it will print when the full template arrives). If full, show
                # delay relative to the first full template in this race.
                if not empty:
                    if race.nonempty_arrivals and len(race.nonempty_arrivals) > 1:
                        base = min(race.nonempty_arrivals.values())
                        delay = ms(recv_ts - base)
                        _print(pool_name, f"match {short_hash(prevhash)} delay={fnum(delay)} ms")
                    else:
                        # This is the first full template in the race
                        _print(pool_name, f"OBSERVED block start {prevhash} (height resolved post-run)")

                # A reconnecting pool becomes eligible again only after it proves it
                # is synced to a real race.
                pool.eligible = True

                if not race.confirmed and len(race.arrivals) >= 2:
                    race.confirmed = True
                    self.consensus_prevhash = prevhash

                    for arrived_pool in race.arrivals:
                        pools[arrived_pool].eligible = True
                        self._count_arrival(race, arrived_pool, pools)

                elif race.confirmed:
                    self._count_arrival(race, pool_name, pools)

            return

        if prevhash in self.seen_prevhashes:
            pool.stale_repeats += 1
            return

        can_start = (
            pool.eligible
            and self.consensus_prevhash is not None
            and old_ph == self.consensus_prevhash
        )

        if not can_start:
            pool.unstable += 1
            return

        self.seen_prevhashes.add(prevhash)

        eligible_at_start = self._eligible_starters(pools)
        eligible_at_start.add(pool_name)

        race = Race(
            index=len(self.all_races) + 1,
            prevhash=prevhash,
            first_pool=pool_name,
            first_ts=recv_ts,
            first_wall=local_iso(),
            first_epoch=time.time(),
            first_utc=utc_iso(),
            eligible_at_start=eligible_at_start,
        )
        race.arrivals[pool_name] = recv_ts
        race.arrival_wall[pool_name] = race.first_wall
        if empty:
            race.empty_first.add(pool_name)
        else:
            race.nonempty_arrivals[pool_name] = recv_ts

        self.active[prevhash] = race
        self.all_races.append(race)

        # Console output: only announce race start for full templates.
        # If the starter sent an empty template, the announcement is deferred
        # until the first full template arrives (handled above).
        if not empty:
            _print(pool_name, f"OBSERVED block start {prevhash} (height resolved post-run)")

    def _quorum_baseline(
        self, pools: Dict[str, PoolState]
    ) -> Tuple[Optional[str], int, int, List[Tuple[str, str]]]:
        """After the deadline, baseline on the prevhash held by a strict majority
        of responding pools. Returns (modal_hash, modal_count, responding_count,
        excluded[(name, reason)]). modal_hash is None if no quorum yet."""
        responding = [p for p in pools.values() if p.current_prevhash]
        if len(responding) < 2:
            return None, 0, len(responding), []

        counts = Counter(p.current_prevhash for p in responding)
        modal, modal_count = counts.most_common(1)[0]

        if modal_count < 2 or modal_count * 2 <= len(responding):
            return None, modal_count, len(responding), []

        excluded: List[Tuple[str, str]] = []
        for name, p in pools.items():
            if p.current_prevhash != modal:
                reason = (
                    "no baseline before deadline"
                    if p.current_prevhash is None
                    else "diverged from quorum prevhash at baseline"
                )
                excluded.append((name, reason))

        return modal, modal_count, len(responding), excluded

    def _apply_baseline(
        self,
        pools: Dict[str, PoolState],
        candidate: str,
        now: float,
        excluded: List[Tuple[str, str]],
        via_quorum: bool,
        modal_count: int = 0,
        responding: int = 0,
    ) -> None:
        self.consensus_prevhash = candidate
        self.consensus_ts = now
        self.baseline_via_quorum = via_quorum

        for p in pools.values():
            p.excluded_at_baseline = False
            p.exclude_reason = None
        for name, reason in excluded:
            pools[name].excluded_at_baseline = True
            pools[name].exclude_reason = reason

        if via_quorum:
            ex_names = ", ".join(name for name, _ in excluded) or "none"
            print(
                f"\n--- QUORUM BASELINE ON {candidate} "
                f"({modal_count}/{responding} responding pools agreed) ---",
                flush=True,
            )
            print(f"--- EXCLUDED AT BASELINE: {ex_names} ---\n", flush=True)
        else:
            print(
                f"\n--- ALL POOLS BASELINED ON SAME PREVHASH {candidate} ---\n",
                flush=True,
            )

    def check_consensus(self, pools: Dict[str, PoolState]) -> None:
        if self.tracking_enabled:
            return

        now = loop_time()
        if self.baseline_started_ts is None:
            self.baseline_started_ts = now

        # Establish the baseline once. Full consensus wins immediately; otherwise
        # fall back to a majority quorum once the deadline passes.
        if self.consensus_prevhash is None:
            ok, ph = self._all_have_same_prevhash(pools)
            deadline_passed = (now - self.baseline_started_ts) >= self.baseline_timeout

            if ok:
                self._apply_baseline(pools, ph, now, excluded=[], via_quorum=False)
            elif deadline_passed:
                modal, modal_count, responding, excluded = self._quorum_baseline(pools)
                if modal is not None:
                    self._apply_baseline(
                        pools, modal, now, excluded=excluded, via_quorum=True,
                        modal_count=modal_count, responding=responding,
                    )
                else:
                    self._print_wait(pools, now, deadline_passed=True)
            else:
                self._print_wait(pools, now, deadline_passed=False)

        if self.consensus_prevhash is not None and self.consensus_ts is not None and (
            now - self.consensus_ts >= WARMUP_AFTER_CONSENSUS
        ):
            self.tracking_enabled = True

            # --- Warmup deadlock fix ---
            # If blocks arrived during the warmup period, pools may have moved
            # past the original consensus_prevhash. Update consensus to reflect
            # the most common current prevhash among connected (non-excluded) pools
            # so that eligibility checks don't permanently stall.
            active_hashes = [
                p.current_prevhash
                for p in pools.values()
                if p.current_prevhash is not None and not p.excluded_at_baseline
            ]
            if active_hashes:
                hash_counts = Counter(active_hashes)
                most_common_hash, _ = hash_counts.most_common(1)[0]
                if most_common_hash != self.consensus_prevhash:
                    print(
                        f"\n--- WARMUP FIX: consensus updated from "
                        f"{short_hash(self.consensus_prevhash)} to "
                        f"{short_hash(most_common_hash)} (block arrived during warmup) ---",
                        flush=True,
                    )
                    self.consensus_prevhash = most_common_hash

            self.seen_prevhashes.add(self.consensus_prevhash)

            for name, p in pools.items():
                if not p.excluded_at_baseline:
                    if p.current_prevhash == self.consensus_prevhash:
                        p.eligible = True
                    else:
                        p.eligible = False

            print("\n--- TRACKING STARTED ---", flush=True)
            excluded_now = sorted(name for name, p in pools.items() if p.excluded_at_baseline)
            if excluded_now:
                print(f"--- NOT IN RACE (excluded at baseline): {excluded_now} ---", flush=True)
            print("", flush=True)

    def _print_wait(self, pools: Dict[str, PoolState], now: float, deadline_passed: bool) -> None:
        if now - self.last_wait_print <= 10:
            return
        vals = self._consensus_values(pools)
        missing = sorted(name for name, p in pools.items() if p.current_prevhash is None)
        if deadline_passed:
            print(
                "\n--- BASELINE DEADLINE PASSED, STILL NO QUORUM "
                "(need >=2 responding pools agreeing on one prevhash) ---",
                flush=True,
            )
        else:
            print(f"\n--- WAITING FOR BASELINE CONSENSUS: {vals} ---", flush=True)
        if missing:
            print(f"--- MISSING BASELINE: {missing} ---", flush=True)
        print("", flush=True)
        self.last_wait_print = now



def _nested_get(obj: Dict[str, Any], path: Iterable[str]) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _extract_block_height(data: Dict[str, Any]) -> Optional[int]:
    for path in (("height",), ("block", "height"), ("extras", "height")):
        value = _nested_get(data, path)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _extract_miner_tag(data: Dict[str, Any]) -> Optional[str]:
    """Best-effort parser for mempool.space enriched block responses."""
    candidates = [
        ("extras", "pool", "name"),
        ("extras", "pool", "slug"),
        ("extras", "pool", "id"),
        ("pool", "name"),
        ("pool", "slug"),
        ("pool", "id"),
        ("miner", "name"),
        ("miner",),
        ("mined_by",),
        ("poolName",),
        ("pool_name",),
    ]

    for path in candidates:
        value = _nested_get(data, path)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for path in (("extras", "pool"), ("pool",)):
        value = _nested_get(data, path)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _fetch_json_blocking(url: str, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": CLIENT_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read().decode(charset)
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("expected JSON object")
    return data


BLOCK_LOOKUP_MAX_RETRIES = 2
BLOCK_LOOKUP_BACKOFF_BASE = 2.0  # seconds; doubles each retry


async def lookup_block_metadata(block_hash: str) -> Dict[str, Any]:
    """Lookup block height/miner tag after timing has stopped.
    Retries on transient HTTP errors (429, 503) with exponential backoff."""
    endpoints = [
        f"{MEMPOOL_API_BASE}/v1/block/{block_hash}",
        f"{MEMPOOL_API_BASE}/block/{block_hash}",
    ]

    last_error: Optional[str] = None
    loop = asyncio.get_running_loop()

    for url in endpoints:
        for attempt in range(1 + BLOCK_LOOKUP_MAX_RETRIES):
            try:
                data = await loop.run_in_executor(
                    None, lambda u=url: _fetch_json_blocking(u, BLOCK_MINER_LOOKUP_TIMEOUT)
                )
            except urllib.error.HTTPError as e:
                last_error = str(e)
                if e.code in (429, 503) and attempt < BLOCK_LOOKUP_MAX_RETRIES:
                    wait = BLOCK_LOOKUP_BACKOFF_BASE * (2 ** attempt)
                    await asyncio.sleep(wait)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as e:
                last_error = str(e)
                break

            return {
                "height": _extract_block_height(data),
                "miner": _extract_miner_tag(data) or "Unknown",
                "source": "mempool.space",
            }

    return {
        "height": None,
        "miner": "lookup_failed",
        "source": f"mempool.space error: {last_error or 'no usable response'}",
    }


async def enrich_races_with_block_miners(races: List[Race]) -> None:
    """Post-run only: attach block height and miner tag to confirmed races."""
    confirmed = [r for r in races if r.confirmed]
    unique_hashes = sorted({r.prevhash for r in confirmed})

    if not unique_hashes:
        return

    print("\n--- POST-RUN BLOCK MINER LOOKUP STARTED ---", flush=True)
    print(f"Looking up {len(unique_hashes)} unique confirmed block hash(es). Timing is already stopped.", flush=True)

    metadata: Dict[str, Dict[str, Any]] = {}
    for i, block_hash in enumerate(unique_hashes, 1):
        meta = await lookup_block_metadata(block_hash)
        metadata[block_hash] = meta
        height = meta["height"] if meta["height"] is not None else "N/A"
        print(f"  {i:>3}/{len(unique_hashes)} {short_hash(block_hash)} height={height} mined_by={meta['miner']}", flush=True)
        if i < len(unique_hashes):
            await asyncio.sleep(0.5)

    for race in confirmed:
        meta = metadata.get(race.prevhash, {})
        race.block_height = meta.get("height")
        race.block_miner = meta.get("miner") or "Unknown"
        race.block_miner_source = meta.get("source")

    print("--- POST-RUN BLOCK MINER LOOKUP FINISHED ---\n", flush=True)


def print_block_miner_summary(races: List[Race]) -> None:
    confirmed = [r for r in races if r.confirmed]
    enriched = [r for r in confirmed if r.block_miner]

    if not enriched:
        return

    print("\nBLOCK MINER TAG SUMMARY:")
    print("  Miner tags are post-run enrichment only. They are not used during timing.")

    columns = [
        ("Mined by", 18),
        ("Races", 5),
        ("Top winner", 14),
        ("Wins", 5),
        ("Avg spread", 10),
        ("Med spread", 10),
    ]
    header = " ".join(title.ljust(width) for title, width in columns)
    print(header)
    print("-" * len(header))

    grouped: Dict[str, List[Race]] = defaultdict(list)
    for race in enriched:
        grouped[race.block_miner or "Unknown"].append(race)

    for miner, miner_races in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        winner_counts = Counter(r.first_pool for r in miner_races)
        top_winner, top_wins = winner_counts.most_common(1)[0]
        spreads = []
        for race in miner_races:
            offsets = list(race.arrival_offsets_ms().values())
            if len(offsets) >= 2:
                spreads.append(max(offsets) - min(offsets))

        avg_spread = statistics.mean(spreads) if spreads else None
        med_spread = statistics.median(spreads) if spreads else None

        row = [
            miner[:18].ljust(18),
            str(len(miner_races)).rjust(5),
            top_winner.ljust(14),
            str(top_wins).rjust(5),
            fnum(avg_spread).rjust(10),
            fnum(med_spread).rjust(10),
        ]
        print(" ".join(row))

    print("\nBLOCK MINER TAG DETAIL:")
    for miner, miner_races in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        winner_text = ", ".join(f"{name}={count}" for name, count in Counter(r.first_pool for r in miner_races).most_common())
        print(f"  {miner}: races={len(miner_races)} winners: {winner_text}")

def delay_stats(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {
            "avg": None,
            "median": None,
            "p95": None,
            "stddev": None,
            "best": None,
            "worst": None,
        }

    return {
        "avg": statistics.mean(values),
        "median": statistics.median(values),
        "p95": pct(values, 95),
        "stddev": statistics.stdev(values) if len(values) > 1 else 0.0,
        "best": min(values),
        "worst": max(values),
    }


def print_pool_table(pools: Dict[str, PoolState]) -> None:
    columns = [
        ("Pool",        12),
        ("Wins",         5),
        ("Loss",         5),
        ("Seen",         5),
        ("Miss",         5),
        ("Avg",          8),
        ("Med",          8),
        ("P95",          8),
        ("Std",          8),
        ("Best",         8),
        ("Worst",        8),
        ("Unmatch",      7),
        ("Stale",        6),
        ("Unstable",     8),
        ("Reconn",       7),
        ("Timeout",      7),
        ("Closed",       6),
        ("ConnTO",       6),
        ("ConnErr",      7),
        ("Notify",       7),
        ("CleanT",       7),
        ("CleanF",       7),
        ("Noise",        7),
    ]

    header = " ".join(title.ljust(width) for title, width in columns)
    print(header)
    print("-" * len(header))

    for p in pools.values():
        stats = delay_stats(p.all_arrival_offsets)

        row = [
            p.name.ljust(12),
            str(p.wins).rjust(5),
            str(p.losses).rjust(5),
            str(p.seen).rjust(5),
            str(p.missed).rjust(5),
            fnum(stats["avg"]).rjust(8),
            fnum(stats["median"]).rjust(8),
            fnum(stats["p95"]).rjust(8),
            fnum(stats["stddev"]).rjust(8),
            fnum(stats["best"]).rjust(8),
            fnum(stats["worst"]).rjust(8),
            str(p.unmatched).rjust(7),
            str(p.stale_repeats).rjust(6),
            str(p.unstable).rjust(8),
            str(p.reconnects).rjust(7),
            str(p.read_timeouts).rjust(7),
            str(p.remote_closes).rjust(6),
            str(p.connect_timeouts).rjust(6),
            str(p.connect_errors).rjust(7),
            str(p.notify_total).rjust(7),
            str(p.clean_true).rjust(7),
            str(p.clean_false).rjust(7),
            str(p.noise_repeats).rjust(7),
        ]

        print(" ".join(row))


def print_race_detail(races: List[Race], limit: Optional[int] = None) -> None:
    selected = races[-limit:] if limit and limit > 0 else races
    if not selected:
        print("\nPER-RACE DETAIL: none")
        return

    print("\nPER-RACE DETAIL:")
    for race in selected:
        status = "confirmed" if race.confirmed else "unmatched notify"
        arrivals = sorted(race.arrival_offsets_ms().items(), key=lambda kv: kv[1])
        arrival_text = ", ".join(f"{name} +{delay:.1f}ms" for name, delay in arrivals)
        missed = race.missed_pools() if race.confirmed else []
        missed_text = f" | missed: {', '.join(missed)}" if missed else ""
        print(
            f"{race.index:>3}. {short_hash(race.prevhash)} {status:<16} "
            f"winner={race.first_pool:<12} arrivals: {arrival_text}{missed_text}"
        )


def print_rankings(pools: Dict[str, PoolState], confirmed_count: int) -> None:
    def msfmt(v: Optional[float]) -> str:
        return "-" if v is None else f"{v:.1f}ms"

    print("\nRANKING  (Median counts wins as 0ms; ChaseMed = median delay on races NOT won, blank if always first)")
    print(f" {'Rk':>2}  {'Pool':<12} {'Median':>9}  {'ChaseMed':>9}  {'Seen':>4}  {'Wins':>4}")

    ranked = [
        (statistics.median(p.all_arrival_offsets), p)
        for p in pools.values()
        if p.all_arrival_offsets
    ]
    for rank, (median_delay, p) in enumerate(sorted(ranked, key=lambda x: x[0]), 1):
        chase_med = statistics.median(p.delays) if p.delays else None
        print(
            f" {rank:>2}  {p.name:<12} {msfmt(median_delay):>9}  {msfmt(chase_med):>9}  "
            f"{f'{p.seen}/{confirmed_count}':>4}  {p.wins:>4}"
        )

    print(
        "\n  high wins + low chase = dominant and fast. High wins + high chase = bimodal "
        "(wins when it wins, far behind otherwise), the signature of a geography/peering effect."
    )



def print_full_timing_table(pools: Dict[str, PoolState], confirmed_count: int) -> None:
    print("\nFULL POOL TIMING:")
    columns = [
        ("Pool", 12),
        ("Wins", 5),
        ("Seen", 7),
        ("Miss", 5),
        ("Avg", 8),
        ("Med", 8),
        ("P95", 8),
        ("Best", 8),
        ("Worst", 8),
        ("Reconn", 7),
        ("Timeout", 7),
        ("Closed", 6),
    ]

    header = " ".join(title.ljust(width) for title, width in columns)
    print(header)
    print("-" * len(header))

    ranked = sorted(
        pools.values(),
        key=lambda p: (
            statistics.median(p.all_arrival_offsets) if p.all_arrival_offsets else float("inf"),
            p.name,
        ),
    )

    for p in ranked:
        stats = delay_stats(p.all_arrival_offsets)

        row = [
            p.name.ljust(12),
            str(p.wins).rjust(5),
            f"{p.seen}/{confirmed_count}".rjust(7),
            str(p.missed).rjust(5),
            fnum(stats["avg"]).rjust(8),
            fnum(stats["median"]).rjust(8),
            fnum(stats["p95"]).rjust(8),
            fnum(stats["best"]).rjust(8),
            fnum(stats["worst"]).rjust(8),
            str(p.reconnects).rjust(7),
            str(p.read_timeouts).rjust(7),
            str(p.remote_closes).rjust(6),
        ]

        print(" ".join(row))


def runtime_info(args: argparse.Namespace, pool_configs: List[PoolConfig], start_local: str, start_utc: str) -> Dict[str, Any]:
    return {
        "credit": "@proofofmike / ProofOfMike.com  and  @AtlasPool_io / AtlasPool.io",
        "client_version": CLIENT_VERSION,
        "started_local": start_local,
        "started_utc": start_utc,
        "python": sys.version.replace("\n", " "),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "duration_seconds": args.duration,
        "confirm_window_seconds": CONFIRM_WINDOW,
        "reconnect_delay_seconds": RECONNECT_DELAY,
        "warmup_after_consensus_seconds": WARMUP_AFTER_CONSENSUS,
        "connect_timeout_seconds": CONNECT_TIMEOUT,
        "read_timeout_seconds": READ_TIMEOUT,
        "baseline_timeout_seconds": getattr(args, "baseline_timeout", DEFAULT_BASELINE_TIMEOUT),
        "shutdown_grace_seconds": SHUTDOWN_GRACE,
        "tag_block_miners": bool(getattr(args, "tag_block_miners", False)),
        "block_miner_lookup_timeout_seconds": BLOCK_MINER_LOOKUP_TIMEOUT,
        "block_miner_lookup_source": MEMPOOL_API_BASE,
        "pool_count": len(pool_configs),
        "pools": [asdict(pc) for pc in pool_configs],
    }


def pool_summary_dict(p: PoolState) -> Dict[str, Any]:
    stats = delay_stats(p.all_arrival_offsets)
    nonwin_stats = delay_stats(p.delays)
    return {
        "name": p.name,
        "host": p.host,
        "port": p.port,
        "excluded_at_baseline": p.excluded_at_baseline,
        "exclude_reason": p.exclude_reason,
        "auth_failed": p.auth_failed,
        "auth_error": p.auth_error,
        "subscribe_failed": p.subscribe_failed,
        "subscribe_error": p.subscribe_error,
        "wins": p.wins,
        "losses": p.losses,
        "seen": p.seen,
        "missed": p.missed,
        "arrival_offset_ms": stats,
        "non_winner_delay_ms": nonwin_stats,
        "unmatched": p.unmatched,
        "stale_repeats": p.stale_repeats,
        "unstable": p.unstable,
        "connect_attempts": p.connect_attempts,
        "connections": p.connections,
        "reconnects": p.reconnects,
        "read_timeouts": p.read_timeouts,
        "remote_closes": p.remote_closes,
        "connect_timeouts": p.connect_timeouts,
        "connect_errors": p.connect_errors,
        "other_disconnects": p.other_disconnects,
        "notify_total": p.notify_total,
        "clean_true": p.clean_true,
        "clean_false": p.clean_false,
        "noise_repeats": p.noise_repeats,
        "noise_prevhash_changes": p.noise_prevhash_changes,
        "parse_errors": p.parse_errors,
        "bad_notify": p.bad_notify,
        "empty_notifies": p.empty_notifies,
        "first_notify_at_wall": p.first_notify_at_wall,
        "last_notify_at_wall": p.last_notify_at_wall,
    }


def race_dict(r: Race) -> Dict[str, Any]:
    return {
        "index": r.index,
        "prevhash": r.prevhash,
        "prevhash_short": short_hash(r.prevhash),
        "block_height": r.block_height,
        "block_miner": r.block_miner,
        "block_miner_source": r.block_miner_source,
        "winner": r.first_pool,
        "winner_nonempty": r.nonempty_winner(),
        "first_wall": r.first_wall,
        "first_epoch": r.first_epoch,
        "first_utc": r.first_utc,
        "confirmed": r.confirmed,
        "closed": r.closed,
        "eligible_at_start": sorted(r.eligible_at_start),
        "arrivals_offset_ms": r.arrival_offsets_ms(),
        "nonempty_arrivals_offset_ms": r.nonempty_arrival_offsets_ms(),
        "empty_first_pools": sorted(r.empty_first),
        # For empty-first pools: how long their miners hashed the empty
        # template before the full one arrived.
        "empty_to_full_ms": {
            p: ms(r.nonempty_arrivals[p] - r.arrivals[p])
            for p in r.empty_first
            if p in r.nonempty_arrivals
        },
        "arrival_wall": dict(r.arrival_wall),
        "missed_pools": r.missed_pools() if r.confirmed else [],
    }


def write_json(path: str, pools: Dict[str, PoolState], races: List[Race], meta: Dict[str, Any]) -> None:
    confirmed = [r for r in races if r.confirmed]
    payload = {
        "meta": meta,
        "methodology_note": "Wins are first observed by this client/vantage point, not global proof of pool propagation victory.",
        "confirmed_races": len(confirmed),
        "unmatched_observed_notifies": len([r for r in races if not r.confirmed]),
        "pools": [pool_summary_dict(p) for p in pools.values()],
        "races": [race_dict(r) for r in races],
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def write_csv(prefix_or_path: str, pools: Dict[str, PoolState], races: List[Race]) -> Tuple[str, str]:
    path = Path(prefix_or_path)
    if path.suffix.lower() == ".csv":
        pool_path = path
        race_path = path.with_name(path.stem + "_races.csv")
    else:
        pool_path = Path(str(path) + "_pools.csv")
        race_path = Path(str(path) + "_races.csv")

    with pool_path.open("w", newline="") as f:
        fieldnames = [
            "pool", "host", "port", "excluded_at_baseline", "exclude_reason",
            "auth_failed", "auth_error", "wins", "losses", "seen", "missed",
            "avg_ms", "median_ms", "p95_ms", "stddev_ms", "best_ms", "worst_ms",
            "chase_median_ms", "chase_avg_ms", "chase_p95_ms",
            "unmatched", "stale", "unstable", "reconnects", "read_timeouts",
            "remote_closes", "connect_timeouts", "connect_errors", "notify_total",
            "clean_true", "clean_false", "noise_repeats", "noise_prevhash_changes",
            "parse_errors", "bad_notify",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in pools.values():
            stats = delay_stats(p.all_arrival_offsets)
            chase = delay_stats(p.delays)
            writer.writerow({
                "pool": p.name,
                "host": p.host,
                "port": p.port,
                "excluded_at_baseline": p.excluded_at_baseline,
                "exclude_reason": p.exclude_reason,
                "auth_failed": p.auth_failed,
                "auth_error": p.auth_error,
                "wins": p.wins,
                "losses": p.losses,
                "seen": p.seen,
                "missed": p.missed,
                "avg_ms": stats["avg"],
                "median_ms": stats["median"],
                "p95_ms": stats["p95"],
                "stddev_ms": stats["stddev"],
                "best_ms": stats["best"],
                "worst_ms": stats["worst"],
                "chase_median_ms": chase["median"],
                "chase_avg_ms": chase["avg"],
                "chase_p95_ms": chase["p95"],
                "unmatched": p.unmatched,
                "stale": p.stale_repeats,
                "unstable": p.unstable,
                "reconnects": p.reconnects,
                "read_timeouts": p.read_timeouts,
                "remote_closes": p.remote_closes,
                "connect_timeouts": p.connect_timeouts,
                "connect_errors": p.connect_errors,
                "notify_total": p.notify_total,
                "clean_true": p.clean_true,
                "clean_false": p.clean_false,
                "noise_repeats": p.noise_repeats,
                "noise_prevhash_changes": p.noise_prevhash_changes,
                "parse_errors": p.parse_errors,
                "bad_notify": p.bad_notify,
            })

    with race_path.open("w", newline="") as f:
        fieldnames = [
            "index", "prevhash", "block_height", "block_miner", "confirmed", "winner",
            "first_wall", "first_epoch", "first_utc",
            "pool", "offset_ms", "eligible_at_start", "missed_pools",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in races:
            offsets = r.arrival_offsets_ms()
            for pool_name, offset in sorted(offsets.items(), key=lambda kv: kv[1]):
                writer.writerow({
                    "index": r.index,
                    "prevhash": r.prevhash,
                    "block_height": r.block_height,
                    "block_miner": r.block_miner,
                    "confirmed": r.confirmed,
                    "winner": r.first_pool,
                    "first_wall": r.first_wall,
                    "first_epoch": r.first_epoch,
                    "first_utc": r.first_utc,
                    "pool": pool_name,
                    "offset_ms": offset,
                    "eligible_at_start": ";".join(sorted(r.eligible_at_start)),
                    "missed_pools": ";".join(r.missed_pools() if r.confirmed else []),
                })

    return str(pool_path), str(race_path)


def print_final_report(
    pools: Dict[str, PoolState],
    races: List[Race],
    duration: int,
    meta: Dict[str, Any],
    race_limit: Optional[int] = None,
    verbose: bool = False,
    debug: bool = False,
    full_timing: bool = False,
) -> None:
    confirmed = [r for r in races if r.confirmed]
    unmatched_observed_notifies = [r for r in races if not r.confirmed]

    print("\n================ SUMMARY ================\n")
    print("Credit: @proofofmike / ProofOfMike.com  and  @AtlasPool_io / AtlasPool.io")
    print(f"Run duration: {duration}s")
    print(f"Confirmed races: {len(confirmed)}")
    print(f"Unmatched observed notifies: {len(unmatched_observed_notifies)}")

    if len(confirmed) < MIN_DIRECTIONAL_RACES:
        print(
            f"WARNING: only {len(confirmed)} confirmed races. Treat this as directional, "
            f"not statistically final. Suggested minimum: {MIN_DIRECTIONAL_RACES}+ races."
        )

    excluded = [p for p in pools.values() if p.excluded_at_baseline]
    print("\nEXCLUDED AT BASELINE (not part of the race):")
    if not excluded:
        print("  none")
    else:
        for p in sorted(excluded, key=lambda x: x.name):
            print(f"  {p.name:<12} {p.exclude_reason}")

    auth_problems = [p for p in pools.values() if p.auth_failed or p.subscribe_failed]
    print("\nAUTH / SUBSCRIBE ISSUES:")
    if not auth_problems:
        print("  none")
    else:
        for p in sorted(auth_problems, key=lambda x: x.name):
            if p.auth_failed:
                print(f"  {p.name:<12} authorize rejected: {p.auth_error}")
            if p.subscribe_failed:
                print(f"  {p.name:<12} subscribe rejected: {p.subscribe_error}")

    print_rankings(pools, len(confirmed))

    if full_timing:
        print_full_timing_table(pools, len(confirmed))

    problem_pools = [
        p for p in pools.values()
        if p.reconnects or p.read_timeouts or p.remote_closes or p.connect_timeouts or p.connect_errors
    ]

    print("\nCONNECTION ISSUES:")
    if not problem_pools:
        print("  none")
    else:
        for p in sorted(problem_pools, key=lambda x: (-x.reconnects, -x.read_timeouts, x.name)):
            parts = []
            if p.reconnects:
                parts.append(f"reconnects={p.reconnects}")
            if p.read_timeouts:
                parts.append(f"timeouts={p.read_timeouts}")
            if p.remote_closes:
                parts.append(f"closed={p.remote_closes}")
            if p.connect_timeouts:
                parts.append(f"connect_timeouts={p.connect_timeouts}")
            if p.connect_errors:
                parts.append(f"connect_errors={p.connect_errors}")
            print(f"  {p.name:<12} " + " ".join(parts))

    selected = confirmed[-race_limit:] if race_limit and race_limit > 0 else confirmed

    print("\nPER-RACE TIMING:")
    if not selected:
        print("  none")
    else:
        for race in selected:
            arrivals = sorted(race.arrival_offsets_ms().items(), key=lambda kv: kv[1])
            top = ", ".join(f"{name} +{delay:.1f}ms" for name, delay in arrivals[:3])
            more = f" (+{len(arrivals) - 3} more)" if len(arrivals) > 3 else ""
            miner = race.block_miner or ""
            miner_text = f" mined_by={miner:<18} " if miner else " "
            height_text = f"height={race.block_height} " if race.block_height is not None else ""
            print(
                f"  {race.index:>3}. {height_text}{short_hash(race.prevhash)}{miner_text}winner={race.first_pool:<12} "
                f"{top}{more}"
            )

    print_block_miner_summary(confirmed)

    print("\nNote: winner means first observed by this client/vantage point, not global propagation proof.")
    print(
        "Note: timing resolution is bounded by event-loop scheduling and TCP read buffering. "
        "Full-precision values are kept in the CSV/JSON for analysis, but treat sub-millisecond "
        "differences in any single race as noise, not signal."
    )

    if verbose:
        print("\nFULL POOL TABLE:")
        print_pool_table(pools)
        print_race_detail(races, limit=race_limit)

    if debug:
        print("\nCONNECTION DETAIL:")
        for p in pools.values():
            print(
                f"{p.name:<12} attempts={p.connect_attempts} "
                f"connected={p.connections} reconnects={p.reconnects} "
                f"timeouts={p.read_timeouts} remote_closed={p.remote_closes} "
                f"connect_timeouts={p.connect_timeouts} connect_errors={p.connect_errors} "
                f"other_disconnects={p.other_disconnects} "
                f"first_notify={p.first_notify_at_wall or 'N/A'} last_notify={p.last_notify_at_wall or 'N/A'}"
            )

        print("\nDEBUG ARRIVAL OFFSETS INCLUDING WINS (raw ms; sub-ms is noise):")
        for pool_name, p in pools.items():
            print(pool_name, p.all_arrival_offsets)

        print("\nRUNTIME:")
        print(f"  started_local = {meta['started_local']}")
        print(f"  started_utc   = {meta['started_utc']}")
        print(f"  python        = {meta['python']}")
        print(f"  platform      = {meta['platform']}")
        print(f"  client        = {CLIENT_VERSION}")
        print(f"  confirm_window={CONFIRM_WINDOW}s read_timeout={READ_TIMEOUT}s reconnect_delay={RECONNECT_DELAY}s")

        print("\nDEBUG NOTES:")
        print("  timestamp  = asyncio event-loop time taken immediately after readline() returns")
        print("  baseline   = first notify after connect/reconnect, clean=true OR clean=false")
        print("  race       = only clean=true + prevhash changed")
        print("  seen       = confirmed races where this pool's notify arrived inside the window")
        print("  miss       = confirmed races this pool was eligible for but did not match inside the window")
        print("  chase      = median/avg/p95 arrival delay on races this pool did NOT win (wins excluded)")
        print("  baseline   = full consensus if all pools agree by --baseline-timeout, else majority quorum + exclusions")
        print("  blockhash  = stratum prevhash transformed to canonical explorer hash; height/miner are post-run lookups")
        print("  reconnects = established session ended and reconnected: read timeout, remote close, or other disconnect")
        print("  noise      = clean=false same-prevhash notify/template refresh")

    # One-line headline result at the very end for quick scanning.
    if confirmed:
        ranked = sorted(
            [(statistics.median(p.all_arrival_offsets), p) for p in pools.values() if p.all_arrival_offsets],
            key=lambda x: x[0],
        )
        if len(ranked) >= 2:
            _, first = ranked[0]
            _, second = ranked[1]
            first_med = statistics.median(first.all_arrival_offsets)
            second_med = statistics.median(second.all_arrival_offsets)
            print(
                f"\nRESULT: {first.name} 1st (median {first_med:.1f}ms), "
                f"{second.name} 2nd (median {second_med:.1f}ms) over {len(confirmed)} races.",
                flush=True,
            )
        elif len(ranked) == 1:
            _, first = ranked[0]
            first_med = statistics.median(first.all_arrival_offsets)
            print(
                f"\nRESULT: {first.name} 1st (median {first_med:.1f}ms) over {len(confirmed)} races.",
                flush=True,
            )


def send_json(writer: asyncio.StreamWriter, obj: object) -> None:
    writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())


async def close_writer(writer: Optional[asyncio.StreamWriter]) -> None:
    if writer is None:
        return

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def pool_worker(
    name: str,
    host: str,
    port: int,
    user: str,
    tracker: RaceTracker,
    pools: Dict[str, PoolState],
    stop_event: asyncio.Event,
) -> None:
    pool = pools[name]

    while not stop_event.is_set():
        reader: Optional[asyncio.StreamReader] = None
        writer: Optional[asyncio.StreamWriter] = None
        established = False

        try:
            pool.connect_attempts += 1

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=CONNECT_TIMEOUT,
            )

            established = True
            pool.connected = True
            pool.connections += 1
            pool.connected_at_wall = local_iso()
            pool.current_prevhash = None
            pool.eligible = False
            pool.auth_failed = False
            pool.auth_error = None
            pool.subscribe_failed = False
            pool.subscribe_error = None
            _print(name, "connected")

            send_json(writer, {"id": 1, "method": "mining.subscribe", "params": []})
            send_json(writer, {"id": 2, "method": "mining.authorize", "params": [user, "x"]})
            await writer.drain()

            while not stop_event.is_set():
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=READ_TIMEOUT)
                except asyncio.TimeoutError:
                    raise ReconnectSession("read_timeout")

                recv_ts = loop_time()

                if not line:
                    raise ReconnectSession("remote_closed")

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except Exception:
                    pool.parse_errors += 1
                    continue

                method = msg.get("method")

                # Responses to our subscribe (id 1) / authorize (id 2) have no method.
                if method is None:
                    mid = msg.get("id")
                    err = msg.get("error")
                    res = msg.get("result")
                    if mid == 2 and (err is not None or res is False):
                        if not pool.auth_failed:
                            pool.auth_failed = True
                            pool.auth_error = (
                                format_stratum_error(err) if err is not None else "authorize result=false"
                            )
                            _print(name, f"authorize rejected by pool: {pool.auth_error}")
                    elif mid == 1 and (err is not None or res is False):
                        if not pool.subscribe_failed:
                            pool.subscribe_failed = True
                            pool.subscribe_error = (
                                format_stratum_error(err) if err is not None else "subscribe result=false"
                            )
                            _print(name, f"subscribe rejected by pool: {pool.subscribe_error}")
                    continue

                if method == "client.get_version":
                    send_json(
                        writer,
                        {"id": msg.get("id"), "result": CLIENT_VERSION, "error": None},
                    )
                    await writer.drain()
                    continue

                if method != "mining.notify":
                    continue

                params = msg.get("params", [])
                if len(params) < 2 or not isinstance(params[1], str):
                    pool.bad_notify += 1
                    continue

                # The stratum prevhash is byte-mangled and is NOT the explorer hash.
                # Transform once at ingest so prevhash is the canonical block hash
                # everywhere: matching, logging, and the post-run mempool lookup.
                try:
                    prevhash = stratum_prevhash_to_blockhash(params[1])
                except ValueError:
                    pool.bad_notify += 1
                    continue

                clean = bool(params[8]) if len(params) > 8 else False

                # An empty merkle-branch list means the template contains only the
                # coinbase: an "empty block" notify sent before the pool has done
                # transaction selection/validation. Some pools always send one of
                # these first, then follow with the full template.
                merkle = params[4] if len(params) > 4 else None
                empty_template = isinstance(merkle, list) and len(merkle) == 0

                tracker.handle_notify(
                    pool_name=name,
                    recv_ts=recv_ts,
                    prevhash=prevhash,
                    clean=clean,
                    pools=pools,
                    empty=empty_template,
                )

        except ReconnectSession as e:
            if not stop_event.is_set():
                pool.record_reconnect(e.reason)

                if e.reason == "read_timeout":
                    _print(name, f"read timeout after {READ_TIMEOUT:.1f}s, reconnecting")
                elif e.reason == "remote_closed":
                    _print(name, "remote closed connection, reconnecting")
                else:
                    _print(name, f"disconnect ({e.reason}), reconnecting")

        except asyncio.TimeoutError:
            if not stop_event.is_set():
                pool.connect_timeouts += 1
                _print(name, f"connect timeout, reconnect in {int(RECONNECT_DELAY)}s")

        except Exception as e:
            if not stop_event.is_set():
                if established:
                    pool.record_reconnect("other")
                    _print(name, f"disconnect ({e}), reconnect in {int(RECONNECT_DELAY)}s")
                else:
                    pool.connect_errors += 1
                    _print(name, f"connect error ({e}), reconnect in {int(RECONNECT_DELAY)}s")

        finally:
            pool.reset_connection_state()
            await close_writer(writer)

        if not stop_event.is_set():
            await asyncio.sleep(RECONNECT_DELAY)


HEARTBEAT_INTERVAL = 300.0  # 5 minutes

# ---------------------------------------------------------------------------
# State persistence for crash recovery
# ---------------------------------------------------------------------------

STATE_FILE_DEFAULT = Path.home() / ".str_race_state.json"
STATE_VERSION = 1


def _get_state_path() -> Path:
    """Return the state file path, configurable via STR_RACE_STATE_FILE env var."""
    env_path = os.environ.get("STR_RACE_STATE_FILE")
    if env_path:
        return Path(env_path)
    return STATE_FILE_DEFAULT


def save_state(
    pools: Dict[str, PoolState],
    tracker: RaceTracker,
    state_path: Optional[Path] = None,
    start_time: Optional[float] = None,
) -> None:
    """Persist collector state to a local JSON file for fast crash recovery.

    Uses atomic write (write to .tmp then rename) to prevent corruption if the
    process is killed mid-write.
    """
    if state_path is None:
        state_path = _get_state_path()

    connected_pools = sorted(
        name for name, p in pools.items() if p.connected
    )

    # Compute uptime: try asyncio loop time, fall back to monotonic for testability
    if start_time is not None:
        try:
            now = loop_time()
        except RuntimeError:
            now = time.monotonic()
        uptime = int(now - start_time)
    else:
        uptime = 0

    state = {
        "version": STATE_VERSION,
        "saved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "consensus_prevhash": tracker.consensus_prevhash,
        "connected_pools": connected_pools,
        "session_races": len(tracker.all_races),
        "uptime_at_save": uptime,
    }

    tmp_path = state_path.with_suffix(".tmp")
    try:
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(state_path)
    except OSError as exc:
        print(f"[{wall_clock()}] WARNING: failed to save state: {exc}", flush=True)


def load_state(state_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load previously saved collector state from the local JSON file.

    Returns the state dict if the file exists and is valid JSON with the expected
    fields. Returns None (with a warning log) if the file is missing or corrupted.
    """
    if state_path is None:
        state_path = _get_state_path()

    if not state_path.exists():
        return None

    try:
        raw = state_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"[{wall_clock()}] WARNING: state file corrupted or unreadable ({exc}), starting fresh",
            flush=True,
        )
        return None

    # Validate expected fields
    required_fields = {"version", "saved_utc", "consensus_prevhash", "connected_pools", "session_races"}
    if not required_fields.issubset(data.keys()):
        missing = required_fields - set(data.keys())
        print(
            f"[{wall_clock()}] WARNING: state file missing fields {missing}, starting fresh",
            flush=True,
        )
        return None

    return data


def _derive_heartbeat_url(post_url: str) -> str:
    """Derive the heartbeat endpoint URL from the ingest POST URL.

    If post_url ends with '/ingest', replace that last segment with '/heartbeat'.
    Otherwise, replace the last path segment with 'heartbeat'.
    """
    if post_url.endswith("/ingest"):
        return post_url[: -len("/ingest")] + "/heartbeat"
    # Strip trailing slash for uniform handling
    base = post_url.rstrip("/")
    last_slash = base.rfind("/")
    if last_slash > 0:
        return base[:last_slash] + "/heartbeat"
    return base + "/heartbeat"


def _post_heartbeat_blocking(url: str, api_key: str, payload: bytes) -> None:
    """POST heartbeat JSON to the backend. Best-effort: no retries."""
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "User-Agent": CLIENT_VERSION,
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()  # consume response body


async def heartbeat_loop(
    args: argparse.Namespace,
    pools: Dict[str, PoolState],
    tracker: RaceTracker,
    stop_event: asyncio.Event,
    start_epoch: float,
) -> None:
    """Background task that POSTs a heartbeat to the backend every 5 minutes.

    Best-effort: if the POST fails, log the error but do not retry.
    Only active when args.post_url is set.
    """
    post_url = getattr(args, "post_url", None)
    api_key = getattr(args, "api_key", None) or os.environ.get("STRATUMRACE_API_KEY")
    vantage = getattr(args, "vantage", None) or "unknown"

    if not post_url or not api_key:
        return

    heartbeat_url = _derive_heartbeat_url(post_url)
    loop = asyncio.get_running_loop()

    while not stop_event.is_set():
        # Wait the heartbeat interval, checking stop_event periodically
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
        except asyncio.CancelledError:
            return

        if stop_event.is_set():
            return

        # Build heartbeat payload
        connected_count = sum(1 for p in pools.values() if p.connected)
        eligible_count = sum(1 for p in pools.values() if p.eligible)
        last_prevhash = tracker.consensus_prevhash or ""
        uptime_seconds = int(time.time() - start_epoch)

        # Get last race epoch from most recent confirmed race
        last_race_epoch: Optional[float] = None
        for race in reversed(tracker.all_races):
            if race.confirmed:
                last_race_epoch = race.first_epoch
                break

        heartbeat_payload = {
            "type": "heartbeat",
            "vantage": vantage,
            "timestamp_utc": utc_iso(),
            "connected_pools": connected_count,
            "eligible_pools": eligible_count,
            "last_prevhash": last_prevhash,
            "last_race_epoch": last_race_epoch,
            "uptime_seconds": uptime_seconds,
        }

        payload_bytes = json.dumps(heartbeat_payload, separators=(",", ":")).encode("utf-8")

        try:
            await loop.run_in_executor(
                None,
                _post_heartbeat_blocking,
                heartbeat_url,
                api_key,
                payload_bytes,
            )
            _print("heartbeat", f"POST to {heartbeat_url} OK (uptime={uptime_seconds}s)")
        except Exception as e:
            _print("heartbeat", f"POST failed: {e}")


async def housekeeping(
    tracker: RaceTracker,
    pools: Dict[str, PoolState],
    stop_event: asyncio.Event,
    args: argparse.Namespace,
    start_time: float,
    race_sink=None,
) -> None:
    last_heartbeat = loop_time()
    session_races = 0

    while not stop_event.is_set():
        await asyncio.sleep(1.0)
        tracker.check_consensus(pools)
        closed_confirmed = tracker.cleanup_races(pools)

        # Fire enrichment + POST/sink processing for newly closed confirmed
        # races as background tasks — never awaited inline. The 1s cadence of
        # this loop is what enforces the 15s confirm window (via cleanup_races
        # above); awaiting the 5s mempool.space indexing delay here would
        # suspend window enforcement, letting a back-to-back block accept
        # arrivals past the window and corrupt its offsets.
        for race in closed_confirmed:
            session_races += 1
            _spawn_background(
                _process_confirmed_race(race, pools, args, start_time, session_races, race_sink)
            )

        # Save state after every confirmed race for crash recovery
        if closed_confirmed:
            save_state(pools, tracker, start_time=start_time)

        now = loop_time()
        if tracker.tracking_enabled and (now - last_heartbeat) >= HEARTBEAT_INTERVAL:
            last_heartbeat = now
            uptime_s = int(now - start_time)
            h, rem = divmod(uptime_s, 3600)
            m, _ = divmod(rem, 60)
            confirmed = sum(1 for r in tracker.all_races if r.confirmed)
            connected = sum(1 for p in pools.values() if p.connected)
            print(
                f"[{wall_clock()}] --- heartbeat: {confirmed} races confirmed, "
                f"{connected}/{len(pools)} pools connected, uptime {h}h{m:02d}m ---",
                flush=True,
            )


def _fetch_pool_config_from_s3(s3_uri: str) -> str:
    """Fetch pool configuration JSON from an S3 URI.

    Supports any S3-compatible endpoint. Requires boto3 to be installed.
    Fails immediately with a clear error if boto3 is unavailable or the
    S3 object cannot be retrieved.
    """
    try:
        import boto3  # noqa: F401
        from botocore.exceptions import ClientError, BotoCoreError
    except ImportError:
        raise SystemExit(
            "ERROR: --pool-config specifies an S3 URI but boto3 is not installed.\n"
            "Install boto3 ('pip install boto3') or provide a local file path instead."
        )

    # Parse s3://bucket/key
    path = s3_uri[5:]  # strip "s3://"
    slash_idx = path.find("/")
    if slash_idx < 1:
        raise SystemExit(f"ERROR: Invalid S3 URI '{s3_uri}'. Expected format: s3://bucket/key")
    bucket = path[:slash_idx]
    key = path[slash_idx + 1:]
    if not key:
        raise SystemExit(f"ERROR: Invalid S3 URI '{s3_uri}'. Key (object path) is empty.")

    try:
        s3 = boto3.client("s3")
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read().decode("utf-8")
        return body
    except (ClientError, BotoCoreError) as e:
        raise SystemExit(
            f"ERROR: Failed to fetch pool configuration from S3 ({s3_uri}).\n"
            f"Details: {e}"
        )


def _load_central_pool_config(config_path: str, pool_group: str) -> List[PoolConfig]:
    """Load pool configuration from the central config format (config/pools.json schema).

    Supports S3 URIs (s3://bucket/key) and local file paths.
    Filters pools by group tag and converts to PoolConfig objects.
    Fails immediately if config is unavailable or filtering yields 0 pools.
    """
    # Fetch raw JSON content
    if config_path.startswith("s3://"):
        raw_text = _fetch_pool_config_from_s3(config_path)
    else:
        file_path = Path(config_path)
        if not file_path.exists():
            raise SystemExit(
                f"ERROR: Pool configuration file not found: {config_path}"
            )
        try:
            raw_text = file_path.read_text()
        except OSError as e:
            raise SystemExit(
                f"ERROR: Unable to read pool configuration file '{config_path}': {e}"
            )

    # Parse JSON
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise SystemExit(
            f"ERROR: Pool configuration is not valid JSON: {e}"
        )

    if not isinstance(data, dict):
        raise SystemExit(
            "ERROR: Pool configuration must be a JSON object with a 'pools' array."
        )

    pools_array = data.get("pools")
    if not isinstance(pools_array, list) or len(pools_array) == 0:
        raise SystemExit(
            "ERROR: Pool configuration must contain a non-empty 'pools' array."
        )

    # Filter pools by group tag
    filtered: List[PoolConfig] = []
    for i, entry in enumerate(pools_array, 1):
        if not isinstance(entry, dict):
            raise SystemExit(f"ERROR: Pool entry {i} must be an object.")
        groups = entry.get("groups", [])
        if not isinstance(groups, list):
            raise SystemExit(f"ERROR: Pool entry {i} 'groups' must be a list.")

        # Include pool if its groups contain the requested pool_group
        if pool_group in groups:
            try:
                filtered.append(
                    PoolConfig(
                        name=str(entry["name"]),
                        host=str(entry["host"]),
                        port=int(entry["port"]),
                    )
                )
            except (KeyError, ValueError) as e:
                raise SystemExit(
                    f"ERROR: Pool entry {i} is missing or has invalid required fields (name, host, port): {e}"
                )

    if not filtered:
        available_groups: Set[str] = set()
        for entry in pools_array:
            for g in entry.get("groups", []):
                available_groups.add(g)
        raise SystemExit(
            f"ERROR: No pools match group filter '{pool_group}'.\n"
            f"Available groups in configuration: {sorted(available_groups)}"
        )

    # Validate unique names
    names = [pc.name for pc in filtered]
    if len(names) != len(set(names)):
        raise SystemExit("ERROR: Pool names must be unique after group filtering.")

    return filtered


def _resolve_api_key(args: argparse.Namespace) -> Optional[str]:
    """Resolve the API key from CLI flag or environment variable.

    Returns the API key string, or None if neither is set.
    Fails immediately if --post-url is provided but no API key can be found.
    """
    api_key = getattr(args, "api_key", None) or os.environ.get("STRATUMRACE_API_KEY")

    if not api_key and getattr(args, "post_url", None):
        raise SystemExit(
            "ERROR: --post-url is specified but no API key was provided.\n"
            "Set --api-key flag or STRATUMRACE_API_KEY environment variable."
        )

    return api_key


def _build_race_result(
    race: Race,
    pools: Dict[str, PoolState],
    args: argparse.Namespace,
    start_time: float,
    session_races: int,
) -> Dict[str, Any]:
    """Build the Race_Result JSON document matching the design schema."""
    return {
        "version": 1,
        "vantage": getattr(args, "vantage", None) or "unknown",
        "block_height": race.block_height,
        "prevhash": race.prevhash,
        "prevhash_short": short_hash(race.prevhash),
        "first_epoch": race.first_epoch,
        "first_utc": race.first_utc,
        "confirm_window_s": CONFIRM_WINDOW,
        "winner": race.first_pool,
        "winner_nonempty": race.nonempty_winner(),
        "block_miner": race.block_miner,
        "block_miner_source": race.block_miner_source,
        "arrivals_offset_ms": race.arrival_offsets_ms(),
        "nonempty_arrivals_offset_ms": race.nonempty_arrival_offsets_ms(),
        "empty_first_pools": sorted(race.empty_first),
        "empty_to_full_ms": {
            p: ms(race.nonempty_arrivals[p] - race.arrivals[p])
            for p in race.empty_first
            if p in race.nonempty_arrivals
        },
        "missed_pools": race.missed_pools(),
        "eligible_at_start": sorted(race.eligible_at_start),
        "pools_connected": sum(1 for p in pools.values() if p.connected),
        "pools_eligible": len(race.eligible_at_start),
        "collector_meta": {
            "version": CLIENT_VERSION,
            "uptime_seconds": int(time.time() - start_time),
            "session_races": session_races,
        },
    }


def _write_dead_letter(race_result: Dict[str, Any]) -> None:
    """Write a Race_Result to the dead-letter directory for manual recovery."""
    try:
        DEAD_LETTER_DIR.mkdir(parents=True, exist_ok=True)
        epoch = race_result.get("first_epoch", time.time())
        height = race_result.get("block_height") or "unknown"
        filename = f"{int(epoch)}-{height}.json"
        dl_path = DEAD_LETTER_DIR / filename
        dl_path.write_text(json.dumps(race_result, indent=2))
        print(f"[{wall_clock()}] POST dead-letter written: {dl_path}", flush=True)
    except OSError as e:
        print(f"[{wall_clock()}] ERROR writing dead-letter: {e}", flush=True)


def _post_json_blocking(url: str, payload: bytes, api_key: str, timeout: float) -> int:
    """POST JSON payload to url. Returns HTTP status code.

    Raises urllib.error.URLError on network failure.
    Raises urllib.error.HTTPError on non-2xx responses (code available on exception).
    """
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "User-Agent": CLIENT_VERSION,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


# Strong references to in-flight enrichment/POST/sink tasks. The event loop
# only keeps weak references to tasks, so without this a fire-and-forget task
# could be garbage-collected mid-execution and its race result silently lost.
_BACKGROUND_TASKS: set = set()


def _spawn_background(coro) -> asyncio.Task:
    """Create a background task, holding a strong reference until it completes."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def _process_confirmed_race(
    race: Race,
    pools: Dict[str, PoolState],
    args: argparse.Namespace,
    start_time: float,
    session_races: int,
    race_sink=None,
) -> None:
    """Enrich a confirmed race, then POST and/or sink the result.

    Runs as a spawned background task so the 5s mempool.space indexing delay
    (plus lookup retries) never blocks the housekeeping loop that enforces
    the confirm window. POST and sink run concurrently once enrichment
    completes; each failure is logged independently.
    """
    race_result = await _enrich_and_build(race, pools, args, start_time, session_races)

    waits = []
    if getattr(args, "post_url", None):
        waits.append(_post_enriched_result(race_result, args))
    if race_sink:
        waits.append(race_sink(race_result))

    if waits:
        results = await asyncio.gather(*waits, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                print(f"[{wall_clock()}] Race processing error: {r}", flush=True)


async def _enrich_and_build(
    race: Race,
    pools: Dict[str, PoolState],
    args: argparse.Namespace,
    start_time: float,
    session_races: int,
) -> Dict[str, Any]:
    """Enrich a race with block metadata and build the race_result dict.

    Used by both POST and local-dir paths to ensure identical output.
    Performs the 5s-delayed height/miner lookup from mempool.space when
    tag_block_miners is enabled, then delegates to _build_race_result.
    """
    if race.block_height is None and getattr(args, "tag_block_miners", False):
        try:
            await asyncio.sleep(5.0)  # Give mempool.space time to index
            meta = await lookup_block_metadata(race.prevhash)
            if meta.get("height") is not None:
                race.block_height = meta["height"]
                race.block_miner = meta.get("miner") or race.block_miner
        except Exception:
            pass  # Best effort — continue with null height if lookup fails
    return _build_race_result(race, pools, args, start_time, session_races)


async def post_race_result(
    race: Race,
    pools: Dict[str, PoolState],
    args: argparse.Namespace,
    start_time: float,
    session_races: int,
) -> None:
    """Build Race_Result JSON and POST to the ingest API with retry logic.

    Retry policy:
    - Exponential backoff: 1s, 2s, 4s (3 attempts max)
    - On 400: log error, do NOT retry (payload won't become valid)
    - On 429: extract Retry-After header, wait that duration, then retry
    - On network error / 5xx: retry with backoff
    - After 3 failed retries: write to dead-letter file

    Uses run_in_executor to avoid blocking the asyncio loop.
    """
    post_url = args.post_url
    api_key = getattr(args, "api_key", None) or os.environ.get("STRATUMRACE_API_KEY", "")

    # Enrich race and build the result dict
    race_result = await _enrich_and_build(race, pools, args, start_time, session_races)
    payload = json.dumps(race_result).encode("utf-8")

    loop = asyncio.get_running_loop()

    for attempt in range(POST_MAX_RETRIES):
        try:
            status = await loop.run_in_executor(
                None,
                lambda: _post_json_blocking(post_url, payload, api_key, POST_TIMEOUT),
            )
            # Success
            print(
                f"[{wall_clock()}] POST race {race.prevhash[:12]}... "
                f"to {post_url} → {status} (attempt {attempt + 1})",
                flush=True,
            )
            return

        except urllib.error.HTTPError as e:
            if e.code == 400:
                # Bad payload — don't retry
                print(
                    f"[{wall_clock()}] POST race {race.prevhash[:12]}... "
                    f"→ 400 Bad Request (not retrying): {e.reason}",
                    flush=True,
                )
                return

            if e.code == 429:
                # Rate limited — use Retry-After header if available
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except (ValueError, TypeError):
                        wait = POST_BACKOFF_BASE * (2 ** attempt)
                else:
                    wait = POST_BACKOFF_BASE * (2 ** attempt)
                print(
                    f"[{wall_clock()}] POST race → 429 Rate Limited, "
                    f"waiting {wait:.1f}s (attempt {attempt + 1}/{POST_MAX_RETRIES})",
                    flush=True,
                )
                await asyncio.sleep(wait)
                continue

            # 5xx or other server error — retry with backoff
            wait = POST_BACKOFF_BASE * (2 ** attempt)
            print(
                f"[{wall_clock()}] POST race → {e.code} {e.reason}, "
                f"retrying in {wait:.1f}s (attempt {attempt + 1}/{POST_MAX_RETRIES})",
                flush=True,
            )
            await asyncio.sleep(wait)

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Network error — retry with backoff
            wait = POST_BACKOFF_BASE * (2 ** attempt)
            print(
                f"[{wall_clock()}] POST race → network error: {e}, "
                f"retrying in {wait:.1f}s (attempt {attempt + 1}/{POST_MAX_RETRIES})",
                flush=True,
            )
            await asyncio.sleep(wait)

    # All retries exhausted — write to dead-letter
    print(
        f"[{wall_clock()}] POST race {race.prevhash[:12]}... "
        f"FAILED after {POST_MAX_RETRIES} attempts. Writing to dead-letter.",
        flush=True,
    )
    _write_dead_letter(race_result)


async def _post_enriched_result(race_result: Dict[str, Any], args: argparse.Namespace) -> None:
    """POST a pre-built race_result dict to the ingest API with retry logic.

    Same retry semantics as post_race_result but operates on an already-enriched
    dict (no additional enrichment step). Used when enrichment is done once and
    shared between POST and sink paths.
    """
    post_url = args.post_url
    api_key = getattr(args, "api_key", None) or os.environ.get("STRATUMRACE_API_KEY", "")
    payload = json.dumps(race_result).encode("utf-8")
    prevhash = race_result.get("prevhash", "?")[:12]

    loop = asyncio.get_running_loop()

    for attempt in range(POST_MAX_RETRIES):
        try:
            status = await loop.run_in_executor(
                None,
                lambda: _post_json_blocking(post_url, payload, api_key, POST_TIMEOUT),
            )
            print(
                f"[{wall_clock()}] POST race {prevhash}... "
                f"to {post_url} → {status} (attempt {attempt + 1})",
                flush=True,
            )
            return

        except urllib.error.HTTPError as e:
            if e.code == 400:
                print(
                    f"[{wall_clock()}] POST race {prevhash}... "
                    f"→ 400 Bad Request (not retrying): {e.reason}",
                    flush=True,
                )
                return

            if e.code == 429:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                if retry_after:
                    try:
                        wait = float(retry_after)
                    except (ValueError, TypeError):
                        wait = POST_BACKOFF_BASE * (2 ** attempt)
                else:
                    wait = POST_BACKOFF_BASE * (2 ** attempt)
                print(
                    f"[{wall_clock()}] POST race → 429 Rate Limited, "
                    f"waiting {wait:.1f}s (attempt {attempt + 1}/{POST_MAX_RETRIES})",
                    flush=True,
                )
                await asyncio.sleep(wait)
                continue

            wait = POST_BACKOFF_BASE * (2 ** attempt)
            print(
                f"[{wall_clock()}] POST race → {e.code} {e.reason}, "
                f"retrying in {wait:.1f}s (attempt {attempt + 1}/{POST_MAX_RETRIES})",
                flush=True,
            )
            await asyncio.sleep(wait)

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            wait = POST_BACKOFF_BASE * (2 ** attempt)
            print(
                f"[{wall_clock()}] POST race → network error: {e}, "
                f"retrying in {wait:.1f}s (attempt {attempt + 1}/{POST_MAX_RETRIES})",
                flush=True,
            )
            await asyncio.sleep(wait)

    # All retries exhausted — write to dead-letter
    print(
        f"[{wall_clock()}] POST race {prevhash}... "
        f"FAILED after {POST_MAX_RETRIES} attempts. Writing to dead-letter.",
        flush=True,
    )
    _write_dead_letter(race_result)


def load_pool_configs(pools_path: Optional[str], pool_config: Optional[str] = None, pool_group: str = "all") -> List[PoolConfig]:
    """Load pool configurations with backward-compatible fallback.

    Priority:
    1. --pool-config (central config format with group filtering) - new v2 path
    2. --pools (simple JSON list format) - legacy path
    3. DEFAULT_POOLS built-in list - original behavior
    """
    # New v2 path: central pool config with group filtering
    if pool_config:
        return _load_central_pool_config(pool_config, pool_group)

    # Legacy path: simple pools.json list
    if pools_path:
        raw = json.loads(Path(pools_path).read_text())
        if not isinstance(raw, list):
            raise ValueError("pools file must be a JSON list")

        configs: List[PoolConfig] = []
        for i, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                raise ValueError(f"pool entry {i} must be an object")
            try:
                configs.append(
                    PoolConfig(
                        name=str(item["name"]),
                        host=str(item["host"]),
                        port=int(item["port"]),
                    )
                )
            except KeyError as e:
                raise ValueError(f"pool entry {i} missing required field: {e}") from e

        names = [pc.name for pc in configs]
        if len(names) != len(set(names)):
            raise ValueError("pool names must be unique")

        return configs

    # Default: built-in pool list
    return [PoolConfig(name, host, port) for name, host, port in DEFAULT_POOLS]


async def run(args: argparse.Namespace, race_sink=None, stop_event=None) -> None:
    # Resolve API key (validates presence when --post-url is set)
    api_key = _resolve_api_key(args)

    # Build a default local sink when --local-dir is set and no sink is injected
    if getattr(args, "local_dir", None) and race_sink is None:
        from lib.local_store import LocalStorage
        _local_storage = LocalStorage(Path(args.local_dir))
        _local_storage.ensure_initial_files()
        _loop = asyncio.get_running_loop()

        async def _default_local_sink(race_result: dict) -> None:
            try:
                await _loop.run_in_executor(None, _local_storage.write_race, race_result)
            except Exception as e:
                print(f"[{wall_clock()}] Local write failed: {e}, writing to dead-letter", flush=True)
                _write_dead_letter(race_result)

        race_sink = _default_local_sink

    # Load pool configs using new v2 path or legacy fallback
    pool_configs = load_pool_configs(
        pools_path=args.pools,
        pool_config=getattr(args, "pool_config", None),
        pool_group=getattr(args, "pool_group", "all"),
    )
    pools = {
        pc.name: PoolState(name=pc.name, host=pc.host, port=pc.port, user=args.user)
        for pc in pool_configs
    }

    tracker = RaceTracker(baseline_timeout=args.baseline_timeout)

    # Only create and manage our own stop_event when not injected externally.
    # When stop_event is injected, the caller owns signal handling (e.g., the
    # standalone server) — do NOT register SIGTERM/SIGINT handlers.
    _own_stop_event = stop_event is None
    if _own_stop_event:
        stop_event = asyncio.Event()
        # Graceful shutdown on SIGTERM/SIGINT: systemd sends SIGTERM on
        # `systemctl stop/restart` (a routine operation for pool-config
        # updates). Without a handler, Python's default action kills the
        # process immediately, losing in-flight races and un-POSTed results.
        # Setting the stop event instead routes shutdown through the finally
        # block below, which drains and POSTs confirmed races before exit.
        _loop = asyncio.get_running_loop()
        for _sig in (signal.SIGTERM, signal.SIGINT):
            try:
                _loop.add_signal_handler(_sig, stop_event.set)
            except (NotImplementedError, RuntimeError, ValueError):
                pass  # Unsupported platform (e.g., Windows) or non-main thread

    start_local = local_iso()
    start_utc = utc_iso()
    meta = runtime_info(args, pool_configs, start_local, start_utc)

    # Attempt to recover state from previous session
    recovered_state = load_state()
    if recovered_state:
        prev_races = recovered_state.get("session_races", 0)
        prev_hash = recovered_state.get("consensus_prevhash", "?")
        prev_pools = recovered_state.get("connected_pools", [])
        saved_at = recovered_state.get("saved_utc", "?")
        print(
            f"Recovered from state file (saved {saved_at}): "
            f"previous session had {prev_races} races, "
            f"last prevhash was {short_hash(prev_hash)}, "
            f"{len(prev_pools)} pools were connected",
            flush=True,
        )

    print("\n================ START ================\n")
    print("Credit: @proofofmike / ProofOfMike.com  and  @AtlasPool_io / AtlasPool.io")
    print(f"Duration: {args.duration}s")
    print(f"Pools: {', '.join(pools.keys())}")
    print("Timing: asyncio event-loop clock, single thread")
    print("Baseline: any mining.notify")
    print("Race signal: clean=true + prevhash change")
    print("Win meaning: first observed by this client/vantage point")
    print()

    start_epoch = time.time()

    tasks = [
        asyncio.create_task(pool_worker(pc.name, pc.host, pc.port, args.user, tracker, pools, stop_event))
        for pc in pool_configs
    ]
    tasks.append(asyncio.create_task(housekeeping(tracker, pools, stop_event, args, start_epoch, race_sink=race_sink)))

    # Start heartbeat POST loop only if post_url is configured
    if getattr(args, "post_url", None):
        tasks.append(asyncio.create_task(heartbeat_loop(args, pools, tracker, stop_event, start_epoch)))

    try:
        if args.duration > 0:
            # Stop early if a shutdown signal arrives before the duration ends
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=args.duration)
            except asyncio.TimeoutError:
                pass
        else:
            # Duration 0 means run forever (daemon mode)
            await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        stop_event.set()
        # Workers may be parked in readline() up to READ_TIMEOUT; give them a short
        # grace to exit cleanly, then cancel the stragglers so shutdown is bounded.
        _, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE)
        for t in pending:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Force-close any confirmed in-flight races so they can be POSTed/sunk
        # before exit. Workers are already stopped, so no further arrivals are
        # possible — closing now loses nothing.
        final_confirmed = tracker.cleanup_races(pools, force=True)
        tracker.finalize(pools)

        # Graceful shutdown drain: process the final races and wait (bounded)
        # for all in-flight background enrichment/POST tasks, so a systemctl
        # restart does not lose confirmed race results (Requirement 21.6).
        if final_confirmed and (getattr(args, "post_url", None) or race_sink):
            confirmed_total = sum(1 for r in tracker.all_races if r.confirmed)
            for race in final_confirmed:
                _spawn_background(
                    _process_confirmed_race(race, pools, args, start_epoch, confirmed_total, race_sink)
                )
        drain = [t for t in _BACKGROUND_TASKS if not t.done()]
        if drain:
            print(
                f"[{wall_clock()}] Shutdown: waiting up to {SHUTDOWN_DRAIN_GRACE:.0f}s "
                f"for {len(drain)} in-flight race task(s)...",
                flush=True,
            )
            _, still_pending = await asyncio.wait(drain, timeout=SHUTDOWN_DRAIN_GRACE)
            for t in still_pending:
                t.cancel()
            await asyncio.gather(*drain, return_exceptions=True)

    if args.tag_block_miners:
        await enrich_races_with_block_miners(tracker.all_races)

    print_final_report(
        pools=pools,
        races=tracker.all_races,
        duration=args.duration,
        meta=meta,
        race_limit=args.race_limit,
        verbose=args.verbose,
        debug=args.debug,
        full_timing=args.full_timing,
    )

    if args.json_out:
        write_json(args.json_out, pools, tracker.all_races, meta)
        print(f"\nWrote JSON: {args.json_out}")

    if args.csv_out:
        pool_csv, race_csv = write_csv(args.csv_out, pools, tracker.all_races)
        print(f"Wrote CSV: {pool_csv}")
        print(f"Wrote CSV: {race_csv}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Asyncio Stratum prevhash race timer by @proofofmike.")
    parser.add_argument(
        "--user",
        help="Stratum username/address.worker. If omitted, a random valid throwaway bc1q address is generated and used as bc1q...race",
    )
    parser.add_argument("--duration", type=int, default=0, help="Run duration in seconds (0 = run forever)")
    parser.add_argument(
        "--baseline-timeout", type=float, default=DEFAULT_BASELINE_TIMEOUT,
        help="Seconds to wait for all pools to baseline before falling back to a majority quorum and excluding laggards",
    )
    parser.add_argument("--pools", help="Optional pools.json file. List of {name, host, port}")
    parser.add_argument("--json-out", help="Write structured JSON results to this path")
    parser.add_argument("--csv-out", help="Write pool/race CSV results. If path ends .csv, race CSV appends _races.csv")
    parser.add_argument("--race-limit", type=int, default=0, help="Limit per-race detail printed; 0 prints all races")
    parser.add_argument("--verbose", action="store_true", help="Print full pool table and full per-race detail")
    parser.add_argument("--full-timing", action="store_true", help="Print compact timing table for all pools")
    parser.add_argument("--tag-block-miners", action="store_true", help="After timing stops, look up block height/miner tags from mempool.space and include them in the report/export")
    parser.add_argument("--debug", action="store_true", help="Print connection detail, runtime info, and raw timing arrays")

    # v2 platform flags
    parser.add_argument("--post-url", help="URL of the Ingest API endpoint for POSTing race results")
    parser.add_argument("--api-key", help="API key for ingest authentication (or set STRATUMRACE_API_KEY env var)")
    parser.add_argument("--vantage", help="Vantage point label (e.g., 'local')")
    parser.add_argument("--local-dir", help="Write race results to local filesystem at this directory path")
    parser.add_argument("--pool-config", help="S3 URI (s3://bucket/key) or local file path for pool configuration")
    parser.add_argument("--pool-group", default="all", help="Pool group tag filter (default: 'all')")

    args = parser.parse_args()

    # Default vantage to "local" when --local-dir is set without explicit --vantage
    if getattr(args, "local_dir", None) and not args.vantage:
        args.vantage = "local"

    if not args.user:
        args.user = generate_throwaway_stratum_user()
        print(f"Using auto-generated throwaway stratum user: {args.user}", flush=True)

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
