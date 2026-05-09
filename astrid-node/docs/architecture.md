# Astrid Node Architecture

Astrid Node separates distribution from querying.

```text
Astrid canonical ClickHouse
  -> Astrid publisher
  -> NATS JetStream manifests/checkpoints/live updates
  -> Astrid Node
  -> Local ClickHouse
  -> SQL / exports / processors
```

NATS is the distribution rail. ClickHouse is the local analytical database.

## Namespaces

Local ClickHouse databases:

- `astrid_meta`: installation metadata, cursors, chunks, processor runs
- `astrid_aave`: processed Aave data
- `astrid_aave_raw`: raw/decoded Aave data
- `astrid_chainlink`: Chainlink prices
- `astrid_user`: custom processor outputs

## Stream Install Contract

Installing a stream may create:

- one local stream table
- one registry row
- one installed-stream row
- cursor/chunk metadata rows

Installing a stream must not alter unrelated tables.

## Message Storage

V1 stores canonical envelopes as `payload_json` in stream tables. This keeps the
local consumer schema-stable while stream-specific materialized views and typed
tables evolve.
