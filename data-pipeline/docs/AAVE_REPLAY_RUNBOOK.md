# Aave Replay Runbook (Raw -> Serving)

This runbook rebuilds Aave data from raw `aave_events` into fresh serving tables.

## 0) Preconditions

- ClickHouse is running and has `aave_events`.
- Docker image `rld_indexer_node` is built from current source.
- `data-pipeline/.env` is present with ClickHouse and RPC settings.

## 1) Freeze Aave ingest

```bash
docker stop rld_aave_processor rld_aave_collector
```

## 2) Reset Aave derived/state tables

```bash
docker exec rld_clickhouse clickhouse-client -q "TRUNCATE TABLE aave_timeseries"
docker exec rld_clickhouse clickhouse-client -q "TRUNCATE TABLE aave_scaled_state"
docker exec rld_clickhouse clickhouse-client -q "ALTER TABLE processor_state DELETE WHERE protocol = 'AAVE_MARKET'"
```

## 3) Replay from raw events

```bash
docker run --rm --network host \
  --env-file "/home/ubuntu/RLD/data-pipeline/.env" \
  -e CLICKHOUSE_HOST=127.0.0.1 \
  -e CLICKHOUSE_PORT=8123 \
  -v "/home/ubuntu/RLD/data-pipeline/indexer:/app/indexer" \
  -v "/home/ubuntu/RLD/data-pipeline/scripts:/app/scripts" \
  rld_indexer_node \
  python -u /app/scripts/replay_aave_from_raw.py
```

Expected completion log:

- `[AAVE_REPLAY] Completed. rows=<...> max_timestamp=<...> processor_head=<...>`

## 4) Rebuild serving tables from unified data

```bash
docker exec rld_clickhouse clickhouse-client -q "TRUNCATE TABLE api_market_latest"
docker exec rld_clickhouse clickhouse-client -q "TRUNCATE TABLE api_market_timeseries_hourly_agg"
docker exec rld_clickhouse clickhouse-client -q "TRUNCATE TABLE api_protocol_tvl_entity_weekly_agg"
```

```bash
docker exec rld_clickhouse clickhouse-client -q "
INSERT INTO api_market_latest
(protocol, entity_id, symbol, target_id, timestamp, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd)
SELECT
  protocol,
  entity_id,
  tupleElement(t, 1) AS symbol,
  tupleElement(t, 2) AS target_id,
  tupleElement(t, 3) AS timestamp,
  tupleElement(t, 4) AS supply_usd,
  tupleElement(t, 5) AS borrow_usd,
  tupleElement(t, 6) AS supply_apy,
  tupleElement(t, 7) AS borrow_apy,
  tupleElement(t, 8) AS utilization,
  tupleElement(t, 9) AS price_usd
FROM (
  SELECT
    protocol,
    entity_id,
    argMax(tuple(symbol, target_id, timestamp, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd), timestamp) AS t
  FROM unified_timeseries
  GROUP BY protocol, entity_id
)"
```

```bash
docker exec rld_clickhouse clickhouse-client -q "
INSERT INTO api_market_timeseries_hourly_agg
SELECT
  protocol,
  entity_id,
  toStartOfHour(timestamp) AS ts,
  avgState(toFloat64(supply_apy)) AS supply_apy_state,
  avgState(toFloat64(borrow_apy)) AS borrow_apy_state,
  avgState(toFloat64(utilization)) AS utilization_state,
  avgState(toFloat64(supply_usd)) AS supply_usd_state,
  avgState(toFloat64(borrow_usd)) AS borrow_usd_state
FROM unified_timeseries
GROUP BY protocol, entity_id, ts
"
```

```bash
docker exec rld_clickhouse clickhouse-client -q "
INSERT INTO api_protocol_tvl_entity_weekly_agg
SELECT day, clean_protocol AS protocol, entity_id, supply_usd_state
FROM (
  SELECT
    toStartOfWeek(timestamp) AS day,
    splitByChar('_', protocol)[1] AS clean_protocol,
    entity_id,
    argMaxState(toFloat64(supply_usd), timestamp) AS supply_usd_state
  FROM unified_timeseries
  WHERE protocol IN ('AAVE_MARKET', 'MORPHO_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
    AND entity_id != 'AAVE_MARKET_SYNTHETIC'
  GROUP BY day, clean_protocol, entity_id
)
"
```

## 5) Resume services

```bash
docker restart rld_graphql_api
docker start rld_aave_collector rld_aave_processor
```

## 6) Validation queries

Aave coverage:

