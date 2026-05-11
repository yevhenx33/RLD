# Astrid Node

Astrid Node is a local-first protocol data runtime. It syncs DeFi protocol
data as Parquet snapshots and makes them instantly queryable — no database
server, no credentials, no Docker required.

> Hosted APIs answer questions the publisher anticipated. Astrid ships
> canonical protocol data so users can ask questions nobody anticipated.

## Why Astrid Matters for AI Agents

AI agents spend **80% of their time** on infrastructure overhead, not analysis.
Astrid eliminates this:

```
  Agent Workflow                          Before          With Astrid
  ───────────────────────────────────     ──────          ───────────
  Server setup (CH host/port/pass)        2-5 min         0
  Data connection & schema discovery      30s per try     3ms
  Five analytical queries                 15s (5 calls)   506ms (1 call)
  Reproducibility on another machine      impossible      ship 2.7 MB file
```

**Key insight:** the bottleneck for agents isn't query speed (DuckDB runs in
5ms). It's **tool call overhead** — each `run_command` costs 2-5 seconds of
shell roundtrip. By eliminating server dependencies, an agent can batch
5 queries + chart generation into **one Python script, one tool call**, achieving
a **30× speedup** over the traditional approach.

```python
# One tool call = full analysis (506ms total)
import duckdb
con = duckdb.connect()
weth = "/data/MORPHO_MARKET__WETH__5a3d8c91.parquet"
daily   = con.execute(f"SELECT date_trunc('day', timestamp) d, avg(borrow_apy)*100 FROM '{weth}' GROUP BY d ORDER BY d").fetchall()
vol     = con.execute(f"SELECT stddev(borrow_apy)*100, percentile_cont(0.95) WITHIN GROUP (ORDER BY borrow_apy)*100 FROM '{weth}'").fetchone()
regimes = con.execute(f"SELECT CASE WHEN borrow_apy*100 < 2 THEN 'low' WHEN borrow_apy*100 < 5 THEN 'mid' ELSE 'high' END, count(*) FROM '{weth}' GROUP BY 1").fetchall()
cross   = con.execute(f"SELECT symbol, avg(borrow_apy)*100 FROM '{all_markets}' WHERE symbol IN ('WETH','USDC') GROUP BY 1").fetchall()
```

## Architecture

```
Publisher (VPS + ClickHouse)                  Client (any machine)
┌─────────────────────────┐                  ┌─────────────────────────┐
│                         │                  │                         │
│  market_timeseries      │   Snapshot       │  /data/markets/         │
│  7M rows, 180 markets   │──────────────►   │    WETH.parquet (2.7MB) │
│                         │   Per-market     │    USDC.parquet (3.7MB) │
│  astrid-publish         │   Parquet files  │    ...                  │
│    snapshot             │                  │                         │
│                         │                  │  DuckDB (pip install)   │
│                         │   Deltas         │    query_parquet()      │
│  NATS JetStream         │──────────────►   │    5ms per query        │
│  (live blocks)          │   append-only    │    no server needed     │
│                         │                  │                         │
└─────────────────────────┘                  └─────────────────────────┘
```

**Two query modes:**

| Mode | Requirements | Best For |
|------|-------------|----------|
| **DuckDB** (default) | `pip install duckdb` | Agents, analysts, zero-setup |
| **ClickHouse** (power) | Docker + ClickHouse | Live dashboards, complex joins |

## Quickstart — DuckDB Mode (Zero Install)

```bash
pip install -e .
pip install duckdb

# Pull WETH data (downloads ~12 MB from R2, cached locally)
astrid-node pull WETH
# ✓ 125 downloaded, 0 cached (0.4s)

# Pull specific markets
astrid-node pull WETH USDC USDT

# Pull all markets (~85 MB)
astrid-node pull

# List what's cached
astrid-node pull --list

# Query pulled data
astrid-node query ~/.astrid/data/markets/AAVE_MARKET__WETH__f7c17eb7.parquet \
  --sql "SELECT date_trunc('day', timestamp) d, avg(borrow_apy)*100 as borrow FROM data GROUP BY d ORDER BY d DESC LIMIT 5"

# Glob all WETH markets across protocols
astrid-node query "$HOME/.astrid/data/markets/*WETH*.parquet" \
  --sql "SELECT count(*), avg(borrow_apy)*100, avg(utilization) FROM data"

# Describe a file
astrid-node query ~/.astrid/data/markets/AAVE_MARKET__WETH__f7c17eb7.parquet --describe
```

## Quickstart — ClickHouse Mode (Power User)

```bash
pip install -e .
cp .env.example .env

# Start local services
astrid-node up
astrid-node migrate

# Fast sync from snapshot (7M rows in <5s)
astrid-node sync-fast --manifest /data/snapshot/manifest.json --target-db astrid_data

# Traditional stream install + live consume
astrid-node streams install aave.processed.account_profiles.v1 \
  --manifest examples/manifest.example.json
astrid-node consume aave.processed.account_profiles.v1 \
  --manifest examples/manifest.example.json --tail
```

## Publisher — Snapshot Export

```bash
# Export per-market Parquet snapshots (publisher-side)
astrid-publish snapshot market_timeseries --out /data/snapshot --compress zstd --workers 8

# Output:
#   /data/snapshot/
#     manifest.json
#     all_markets.parquet (32 MB)
#     markets/
#       MORPHO_MARKET__WETH__5a3d8c91.parquet (2.7 MB)
#       MORPHO_MARKET__USDC__a1b2c3d4.parquet (3.7 MB)
#       AAVE_MARKET__WETH__e5f6a7b8.parquet (2.6 MB)
#       ...
```

## Python SDK

```python
from astrid_node.duckdb_mode import query_parquet, describe_parquet, export_csv

# Inspect
info = describe_parquet("weth.parquet")
# {'rows': 672057, 'columns': [...], 'bytes': 2786304, ...}

# Query
rows = query_parquet("weth.parquet", """
    SELECT date_trunc('day', timestamp) d,
           avg(borrow_apy)*100 as borrow,
           count(*) as n
    FROM data
    WHERE timestamp >= '2026-05-01'
    GROUP BY d ORDER BY d
""")

# Export
export_csv("weth.parquet",
    "SELECT * FROM data WHERE timestamp >= '2026-04-01'",
    "weth_april.csv")
```

## Local Services

The bundled compose file starts local-only infrastructure:

- ClickHouse HTTP: `127.0.0.1:8124`
- NATS: `127.0.0.1:4223`
- NATS monitor: `127.0.0.1:8223`

```bash
astrid-node up
astrid-node down
```

## Processor SDK

Custom processors consume canonical streams and write derived rows to
`astrid_user.*` tables.

```python
from astrid_node import Processor

class MyRiskProcessor(Processor):
    inputs = ["aave.raw.account_events.v1"]

    def handle(self, msg, ctx):
        ctx.insert("astrid_user.my_scores", {
            "user": msg["user"],
            "score": 1,
        })
```

## Stream Safety

- New streams are visible in manifests but are not installed automatically.
- `--dry-run` shows exactly which local tables will be created.
- Canonical stream tables are separate from user tables.
- Custom processors may only write to `astrid_user.*`.
- Cursors advance after successful local writes.
- SHA-256 checksums verified on every snapshot sync.
