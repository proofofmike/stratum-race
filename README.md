# StratumRace

Measure Bitcoin mining pool block notification speed. StratumRace connects to mining pools via the Stratum protocol, observes block transitions in real-time, and measures which pools notify miners fastest.

You can run it two ways:
- **Command-line mode** — Terminal output only. Minimal dependencies (just Python).
- **Dashboard mode** — Local web UI with leaderboards, charts, and real-time updates.

---

## Command-Line Mode

Run the collector directly for terminal-based race results. No web server, no browser — just Python and a pools config file.

### Prerequisites

- Python 3.10+

### Quick Start

```bash
git clone https://github.com/proofofmike/stratum-race.git
cd stratum-race
python3 collector/str_race.py --pools config/pools.json --tag-block-miners
```

That's it. The script connects to all configured pools, establishes a baseline, and prints race results as blocks are found (~every 10 minutes).

### Example Output

```
[14:32:07.891] atlaspool    OBSERVED block start 00000000...a4f2eae (height resolved post-run)
[14:32:07.983] ckpool       match …a4f2eae delay=92.4 ms
[14:32:08.016] parasite     match …a4f2eae delay=125.7 ms
[14:32:08.099] viabtc       match …a4f2eae delay=208.5 ms
[14:32:08.134] f2pool       match …a4f2eae delay=243.1 ms
...
```

Each race shows which pool delivered a full block template first (offset = 0ms) and how far behind the others were.

### Common Options

| Flag | Description |
|------|-------------|
| `--pools config/pools.json` | Pool configuration file (required) |
| `--tag-block-miners` | Look up block height and miner from mempool.space after each race |
| `--duration 3600` | Run for N seconds then exit (default: 0 = run forever) |
| `--pool-group solo` | Only connect to pools in the "solo" group |
| `--json-out results.json` | Write structured JSON results on exit |
| `--csv-out results.csv` | Write CSV summary on exit |
| `--verbose` | Print full pool table and per-race detail |

### Running in the Background

To run continuously on a server:

```bash
# Run forever, tag miners, log to file
nohup python3 collector/str_race.py \
    --pools config/pools.json \
    --tag-block-miners \
    --json-out ~/races.json \
    > ~/stratum-race.log 2>&1 &

# Check on it
tail -f ~/stratum-race.log

# Stop it
kill %1
```

Or with a fixed duration (e.g., 24 hours):

```bash
nohup python3 collector/str_race.py \
    --pools config/pools.json \
    --tag-block-miners \
    --duration 86400 \
    --json-out ~/races-24h.json \
    > ~/stratum-race.log 2>&1 &
```

### What `--tag-block-miners` Does

After each race confirms, the collector queries mempool.space to determine:
- The **block height** of the new block
- Which **mining pool** found it (e.g., "Foundry USA", "AntPool")

This information is included in the JSON output and end-of-run summary. Without this flag, races are identified only by their prevhash.

---

## Dashboard Mode

Run a local web dashboard that shows real-time leaderboards, block timelines, and historical statistics. Includes the collector, an HTTP/WebSocket server, and a background aggregator in one process.

### Prerequisites

- Python 3.10+
- On Debian/Ubuntu: `sudo apt install python3-venv` (if not already installed)

Node.js is **not required** — the frontend comes pre-built.

### Quick Start

```bash
git clone https://github.com/proofofmike/stratum-race.git
cd stratum-race
./standalone/run.sh
```

Open **http://localhost:8080** in your browser. The leaderboard populates after the first block is found (~10 minutes).

### Manual Setup

```bash
git clone https://github.com/proofofmike/stratum-race.git
cd stratum-race

python3 -m venv .venv
source .venv/bin/activate
pip install -r standalone/packaging/requirements.txt

python -m standalone.main --pools config/pools.json
```

### Dashboard Options

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8080` | Bind port |
| `--data-dir` | `./data` | Directory for race data and aggregates |
| `--pools` | `config/pools.json` | Path to pool configuration file |
| `--pool-group` | `all` | Pool group filter |
| `--vantage` | `local` | Vantage point label (shown in the UI) |
| `--frontend-dir` | auto-detect | Path to built frontend |

### What You'll See

- **Leaderboard** — Pools ranked by median notification speed
- **Recent Blocks** — Per-block race results as they happen
- **Block Detail** — Click any block for a per-pool offset timeline
- **History** — Aggregate stats over configurable time periods

### Running the Dashboard in the Background

```bash
nohup ./standalone/run.sh --host 0.0.0.0 --port 8080 > ~/stratumrace.log 2>&1 &
```

Access from any machine on your network at `http://<your-ip>:8080`.

---

## Pool Configuration

The included `config/pools.json` connects to 34 major mining pools. Customize it:

```json
{
  "pools": [
    {
      "name": "my_pool",
      "display_name": "My Pool",
      "host": "stratum.mypool.com",
      "port": 3333,
      "operator": "My Pool",
      "pool_type": "solo",
      "groups": ["all", "custom"]
    }
  ]
}
```

Required fields: `name`, `display_name`, `host`, `port`, `groups`.

Use `--pool-group custom` (CLI) or `--pool-group custom` (dashboard) to connect only to pools tagged with that group.

---

## Troubleshooting

**"ensurepip is not available" / venv creation fails**
- Debian/Ubuntu: `sudo apt install python3.XX-venv` (replace XX with your Python version)

**No races appearing**
- Wait for the next Bitcoin block (~10 minutes average)
- Check for connection warnings — some pools may be unreachable from your network

**Dashboard: blank page**
- The pre-built frontend should work out of the box
- If you modified frontend source: `cd frontend && npm install && npm run build`

**Port already in use**
- Use `--port 9090` (or any available port)

---

## How It Works

The collector connects to each pool's Stratum endpoint and subscribes to `mining.notify` messages. When a new block is found on the Bitcoin network, pools send updated work to their miners. StratumRace measures the arrival time of each pool's notification relative to the fastest pool.

- **Race signal**: `clean_jobs=true` + new prevhash (indicates a new block)
- **Confirm window**: 15 seconds (arrivals after this are excluded)
- **Winner**: Earliest RTT-corrected notify at this vantage (raw first-seen is still retained)
- **Offsets**: Milliseconds behind the winner for each other pool, after subtracting half of each pool's shortest ICMP ping (one-way path latency)

---

## Architecture

```
Command-line mode:           Dashboard mode:

  str_race.py                 ┌─────────────────────────────┐
  (connects to pools,         │     Single Python Process    │
   prints to terminal,        │                              │
   writes JSON/CSV)           │  Collector → Server → Browser│
                              │              ↕               │
                              │          Aggregator          │
                              │              ↕               │
                              │         Local Storage        │
                              └─────────────────────────────┘
```

---

## Development

```bash
pip install -r standalone/packaging/requirements-dev.txt
python3 -m pytest tests/ -q
```

Source layout:
- `collector/str_race.py` — Stratum protocol client and race detection
- `standalone/` — HTTP server, aggregator, orchestration (dashboard mode)
- `lib/` — Shared computation (stats, aggregation, storage)
- `frontend/` — Vue 3 SPA (source + pre-built dist)
- `config/pools.json` — Pool configuration
