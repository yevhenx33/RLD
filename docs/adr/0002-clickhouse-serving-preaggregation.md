# ADR 0002: ClickHouse Serving Pre-Aggregation

- Status: Accepted
- Date: 2026-04-22

## Context

Hot API paths were aggregating directly from base timeseries tables and some processors used mutation-style delete+reinsert windows. This increased latency variance and merge pressure at higher ingest rates.

## Decision

Use incremental materialized views and append-first ingestion defaults:

- Keep processor writes append-first by default and gate mutation rewrites behind `CLICKHOUSE_MUTATION_REWRITE_ENABLED=false`.
- Add serving pre-aggregation tables:
  - `api_market_timeseries_hourly_agg` (AggregatingMergeTree, monthly partition, TTL)
  - `api_protocol_tvl_entity_weekly_agg` (AggregatingMergeTree, monthly partition, TTL)
- Populate via incremental MVs from `unified_timeseries` and expose queries through `avgMerge` / `argMaxMerge`.
- Enable async insert safeguards via:
  - `CLICKHOUSE_ASYNC_INSERT=true`
  - `CLICKHOUSE_WAIT_FOR_ASYNC_INSERT=true`

## Consequences

- API reads shift from base-table scans to pre-aggregated serving tables.
- Insert part pressure is reduced for high-frequency writes.
- Retention lifecycle is formalized at the serving-layer table class.
