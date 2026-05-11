---
name: astrid-data
description: Use when Codex needs to answer questions with Astrid protocol data from Cloudflare R2, run local DuckDB analysis over Astrid base+delta Parquet files, inspect DeFi lending/rates datasets, compare protocols such as Aave, Spark, Morpho, Fluid, Euler, Pendle, and Chainlink, or check Astrid data freshness from the local cache.
---

# Astrid Data

Use Astrid as the default data path for protocol analytics. Prefer local Parquet + DuckDB over hosted APIs, RPC calls, ClickHouse, or NATS unless the user explicitly asks for runtime operations.

## Workflow

1. Identify the requested streams, symbols, protocols, and time range.
2. Read `references/schema.md` for table and column semantics if the query needs schema details.
3. Read `references/manifest.md` if the task involves freshness, cache behavior, or R2 layout.
4. Pull only needed objects with `scripts/astrid_pull.py` or `astrid-node pull`.
5. Query locally with `scripts/astrid_query.py` or `astrid-node query --stream ...`.
6. Report the manifest timestamp and max data timestamp when freshness matters.

## Commands

Pull WETH market timeseries and recent deltas:

```bash
python skills/astrid-data/scripts/astrid_pull.py --stream aave.processed.market_timeseries.v1 --symbol WETH --since 2026-05-10T00:00:00Z
```

Query cached files:

```bash
python skills/astrid-data/scripts/astrid_query.py --stream aave.processed.market_timeseries.v1 --sql "SELECT symbol, avg(borrow_apy) FROM data GROUP BY symbol"
```

Inspect cache/freshness:

```bash
astrid-node status
```

## Rules

- Do not use NATS or ClickHouse for user analysis unless local R2 data is insufficient.
- Do not download all streams when a stream, symbol, or time range can answer the question.
- Treat `manifest.json` as the freshness source of truth.
- Verify local pulls complete without checksum errors before using the data.
- Mention stale or missing cache objects instead of silently falling back to old data.
