"""Pool stats: parsers, validation, history."""

import json
import logging
import re
import time
import urllib.request
import ssl
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)
_SSL_CTX = ssl.create_default_context()
def parse_ck_hashrate(s: str) -> float:
    """'148P' -> H/s"""
    m = re.match(r'([\d.]+)\s*([KMGTPE]?)', str(s))
    if not m:
        return 0.0
    val = float(m.group(1))
    suffix = {'K': 1e3, 'M': 1e6, 'G': 1e9, 'T': 1e12, 'P': 1e15, 'E': 1e18}
    return val * suffix.get(m.group(2), 1.0)
def format_hashrate(h: float) -> str:
    if h >= 1e18:
        return f"{h / 1e18:.2f} EH/s"
    if h >= 1e15:
        return f"{h / 1e15:.2f} PH/s"
    if h >= 1e12:
        return f"{h / 1e12:.2f} TH/s"
    if h >= 1e9:
        return f"{h / 1e9:.2f} GH/s"
    if h >= 1e6:
        return f"{h / 1e6:.2f} MH/s"
    return f"{h:.0f} H/s"
def _fetch(url: str, method: str = 'GET', timeout: int = 10) -> str:
    """GET/POST a URL, return body. 1MB max so nobody can OOM us."""
    req = urllib.request.Request(url, method=method,
                                headers={'User-Agent': 'StratumRace/1.0'})
    resp = urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
    data = resp.read(1_048_576)
    if len(data) >= 1_048_576:
        raise ValueError(f"Response exceeded 1MB limit from {url}")
    return data.decode('utf-8')
# --- Parsers (one per pool API format) ---

def parse_atlaspool(data: dict) -> dict:
    return {
        "hashrate_value": data["poolHashrate"]["value"],
        "hashrate_formatted": data["poolHashrate"]["formatted"],
        "active_users": data.get("activeMiners"),
        "active_workers": data.get("activeWorkers"),
        "pool_fee": data.get("poolFee"),
        "miner_types": None,
    }
