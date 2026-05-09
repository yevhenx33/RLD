# Astrid Node

Astrid Node is a local-first protocol data runtime. It syncs opt-in canonical
Astrid data streams into local ClickHouse so analysts, agents, and builders
can query, export, and process DeFi protocol data without depending on a hosted
query API.

> Hosted APIs answer questions the publisher anticipated. Astrid ships
> canonical protocol data so users can ask questions nobody anticipated.

## What Astrid Provides

- Local ClickHouse-backed protocol datasets
- NATS JetStream live stream consumption
- Versioned stream manifests
- Opt-in stream installation
- CSV and Parquet export
- Python processor SDK for custom derived tables
- Raw and processed stream modes

Astrid Node never mutates unrelated local tables. Each stream is installed
explicitly and writes to its own namespace, such as `astrid_aave.*`,
`astrid_aave_raw.*`, `astrid_chainlink.*`, or `astrid_user.*`.

## Quickstart

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env

astrid-node up
astrid-node migrate
astrid-node streams list --manifest examples/manifest.example.json
astrid-node streams install aave.processed.account_profiles.v1 \
  --manifest examples/manifest.example.json \
  --dry-run
```

Install a stream:

```bash
astrid-node streams install aave.processed.account_profiles.v1 \
  --manifest examples/manifest.example.json
```

Sync historical JSONL chunks from a manifest:

```bash
astrid-node sync aave.processed.account_profiles.v1 \
  --manifest examples/manifest.example.json
```

Consume live JetStream messages:

```bash
astrid-node consume aave.processed.account_profiles.v1 \
  --manifest examples/manifest.example.json \
  --tail
```

Export local data:

```bash
astrid-node export astrid_aave.account_profiles --format parquet --out exports/profiles.parquet
astrid-node export astrid_aave.account_profiles --format csv --out exports/profiles.csv
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

Custom processors can consume canonical streams and write derived rows to
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

See `examples/processors/health_factor_alerts.py`.

Fixture replay is available from the CLI:

```bash
astrid-node processor run examples/processors/health_factor_alerts.py \
  --fixture ./messages.jsonl
```

Processors can also replay synced local Astrid tables:

```bash
astrid-node processor run examples/processors/health_factor_alerts.py \
  --table astrid_aave.account_profiles \
  --limit 10000
```

## Stream Safety

- New streams are visible in manifests but are not installed automatically.
- `--dry-run` shows exactly which local tables will be created.
- Canonical stream tables are separate from user tables.
- Custom processors may only write to `astrid_user.*`.
- Cursors advance after successful local writes.

## Current V1 Status

This repository is ready as a standalone Astrid Node foundation:

- local metadata schema
- stream manifest parsing
- stream install/dry-run
- JSONL historical chunk sync
- JetStream live consume into ClickHouse payload tables
- CSV/Parquet export through ClickHouse formats
- processor SDK fixture and local-table replay runner

The canonical Astrid publisher currently lives in the analytics stack and publishes
manifests plus live NATS subjects.
