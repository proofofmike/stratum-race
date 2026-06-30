# stratum-race

Bitcoin mining tools by [@proofofmike](https://proofofmike.com).

## str_race.py

Multi-pool stratum prevhash race tool.

Connects to multiple Bitcoin solo mining pools simultaneously and compares when each pool delivers new block notifications (`mining.notify` with `clean_jobs=true`) from your network vantage point.

### What it measures

- Which pool announces a new prevhash (new block) first from your location
- The delay (in milliseconds) between the fastest and slowest pools for each block
- Connection reliability: reconnects, timeouts, and remote closes per pool
- Optionally enriches results with block height and miner tags from mempool.space

### How it works

1. Opens persistent stratum connections to all configured pools
2. Waits for all pools to baseline on the same prevhash (or falls back to majority quorum)
3. When a pool sends `mining.notify` with `clean_jobs=true` and a new prevhash, a race starts
4. Other pools matching the same prevhash within a 15-second window confirm the race
5. The first pool to deliver the new prevhash is the "winner" for that race

A "win" means this client observed that pool first from this network/location. It is not proof that the pool globally won block-template propagation.

### Requirements

- Python 3.7+
- No external dependencies (stdlib only)

### Installation

```bash
git clone https://github.com/proofofmike/stratum-race.git
cd stratum-race
```

### Usage

```bash
python3 str_race.py [OPTIONS]
```

#### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--pools` | built-in list | Path to a JSON file defining pools to test |
| `--duration` | 7200 | Run duration in seconds |
| `--user` | auto-generated | Stratum username/address.worker |
| `--baseline-timeout` | 120 | Seconds to wait for pool consensus before using majority quorum |
| `--json-out` | none | Write structured JSON results to this path |
| `--csv-out` | none | Write pool/race CSV results |
| `--tag-block-miners` | off | Look up block miner tags from mempool.space after timing stops |
| `--full-timing` | off | Print compact timing table for all pools |
| `--verbose` | off | Print full pool table and per-race detail |
| `--debug` | off | Print connection detail, runtime info, and raw timing arrays |
| `--race-limit` | 0 (all) | Limit per-race detail lines printed |

### Examples

**Quick 1-hour test with default pools:**

```bash
python3 str_race.py --duration 3600
```

**10-hour run with custom pools, full export, and block miner tags:**

```bash
python3 str_race.py \
  --pools solo-pools.json \
  --duration 36000 \
  --tag-block-miners \
  --json-out results.json \
  --csv-out results.csv
```

**Short test with verbose output:**

```bash
python3 str_race.py --duration 1800 --verbose --full-timing
```

### Custom pool configuration

The script includes a small default pool list built in. To test a broader set of pools, use the `--pools` flag with a JSON config file.

This repo includes `solo-pools.json` with 29 known solo mining pools:

```bash
python3 str_race.py --pools solo-pools.json --duration 36000
```

You can also create your own file. The format is a JSON array of objects with `name`, `host`, and `port`:

```json
[
  {"name": "ckpool", "host": "solo.ckpool.org", "port": 3333},
  {"name": "atlaspool", "host": "solo.atlaspool.io", "port": 3333},
  {"name": "public_pool", "host": "public-pool.io", "port": 3333}
]
```

Pool names must be unique. Use any name you like — it's just a label for the output.

### Output

The final report includes:

- **Rankings** — pools sorted by median arrival time
- **Per-race timing** — winner and delays for each observed block
- **Connection issues** — reconnects, timeouts, and errors per pool
- **Block miner summary** — which mining pools found each block (with `--tag-block-miners`)
- **One-line result** — headline showing 1st and 2nd place at the very end

JSON and CSV exports contain full precision data for further analysis.

### Notes

- Results depend on your location, network path, DNS, and pool backend behavior
- Run from different geographic locations to compare propagation paths
- Longer runs produce more statistically meaningful results (20+ races minimum suggested)
- Sub-millisecond differences in any single race should be treated as noise
- A 5-minute heartbeat prints during the run to confirm the script is alive

---

## Real-world usage

This tool was used to benchmark block notification speeds across solo mining pools. See the full analysis and results:

**[Block Notification Speed — AtlasPool](https://atlaspool.io/resources/articles/block-notification-speed)**
