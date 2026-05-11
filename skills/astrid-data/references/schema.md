# Astrid Streams

The stream registry in `backend/analytics/streams/definitions` is the schema source of truth. Common streams:

- `aave.processed.market_timeseries.v1`: Aave market APY, utilization, supply/borrow USD over time.
- `aave.processed.market_latest.v1`: latest Aave market snapshot.
- `aave.processed.account_profiles.v1`: Aave account-level health/risk profile timeseries.
- `aave.processed.account_positions.v1`: Aave account position timeseries.
- `aave.raw.account_events.v1`: decoded Aave account events.
- `spark.processed.timeseries.v1`: Spark market APY and reserve timeseries.
- `spark.serving.market_timeseries.v1`: Spark rows from shared serving market timeseries.
- `spark.serving.market_latest.v1`: latest Spark market snapshot.
- `spark.state.*`: Spark reserve, eMode, risk, and scaled state.
- `chainlink.processed.prices.v1`: Chainlink price observations.
- `source.status.v1` and `spark.status.source.v1`: freshness and indexing status.

Common market columns:

- `timestamp`: data timestamp.
- `protocol`: canonical protocol id such as `AAVE_MARKET` or `SPARK_MARKET`.
- `entity_id`: canonical market/reserve identifier.
- `symbol`: asset symbol.
- `supply_apy`, `borrow_apy`: decimal APY values, not percentages. Multiply by 100 for percentage points.
- `utilization`: decimal utilization ratio.
- `supply_usd`, `borrow_usd`: USD notional values.

Query conventions:

- The helper scripts expose cached Parquet as DuckDB view `data`.
- Base+delta queries de-dupe by stream identity columns when stream metadata is available.
- For rates comparisons, filter by `symbol`, `protocol`, and `timestamp`.
