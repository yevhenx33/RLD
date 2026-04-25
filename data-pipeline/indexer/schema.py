"""ClickHouse schema management for the analytics pipeline."""

from __future__ import annotations

import os

from indexer.state import ensure_source_status_table


API_MARKET_TIMESERIES_AGG_TABLE = "api_market_timeseries_hourly_agg"
API_PROTOCOL_TVL_AGG_TABLE = "api_protocol_tvl_entity_weekly_agg"
AAVE_FLOW_DAILY_AGG_TABLE = "api_aave_market_flow_daily_agg"
API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE = "api_chainlink_price_weekly_agg"
MARKET_TIMESERIES_TABLE = "market_timeseries"


def _escape_sql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "''")


def ensure_clickhouse_users(ch) -> None:
    """Optionally create least-privilege ClickHouse users for local deployments."""
    if os.getenv("CLICKHOUSE_MANAGE_USERS", "false").strip().lower() not in {"1", "true", "yes"}:
        return

    api_user = os.getenv("CLICKHOUSE_API_USER", "api_readonly")
    api_password = os.getenv("CLICKHOUSE_API_PASSWORD")
    worker_user = os.getenv("CLICKHOUSE_WORKER_USER", "worker")
    worker_password = os.getenv("CLICKHOUSE_WORKER_PASSWORD")
    migration_user = os.getenv("CLICKHOUSE_MIGRATION_USER", "migration_admin")
    migration_password = os.getenv("CLICKHOUSE_MIGRATION_PASSWORD")

    for user, password in (
        (api_user, api_password),
        (worker_user, worker_password),
        (migration_user, migration_password),
    ):
        if not user or not password:
            continue
        ch.command(
            f"CREATE USER IF NOT EXISTS `{user}` IDENTIFIED WITH plaintext_password BY '{_escape_sql_string(password)}'"
        )

    if api_user and api_password:
        ch.command(f"GRANT SELECT ON *.* TO `{api_user}`")
    if worker_user and worker_password:
        ch.command(f"GRANT SELECT, INSERT, ALTER, CREATE, DROP ON *.* TO `{worker_user}`")
    if migration_user and migration_password:
        ch.command(f"GRANT ALL ON *.* TO `{migration_user}`")