```bash
docker exec rld_clickhouse clickhouse-client -q "
SELECT count(DISTINCT entity_id) AS entities, max(timestamp) AS max_ts
FROM aave_timeseries
"
```

Aave latest totals:

```bash
docker exec rld_clickhouse clickhouse-client -q "
SELECT
  sum(supply_usd) AS total_supply,
  sum(borrow_usd) AS total_borrow,
  count() AS markets,
  max(timestamp) AS snapshot_ts
FROM api_market_latest FINAL
WHERE protocol = 'AAVE_MARKET'
"
```

Aave latest weekly TVL:

```bash
docker exec rld_clickhouse clickhouse-client -q "
WITH max_day AS (
  SELECT max(day) AS d
  FROM api_protocol_tvl_entity_weekly_agg
  WHERE protocol='AAVE'
)
SELECT day, sum(supply_usd) AS total_supply
FROM (
  SELECT day, entity_id, argMaxMerge(supply_usd_state) AS supply_usd
  FROM api_protocol_tvl_entity_weekly_agg
  WHERE protocol='AAVE'
  GROUP BY day, entity_id
)
WHERE day = (SELECT d FROM max_day)
GROUP BY day
"
```

Latest snapshot and latest weekly TVL should match after a clean rebuild.

## Missing reserve set resolved in this replay

The replay fixed coverage for these 30 reserves (all now mapped in `indexer/tokens.py`):

- `1abaea1f7c830bd89acc67ec4af516284b1bc33c` EURC (6)
- `8292bb45bf1ee4d140127049757c2e0ff06317ed` RLUSD (18)
- `9f56094c450763769ba0ea9fe2876070c0fd5f77` PT-sUSDE-25SEP2025 (18)
- `14bdc3a3ae09f5518b923b69489cbcafb238e617` PT-eUSDE-14AUG2025 (18)
- `3b3fb9c57858ef816833dc91565efcd85d96f634` PT-sUSDE-31JUL2025 (18)
- `5a98fcbea516cf06857215779fd812ca3bef1b32` LDO (18)
- `d33526068d116ce69f19a9ee46f0bd304f21a51f` RPL (18)
- `e6a934089bbee34f832060ce98848359883749b3` PT-sUSDE-27NOV2025 (18)
- `e343167631d89b6ffc58b88d6b7fb0228795491d` USDG (6)
- `62c6e813b9589c3631ba0cdb013acdb8544038b7` PT-USDe-27NOV2025 (18)
- `917459337caac939d41d7493b3999f571d20d667` PT-USDe-31JUL2025 (18)
- `90d2af7d622ca3141efa4d8f1f24d86e5974cc8f` eUSDe (18)
- `bc6736d346a5ebc0debc997397912cd9b8fae10a` PT-USDe-25SEP2025 (18)
- `c18360217d8f7ab5e7c516566761ea12ce7f9d72` ENS (18)
- `50d2c7992b802eef16c04feadab310f31866a545` PT-eUSDE-29MAY2025 (18)
- `111111111117dc0aa78b770fa6a738034120c302` 1INCH (18)
- `e8483517077afa11a9b07f849cee2552f040d7b2` PT-sUSDE-5FEB2026 (18)
- `9bf45ab47747f4b4dd09b3c2c73953484b4eb375` PT-srUSDe-2APR2026 (18)
- `3de0ff76e8b528c092d47b9dac775931cef80f49` PT-sUSDE-7MAY2026 (18)
- `d11c452fc99cf405034ee446803b6f6c1f6d5ed8` tETH (18)
- `c96de26018a54d51c097160568752c4e3bd6c364` FBTC (8)
- `1f84a51296691320478c98b8d77f2bbd17d34350` PT-USDe-5FEB2026 (18)
- `aca92e438df0b2401ff60da7e4337b687a2435da` mUSD (6)
- `619d75e3b790ebc21c289f2805bb7177a7d732e2` PT-srUSDe-25JUN2026 (18)
- `3432b6a60d23ca0dfca7761b7ab56459d9c964d0` FXS (18)
- `af5191b0de278c7286d6c7cc6ab6bb8a73ba2cd6` STG (18)
- `defa4e8a7bcba345f687a2f1456f5edd9ce97202` KNC (18)
- `b0f70c0bd6fd87dbeb7c10dc692a2a6106817072` BTC.b (8)
- `aebf0bb9f57e89260d57f31af34eb58657d96ce0` PT-USDe-7MAY2026 (18)
- `9db38d74a0d29380899ad354121dfb521adb0548` PT-USDG-28MAY2026 (6)