def parse_ckpool(raw: str) -> dict:
    """ckpool: multi-line JSON."""
    lines = [l.strip() for l in raw.strip().split('\n') if l.strip()]
    if len(lines) < 2:
        raise ValueError(f"Expected at least 2 lines, got {len(lines)}")
    users = json.loads(lines[0])
    hashrate = json.loads(lines[1])
    hr_str = hashrate.get("hashrate1hr", hashrate.get("hashrate5m", "0"))
    return {
        "hashrate_value": parse_ck_hashrate(hr_str),
        "hashrate_formatted": hr_str + "H/s",
        "active_users": users.get("Users"),
        "active_workers": users.get("Workers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_2miners(data: dict) -> dict:
    return {
        "hashrate_value": data["hashrate"],
        "hashrate_formatted": format_hashrate(data["hashrate"]),
        "active_users": data.get("minersTotal"),
        "active_workers": data.get("workersTotal"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_solofury(data: dict) -> dict:
    hr = data.get("totalHashRate1hr", data.get("totalHashRate", 0))
    return {
        "hashrate_value": hr,
        "hashrate_formatted": format_hashrate(hr),
        "active_users": data.get("totalMiners"),
        "active_workers": data.get("totalWorkers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_blitzpool(data: dict) -> dict:
    hr = data.get("totalHashRate", 0)
    return {
        "hashrate_value": hr,
        "hashrate_formatted": format_hashrate(hr),
        "active_users": None,
        "active_workers": data.get("totalMiners"),
        "pool_fee": data.get("fee"),
        "miner_types": None,
    }
def parse_mineshop(data: dict) -> dict:
    hr_str = data.get("hashrate", "0")
    # Parse the formatted string (e.g. "3.04 PH/s")
    hr_val = parse_ck_hashrate(hr_str.replace("/s", "").replace("H", "").strip())
    return {
        "hashrate_value": hr_val,
        "hashrate_formatted": hr_str,
        "active_users": data.get("users"),
        "active_workers": data.get("workers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_publicpool(data: dict) -> dict:
    """publicpool-style: sum userAgents array."""
    agents = data.get("userAgents", [])
    total_hr = sum(float(a.get("totalHashRate", 0)) for a in agents)
    total_workers = sum(int(a.get("count", 0)) for a in agents)

    # Build device type breakdown (top 10 by hashrate)
    sorted_agents = sorted(agents, key=lambda x: float(x.get("totalHashRate", 0)), reverse=True)
    miner_types = []
    for a in sorted_agents[:10]:
        hr = float(a.get("totalHashRate", 0))
        diff = float(a.get("bestDifficulty", 0))
        miner_types.append({
            "device": a.get("userAgent", "Unknown"),
            "workers": int(a.get("count", 0)),
            "hashrate_formatted": format_hashrate(hr),
            "best_difficulty_formatted": format_hashrate(diff).replace("H/s", ""),
        })

    return {
        "hashrate_value": total_hr,
        "hashrate_formatted": format_hashrate(total_hr),
        "active_users": None,
        "active_workers": total_workers,
        "pool_fee": None,
        "miner_types": miner_types if miner_types else None,
    }
def parse_pyblock(data: dict) -> dict:
    # Hashrate values are in TH/s as raw numbers
    hr_ths = float(data.get("hashrate1hr", data.get("hashrate5m", 0)))
    hr_raw = hr_ths * 1e12
    return {
        "hashrate_value": hr_raw,
        "hashrate_formatted": format_hashrate(hr_raw),
        "active_users": None,
        "active_workers": data.get("Workers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_noderunners(data: dict) -> dict:
    hr_str = data.get("Hashrate 1hr", data.get("Hashrate 5m", "0"))
    return {
        "hashrate_value": parse_ck_hashrate(hr_str.replace("/s", "").replace("H", "").strip()),
        "hashrate_formatted": hr_str,
        "active_users": data.get("Users"),
        "active_workers": data.get("Workers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_solohash(data: list) -> dict:

    btc = None
    for p in data:
        if isinstance(p, dict) and p.get("coin", {}).get("symbol") == "BTC":
            btc = p
            break
    if not btc:
        raise ValueError("BTC pool not found in SoloHash response")
    hr = btc["poolStats"]["poolHashrate"]
    return {
        "hashrate_value": hr,
        "hashrate_formatted": format_hashrate(hr),
        "active_users": btc["poolStats"].get("connectedMiners"),
        "active_workers": None,
        "pool_fee": None,
        "miner_types": None,
    }
def parse_ocean(raw: str) -> dict:
    # OCEAN CSV — values are TH/s despite what it looks like
    lines = [l.strip() for l in raw.strip().split('\n') if l.strip()]
    hr_ghs = 0.0
    for line in reversed(lines):
        parts = line.split(',')
        for p in parts[1:]:
            if p.strip():
                try:
                    hr_ghs = float(p.strip())
                    break
                except ValueError:
                    continue
        if hr_ghs > 0:
            break
    hr_hs = hr_ghs * 1e12  # TH/s to H/s (OCEAN CSV values are in TH/s)
    return {
        "hashrate_value": hr_hs,
        "hashrate_formatted": format_hashrate(hr_hs),
        "active_users": None,
        "active_workers": None,
        "pool_fee": None,
        "miner_types": None,
    }
def parse_solopool(data: dict) -> dict:

    pool = data.get("pool", {})
    header = pool.get("pool_header", {})
    hr = pool.get("total_hashrate1h", pool.get("total_hashrate5m", 0))
    return {
        "hashrate_value": hr,
        "hashrate_formatted": format_hashrate(hr),
        "active_users": header.get("active_users"),
        "active_workers": header.get("active_workers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_parasite(data: dict) -> dict:

    return {
        "hashrate_value": data.get("hashrate", 0),
        "hashrate_formatted": format_hashrate(data.get("hashrate", 0)),
        "active_users": data.get("users"),
        "active_workers": data.get("workers"),
        "pool_fee": None,
        "miner_types": None,
    }
def parse_antpool(data: dict) -> dict:

    items = data.get("data", {}).get("items", [])
    btc = next((c for c in items if c.get("coinType") == "BTC"), None)
    if not btc:
        raise ValueError("BTC not found in AntPool response")
    hr_str = btc.get("poolHashrate", "0")
    hr_val = parse_ck_hashrate(hr_str.replace("/s", "").replace("H", "").strip())
    return {
        "hashrate_value": hr_val,
        "hashrate_formatted": hr_str,
        "active_users": None,
        "active_workers": None,
        "pool_fee": None,
        "miner_types": None,
    }
def parse_spiderpool(data: dict) -> dict:

    pools = data.get("data", [])
    btc = next((c for c in pools if c.get("coin") == "BTC"), None)
    if not btc:
        raise ValueError("BTC not found in SpiderPool response")
    hr = float(btc.get("poolHashrate", 0))
    return {
        "hashrate_value": hr,
        "hashrate_formatted": format_hashrate(hr),
        "active_users": None,
        "active_workers": None,
        "pool_fee": None,
        "miner_types": None,
    }
def parse_helios(data: dict) -> dict:
    btc = data.get("btc", {})
    hr = btc.get("hashrate_5m", 0)
    fee_str = btc.get("fee")
    try:
        fee = float(fee_str) if fee_str is not None else None
    except (ValueError, TypeError):
        fee = None
    return {
        "hashrate_value": hr,
        "hashrate_formatted": format_hashrate(hr),
        "active_users": btc.get("users"),
        "active_workers": btc.get("workers"),
        "pool_fee": fee,
        "miner_types": None,
    }

def parse_btcpowlab(data: dict) -> dict:
    """BTC PoW Lab public pool summary; hashrate values are in TH/s."""
    pool = data.get("pool", {})
    economics = data.get("economics", {})
    hr_hs = float(pool.get("hashrate_1h_ths", pool.get("hashrate_5m_ths", 0))) * 1e12
    lab_bp = economics.get("lab_bp")
    return {
        "hashrate_value": hr_hs,
        "hashrate_formatted": format_hashrate(hr_hs),
        "active_users": pool.get("active_miners"),
        "active_workers": pool.get("active_workers"),
        "pool_fee": float(lab_bp) / 100 if lab_bp is not None else None,
        "miner_types": None,
    }

PARSERS = {
    "atlaspool": lambda raw: parse_atlaspool(json.loads(raw)),
    "ckpool": parse_ckpool,  # takes raw string (multi-line)
    "2miners": lambda raw: parse_2miners(json.loads(raw)),
    "solofury": lambda raw: parse_solofury(json.loads(raw)),
    "blitzpool": lambda raw: parse_blitzpool(json.loads(raw)),
    "mineshop": lambda raw: parse_mineshop(json.loads(raw)),
    "publicpool": lambda raw: parse_publicpool(json.loads(raw)),
    "pyblock": lambda raw: parse_pyblock(json.loads(raw)),
    "noderunners": lambda raw: parse_noderunners(json.loads(raw)),
    "solohash": lambda raw: parse_solohash(json.loads(raw)),
    "ocean": parse_ocean,  # takes raw string (CSV)
    "solopool": lambda raw: parse_solopool(json.loads(raw)),
    "parasite": lambda raw: parse_parasite(json.loads(raw)),
    "antpool": lambda raw: parse_antpool(json.loads(raw)),
    "spiderpool": lambda raw: parse_spiderpool(json.loads(raw)),
    "helios": lambda raw: parse_helios(json.loads(raw)),
    "btcpowlab": lambda raw: parse_btcpowlab(json.loads(raw)),
}
def fetch_pool_stats(url: str, api_type: str) -> dict:

    method = 'POST' if api_type == 'noderunners' else 'GET'
    raw = _fetch(url, method=method)
    parser = PARSERS.get(api_type)
    if not parser:
        raise ValueError(f"Unknown api_type: {api_type}")
    return parser(raw)
def compute_validation_status(
    pool_name: str,
    recent_races: list[dict],
    first_added_utc: Optional[str] = None,
    grace_hours: int = 48,
    now: Optional[datetime] = None,
) -> dict:
    """Compute validation status and uptime from race data.

    Returns:
        {"status": str, "last_verified_utc": str|None, "uptime_7d": float|None}
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Grace period check
    if first_added_utc:
        try:
            added_at = datetime.fromisoformat(first_added_utc.replace('Z', '+00:00'))
            grace_expires = added_at + timedelta(hours=grace_hours)
        except (ValueError, TypeError):
            grace_expires = now - timedelta(hours=1)  # expired
    else:
        grace_expires = now - timedelta(hours=1)  # no date = grace expired

    # Find races where this pool appeared (baselined on correct prevhash)
    verified_epochs = []

    for race in recent_races:
        arrivals = race.get("nonempty_arrivals_offset_ms", {})
        any_arrivals = race.get("arrivals_offset_ms", {})
        if pool_name in arrivals or pool_name in any_arrivals:
            verified_epochs.append(race.get("first_epoch", 0))

    # Compute uptime percentage

    if not verified_epochs:
        if now < grace_expires:
            return {"status": "pending", "last_verified_utc": None}
        return {"status": "critical", "last_verified_utc": None}

    latest = max(verified_epochs)
    hours_since = (now.timestamp() - latest) / 3600
    last_verified_utc = datetime.fromtimestamp(latest, tz=timezone.utc).isoformat()

    if hours_since <= 6:
        return {"status": "verified", "last_verified_utc": last_verified_utc}
    elif hours_since <= 24:
        return {"status": "warning", "last_verified_utc": last_verified_utc}
    else:
        return {"status": "critical", "last_verified_utc": last_verified_utc}
def compute_expected_days(hashrate: Optional[float], difficulty: Optional[float]) -> Optional[float]:
    # difficulty * 2^32 / hashrate / 86400
    if not hashrate or not difficulty or hashrate <= 0 or difficulty <= 0:
        return None
    expected_seconds = (difficulty * (2 ** 32)) / hashrate
    return expected_seconds / 86400.0
def append_snapshot(history_data: list, snapshot: dict, cap: int = 2016) -> list:

    history_data.append({
        "timestamp": int(time.time()),
        "hashrate_value": snapshot.get("hashrate_value"),
        "hashrate_formatted": snapshot.get("hashrate_formatted"),
        "active_users": snapshot.get("active_users"),
        "active_workers": snapshot.get("active_workers"),
    })
    # Trim oldest entries
    if len(history_data) > cap:
        history_data = history_data[-cap:]
    return history_data
def build_pool_stats_latest(
    pool_snapshots: dict[str, dict],
    validation_statuses: dict[str, dict],
    network_difficulty: Optional[float],
) -> dict:

    pools = {}
    for pool_name, snap in pool_snapshots.items():
        val = validation_statuses.get(pool_name, {"status": "pending", "last_verified_utc": None})
        entry = {
            "hashrate_value": snap.get("hashrate_value"),
            "hashrate_formatted": snap.get("hashrate_formatted"),
            "active_users": snap.get("active_users"),
            "active_workers": snap.get("active_workers"),
            "pool_fee": snap.get("pool_fee"),
            "miner_types": snap.get("miner_types"),
            "fetch_ok": snap.get("fetch_ok", True),
            "fetch_error": snap.get("fetch_error"),
            "fetched_utc": snap.get("fetched_utc"),
            "validation_status": val["status"],
            "last_verified_utc": val.get("last_verified_utc"),
        }
        pools[pool_name] = entry

    return {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "network_difficulty": network_difficulty,
        "pools": pools,
    }

def fetch_pool_blocks(mempool_slugs: list[str], limit: int = 5) -> dict:
    # pulls blocks found and coinbase payouts from mempool.space
    total_blocks = 0
    all_blocks = []

    for slug in mempool_slugs:
        try:
            # Get pool summary (total block count)
            raw = _fetch(f"https://mempool.space/api/v1/mining/pool/{slug}")
            data = json.loads(raw)
            total_blocks += data.get("blockCount", {}).get("all", 0)

            # Get recent blocks
            raw = _fetch(f"https://mempool.space/api/v1/mining/pool/{slug}/blocks?blockHeight=999999")
            blocks = json.loads(raw)
            all_blocks.extend(blocks)
        except Exception as e:
            logger.warning(f"Failed to fetch blocks for slug {slug}: {e}")

    # Sort by height descending, take top N
    all_blocks.sort(key=lambda b: b.get("height", 0), reverse=True)
    recent = all_blocks[:limit]

    # Fetch coinbase payouts for each recent block
    recent_with_payouts = []
    for block in recent:
        height = block.get("height")
        timestamp = block.get("timestamp", 0)
        date_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")

        payouts = _fetch_coinbase_payouts(height)

        recent_with_payouts.append({
            "height": height,
            "timestamp": timestamp,
            "date": date_str,
            "payouts": payouts,
        })

    return {
        "total_blocks": total_blocks,
        "recent_blocks": recent_with_payouts,
    }
def _fetch_coinbase_payouts(block_height: int) -> list[dict]:
    # grabs the coinbase tx to see who got paid
    try:
        # Get block hash
        raw = _fetch(f"https://mempool.space/api/block-height/{block_height}")
        block_hash = raw.strip()

        # Get first transaction (coinbase)
        raw = _fetch(f"https://mempool.space/api/block/{block_hash}/txs/0")
        txs = json.loads(raw)
        if not txs:
            return []

        coinbase_tx = txs[0]
        outputs = coinbase_tx.get("vout", [])
        total_value = sum(o.get("value", 0) for o in outputs)

        if total_value == 0:
            return []

        payouts = []
        for o in outputs:
            value = o.get("value", 0)
            if value == 0:
                continue
            addr = o.get("scriptpubkey_address", "unknown")
            payouts.append({
                "address": addr,
                "value_btc": round(value / 1e8, 8),
                "percentage": round((value / total_value) * 100, 2),
            })

        # Sort by value descending
        payouts.sort(key=lambda p: p["value_btc"], reverse=True)
        return payouts

    except Exception as e:
        logger.warning(f"Failed to fetch coinbase for block {block_height}: {e}")
        return []