def ensure_schema(ch) -> None:
    """Apply idempotent ClickHouse support schema for workers and serving."""
    ensure_clickhouse_users(ch)
    ensure_source_status_table(ch)
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS processor_state (
            protocol String,
            last_processed_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY protocol
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS collector_state (
            protocol String,
            last_collected_block UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY protocol
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {MARKET_TIMESERIES_TABLE} (
            timestamp DateTime,
            protocol LowCardinality(String),
            symbol LowCardinality(String),
            entity_id String,
            target_id String,
            supply_usd Float64,
            borrow_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (protocol, entity_id, timestamp)
        TTL timestamp + INTERVAL 18 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS api_market_latest (
            protocol LowCardinality(String),
            entity_id String,
            symbol LowCardinality(String),
            target_id String,
            timestamp DateTime,
            supply_usd Float64,
            borrow_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (protocol, entity_id)
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {API_MARKET_TIMESERIES_AGG_TABLE} (
            protocol LowCardinality(String),
            entity_id String,
            ts DateTime,
            supply_apy_state AggregateFunction(avg, Float64),
            borrow_apy_state AggregateFunction(avg, Float64),
            utilization_state AggregateFunction(avg, Float64),
            supply_usd_state AggregateFunction(avg, Float64),
            borrow_usd_state AggregateFunction(avg, Float64)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(ts)
        ORDER BY (protocol, entity_id, ts)
        TTL ts + INTERVAL 18 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {API_PROTOCOL_TVL_AGG_TABLE} (
            day DateTime,
            protocol LowCardinality(String),
            entity_id String,
            supply_usd_state AggregateFunction(argMax, Float64, DateTime)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(day)
        ORDER BY (protocol, day, entity_id)
        TTL day + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {AAVE_FLOW_DAILY_AGG_TABLE} (
            day DateTime,
            entity_id String,
            supply_inflow_raw_state AggregateFunction(sum, UInt256),
            supply_outflow_raw_state AggregateFunction(sum, UInt256),
            borrow_inflow_raw_state AggregateFunction(sum, UInt256),
            borrow_outflow_raw_state AggregateFunction(sum, UInt256)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(day)
        ORDER BY (entity_id, day)
        TTL day + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE} (
            day DateTime,
            feed LowCardinality(String),
            price_state AggregateFunction(argMax, Float64, DateTime)
        ) ENGINE = AggregatingMergeTree()
        PARTITION BY toStartOfMonth(day)
        ORDER BY (feed, day)
        TTL day + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(f"DROP VIEW IF EXISTS mv_{API_MARKET_TIMESERIES_AGG_TABLE}")
    ch.command(f"DROP VIEW IF EXISTS mv_{API_PROTOCOL_TVL_AGG_TABLE}")
    ch.command(f"DROP VIEW IF EXISTS mv_{API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}")
    ch.command(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_{API_MARKET_TIMESERIES_AGG_TABLE}
        TO {API_MARKET_TIMESERIES_AGG_TABLE}
        AS
        SELECT
            protocol,
            entity_id,
            toStartOfHour(timestamp) AS ts,
            avgState(toFloat64(supply_apy)) AS supply_apy_state,
            avgState(toFloat64(borrow_apy)) AS borrow_apy_state,
            avgState(toFloat64(utilization)) AS utilization_state,
            avgState(toFloat64(supply_usd)) AS supply_usd_state,
            avgState(toFloat64(borrow_usd)) AS borrow_usd_state
        FROM {MARKET_TIMESERIES_TABLE}
        GROUP BY protocol, entity_id, ts
        """
    )
    ch.command(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_{API_PROTOCOL_TVL_AGG_TABLE}
        TO {API_PROTOCOL_TVL_AGG_TABLE}
        AS
        SELECT day, clean_protocol AS protocol, entity_id, supply_usd_state
        FROM (
            SELECT
                toStartOfWeek(timestamp) AS day,
                splitByChar('_', protocol)[1] AS clean_protocol,
                entity_id,
                argMaxState(toFloat64(supply_usd), inserted_at) AS supply_usd_state
            FROM {MARKET_TIMESERIES_TABLE}
            WHERE protocol IN ('AAVE_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
              AND entity_id != 'AAVE_MARKET_SYNTHETIC'
            GROUP BY day, clean_protocol, entity_id
        )
        """
    )
    ch.command(
        f"""
        CREATE MATERIALIZED VIEW IF NOT EXISTS mv_{API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
        TO {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
        AS
        SELECT
            toStartOfWeek(timestamp) AS day,
            feed,
            argMaxState(toFloat64(price), timestamp) AS price_state
        FROM chainlink_prices
        WHERE feed IN ('BTC / USD', 'ETH / USD')
        GROUP BY day, feed
        """
    )


def backfill_serving_tables(ch) -> None:
    """Backfill new serving tables from legacy timeseries when empty."""
    market_rows = ch.command(f"SELECT count() FROM {MARKET_TIMESERIES_TABLE}") or 0
    if int(market_rows) == 0:
        ch.command(
            f"""
            INSERT INTO {MARKET_TIMESERIES_TABLE}
            (
                timestamp, protocol, symbol, entity_id, target_id,
                supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd
            )
            SELECT
                timestamp, protocol, symbol, entity_id, target_id,
                supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd
            FROM unified_timeseries
            WHERE entity_id != 'AAVE_MARKET_SYNTHETIC'
            """
        )
    latest_rows = ch.command("SELECT count() FROM api_market_latest") or 0
    if int(latest_rows) == 0:
        ch.command(
            f"""
            INSERT INTO api_market_latest
            (
                protocol, entity_id, symbol, target_id, timestamp,
                supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd
            )
            SELECT
                protocol,
                entity_id,
                tupleElement(latest_tuple, 1) AS symbol,
                tupleElement(latest_tuple, 2) AS target_id,
                tupleElement(latest_tuple, 3) AS timestamp,
                tupleElement(latest_tuple, 4) AS supply_usd,
                tupleElement(latest_tuple, 5) AS borrow_usd,
                tupleElement(latest_tuple, 6) AS supply_apy,
                tupleElement(latest_tuple, 7) AS borrow_apy,
                tupleElement(latest_tuple, 8) AS utilization,
                tupleElement(latest_tuple, 9) AS price_usd
            FROM (
                SELECT
                    protocol,
                    entity_id,
                    argMax(
                        tuple(symbol, target_id, timestamp, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization, price_usd),
                        timestamp
                    ) AS latest_tuple
                FROM {MARKET_TIMESERIES_TABLE}
                GROUP BY protocol, entity_id
            )
            """
        )


def rebuild_aggregates(ch) -> None:
    """Rebuild serving aggregate tables from canonical source tables."""
    ch.command(f"TRUNCATE TABLE IF EXISTS {API_MARKET_TIMESERIES_AGG_TABLE}")
    ch.command(f"TRUNCATE TABLE IF EXISTS {API_PROTOCOL_TVL_AGG_TABLE}")
    ch.command(f"TRUNCATE TABLE IF EXISTS {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}")
    ch.command(f"TRUNCATE TABLE IF EXISTS {AAVE_FLOW_DAILY_AGG_TABLE}")

    ch.command(
        f"""
        INSERT INTO {API_MARKET_TIMESERIES_AGG_TABLE}
        (
            protocol,
            entity_id,
            ts,
            supply_apy_state,
            borrow_apy_state,
            utilization_state,
            supply_usd_state,
            borrow_usd_state
        )
        SELECT
            protocol,
            entity_id,
            toStartOfHour(timestamp) AS ts,
            avgState(toFloat64(supply_apy)) AS supply_apy_state,
            avgState(toFloat64(borrow_apy)) AS borrow_apy_state,
            avgState(toFloat64(utilization)) AS utilization_state,
            avgState(toFloat64(supply_usd)) AS supply_usd_state,
            avgState(toFloat64(borrow_usd)) AS borrow_usd_state
        FROM {MARKET_TIMESERIES_TABLE}
        GROUP BY protocol, entity_id, ts
        """
    )
    ch.command(
        f"""
        INSERT INTO {API_PROTOCOL_TVL_AGG_TABLE}
        (day, protocol, entity_id, supply_usd_state)
        SELECT day, clean_protocol AS protocol, entity_id, supply_usd_state
        FROM (
            SELECT
                toStartOfWeek(timestamp) AS day,
                splitByChar('_', protocol)[1] AS clean_protocol,
                entity_id,
                argMaxState(toFloat64(supply_usd), inserted_at) AS supply_usd_state
            FROM {MARKET_TIMESERIES_TABLE}
            WHERE protocol IN ('AAVE_MARKET', 'EULER_MARKET', 'FLUID_MARKET')
              AND entity_id != 'AAVE_MARKET_SYNTHETIC'
            GROUP BY day, clean_protocol, entity_id
        )
        """
    )
    ch.command(
        f"""
        INSERT INTO {API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE}
        (day, feed, price_state)
        SELECT
            toStartOfWeek(timestamp) AS day,
            feed,
            argMaxState(toFloat64(price), timestamp) AS price_state
        FROM chainlink_prices
        WHERE feed IN ('BTC / USD', 'ETH / USD')
        GROUP BY day, feed
        """
    )
    ch.command(
        f"""
        INSERT INTO {AAVE_FLOW_DAILY_AGG_TABLE}
        (
            day,
            entity_id,
            supply_inflow_raw_state,
            supply_outflow_raw_state,
            borrow_inflow_raw_state,
            borrow_outflow_raw_state
        )
        SELECT
            day,
            entity_id,
            sumState(supply_inflow_raw) AS supply_inflow_raw_state,
            sumState(supply_outflow_raw) AS supply_outflow_raw_state,
            sumState(borrow_inflow_raw) AS borrow_inflow_raw_state,
            sumState(borrow_outflow_raw) AS borrow_outflow_raw_state
        FROM (
            SELECT
                toStartOfDay(block_timestamp) AS day,
                lower(concat('0x', substring(ifNull(topic1, ''), 27))) AS entity_id,
                if(
                    event_name = 'Supply',
                    if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)),
                    if(event_name = 'MintedToTreasury', if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)), toUInt256(0))
                ) AS supply_inflow_raw,
                if(
                    event_name = 'Withdraw',
                    if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)),
                    if(
                        event_name = 'Repay'
                        AND if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)) = toUInt256(1),
                        if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)),
                        toUInt256(0)
                    )
                ) AS supply_outflow_raw,
                if(
                    event_name = 'Borrow',
                    if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)),
                    toUInt256(0)
                ) AS borrow_inflow_raw,
                if(
                    event_name = 'Repay',
                    if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)),
                    toUInt256(0)
                ) AS borrow_outflow_raw
            FROM aave_events
            WHERE event_name IN ('Supply', 'Withdraw', 'Borrow', 'Repay', 'MintedToTreasury')

            UNION ALL

            SELECT
                toStartOfDay(block_timestamp) AS day,
                lower(concat('0x', substring(ifNull(topic1, ''), 27))) AS entity_id,
                toUInt256(0) AS supply_inflow_raw,
                if(length(data) >= 130, reinterpretAsUInt256(reverse(unhex(substring(data, 67, 64)))), toUInt256(0)) AS supply_outflow_raw,
                toUInt256(0) AS borrow_inflow_raw,
                toUInt256(0) AS borrow_outflow_raw
            FROM aave_events
            WHERE event_name = 'LiquidationCall'

            UNION ALL

            SELECT
                toStartOfDay(block_timestamp) AS day,
                lower(concat('0x', substring(ifNull(topic2, ''), 27))) AS entity_id,
                toUInt256(0) AS supply_inflow_raw,
                toUInt256(0) AS supply_outflow_raw,
                toUInt256(0) AS borrow_inflow_raw,
                if(length(data) >= 66, reinterpretAsUInt256(reverse(unhex(substring(data, 3, 64)))), toUInt256(0)) AS borrow_outflow_raw
            FROM aave_events
            WHERE event_name = 'LiquidationCall'
        )
        WHERE length(entity_id) = 42
        GROUP BY day, entity_id
        """
    )


def list_serving_views(ch) -> list[dict[str, str]]:
    rows = ch.query(
        """
        SELECT name, engine, create_table_query
        FROM system.tables
        WHERE database = currentDatabase()
          AND name IN (
            'mv_api_market_timeseries_hourly_agg',
            'mv_api_protocol_tvl_entity_weekly_agg',
            'mv_api_chainlink_price_weekly_agg'
          )
        ORDER BY name
        """
    ).result_rows
    return [
        {"name": str(name), "engine": str(engine), "definition": str(definition)}
        for name, engine, definition in rows
    ]
