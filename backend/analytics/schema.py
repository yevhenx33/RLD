"""ClickHouse schema management for the analytics pipeline."""

from __future__ import annotations

import os

from analytics.protocols import MORPHO_MARKET
from analytics.state import ensure_source_status_table
from analytics.morpho_oracle_snapshots import ensure_morpho_oracle_snapshot_tables
from analytics.oracle_snapshots import ensure_oracle_snapshot_tables
from analytics.fluid_full_coverage import ensure_fluid_full_coverage_tables, seed_core_fluid_contracts
from analytics.euler_schema import ensure_euler_tables
from analytics.aave_accounts import ensure_aave_account_tables
from analytics.streams.state import ensure_publisher_state_tables


API_MARKET_TIMESERIES_AGG_TABLE = "api_market_timeseries_hourly_agg"
API_PROTOCOL_TVL_AGG_TABLE = "api_protocol_tvl_entity_weekly_agg"
AAVE_FLOW_DAILY_AGG_TABLE = "api_aave_market_flow_daily_agg"
API_CHAINLINK_WEEKLY_PRICE_AGG_TABLE = "api_chainlink_price_weekly_agg"
MARKET_TIMESERIES_TABLE = "market_timeseries"
PENDLE_ETH_ASSETS_TABLE = "pendle_eth_assets"
PENDLE_ETH_PRICE_LATEST_TABLE = "pendle_eth_price_latest"
PENDLE_ETH_PRICE_OHLCV_TABLE = "pendle_eth_price_ohlcv"
PENDLE_ETH_BACKFILL_PROGRESS_TABLE = "pendle_eth_backfill_progress"
MORPHO_CHAINLINK_TIMESERIES_TABLE = "morpho_chainlink_timeseries"
EULER_TIMESERIES_TABLE = "euler_timeseries"
FLUID_TIMESERIES_TABLE = "fluid_timeseries"
AAVE_TIMESERIES_TABLE = "aave_timeseries"
SPARK_TIMESERIES_TABLE = "spark_timeseries"


def _ensure_raw_event_table(ch, table: str) -> None:
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name LowCardinality(String),
            topic0 String,
            topic1 Nullable(String),
            topic2 Nullable(String),
            topic3 Nullable(String),
            data String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(block_timestamp)
        ORDER BY (block_number, tx_hash, log_index)
        """
    )


def _ensure_aave_family_state_tables(
    ch,
    *,
    scaled_table: str,
    risk_table: str,
    emode_table: str,
    reserve_tokens_table: str,
) -> None:
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {scaled_table} (
            entity_id String,
            total_scaled_supply Float64,
            total_scaled_borrow Float64,
            liquidity_index Float64,
            variable_borrow_index Float64,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY entity_id
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {risk_table} (
            entity_id String,
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            e_mode_category UInt8 DEFAULT 0,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY entity_id
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {emode_table} (
            category_id UInt8,
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            price_source String DEFAULT '',
            label String DEFAULT '',
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY category_id
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {reserve_tokens_table} (
            deployment_id String,
            chain_id UInt32,
            reserve String,
            a_token String,
            stable_debt_token String DEFAULT '',
            variable_debt_token String,
            symbol LowCardinality(String),
            decimals UInt8,
            active UInt8 DEFAULT 1,
            source LowCardinality(String) DEFAULT 'UNKNOWN',
            block_number UInt64 DEFAULT 0,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (deployment_id, reserve)
        """
    )


def _ensure_market_risk_columns(ch, table: str) -> None:
    for column, column_type in (
        ("ltv", "Float64 DEFAULT 0"),
        ("liquidation_threshold", "Float64 DEFAULT 0"),
        ("liquidation_penalty", "Float64 DEFAULT 0"),
        ("e_mode_category", "UInt8 DEFAULT 0"),
        ("e_mode_ltv", "Float64 DEFAULT 0"),
        ("e_mode_liquidation_threshold", "Float64 DEFAULT 0"),
        ("e_mode_liquidation_penalty", "Float64 DEFAULT 0"),
        ("e_mode_label", "String DEFAULT ''"),
    ):
        ch.command(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}")


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
    ensure_morpho_oracle_snapshot_tables(ch)
    ensure_oracle_snapshot_tables(ch)
    ensure_aave_account_tables(ch)
    ensure_publisher_state_tables(ch)
    ensure_fluid_full_coverage_tables(ch)
    seed_core_fluid_contracts(ch)
    ensure_euler_tables(ch)
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
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            e_mode_category UInt8 DEFAULT 0,
            e_mode_ltv Float64 DEFAULT 0,
            e_mode_liquidation_threshold Float64 DEFAULT 0,
            e_mode_liquidation_penalty Float64 DEFAULT 0,
            e_mode_label String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (protocol, entity_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
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
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            e_mode_category UInt8 DEFAULT 0,
            e_mode_ltv Float64 DEFAULT 0,
            e_mode_liquidation_threshold Float64 DEFAULT 0,
            e_mode_liquidation_penalty Float64 DEFAULT 0,
            e_mode_label String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (protocol, entity_id)
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {AAVE_TIMESERIES_TABLE} (
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
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            e_mode_category UInt8 DEFAULT 0,
            e_mode_ltv Float64 DEFAULT 0,
            e_mode_liquidation_threshold Float64 DEFAULT 0,
            e_mode_liquidation_penalty Float64 DEFAULT 0,
            e_mode_label String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (protocol, entity_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {SPARK_TIMESERIES_TABLE} (
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
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            e_mode_category UInt8 DEFAULT 0,
            e_mode_ltv Float64 DEFAULT 0,
            e_mode_liquidation_threshold Float64 DEFAULT 0,
            e_mode_liquidation_penalty Float64 DEFAULT 0,
            e_mode_label String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (protocol, entity_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    _ensure_raw_event_table(ch, "aave_events")
    _ensure_raw_event_table(ch, "spark_events")
    for table in (MARKET_TIMESERIES_TABLE, "api_market_latest", AAVE_TIMESERIES_TABLE, SPARK_TIMESERIES_TABLE):
        _ensure_market_risk_columns(ch, table)
    _ensure_aave_family_state_tables(
        ch,
        scaled_table="spark_scaled_state",
        risk_table="spark_reserve_risk_state",
        emode_table="spark_emode_categories",
        reserve_tokens_table="spark_reserve_tokens",
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_reserve_risk_state (
            entity_id String,
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            e_mode_category UInt8 DEFAULT 0,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY entity_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS aave_emode_categories (
            category_id UInt8,
            ltv Float64 DEFAULT 0,
            liquidation_threshold Float64 DEFAULT 0,
            liquidation_penalty Float64 DEFAULT 0,
            price_source String DEFAULT '',
            label String DEFAULT '',
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY category_id
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
        TTL ts + INTERVAL 36 MONTH DELETE
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
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {PENDLE_ETH_ASSETS_TABLE} (
            asset_address String,
            chain_id UInt32,
            asset_type LowCardinality(String),
            symbol String,
            market_address String,
            expiry DateTime,
            active UInt8,
            matured UInt8,
            raw_metadata_json String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY asset_address
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {PENDLE_ETH_PRICE_LATEST_TABLE} (
            asset_address String,
            chain_id UInt32,
            asset_type LowCardinality(String),
            symbol String,
            expiry DateTime DEFAULT toDateTime(0),
            price_usd Float64,
            source_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY asset_address
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {PENDLE_ETH_PRICE_OHLCV_TABLE} (
            asset_address String,
            chain_id UInt32,
            asset_type LowCardinality(String),
            symbol String,
            expiry DateTime DEFAULT toDateTime(0),
            time_frame LowCardinality(String),
            timestamp DateTime,
            open Float64,
            high Float64,
            low Float64,
            close Float64,
            volume Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (asset_address, time_frame, timestamp)
        """
    )
    ch.command(
        f"ALTER TABLE {PENDLE_ETH_PRICE_LATEST_TABLE} "
        "ADD COLUMN IF NOT EXISTS expiry DateTime DEFAULT toDateTime(0) AFTER symbol"
    )
    ch.command(
        f"ALTER TABLE {PENDLE_ETH_PRICE_OHLCV_TABLE} "
        "ADD COLUMN IF NOT EXISTS expiry DateTime DEFAULT toDateTime(0) AFTER symbol"
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {PENDLE_ETH_BACKFILL_PROGRESS_TABLE} (
            asset_address String,
            chain_id UInt32,
            time_frame LowCardinality(String),
            cursor_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (asset_address, time_frame)
        """
    )

    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_events (
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name LowCardinality(String),
            topic0 String,
            topic1 Nullable(String),
            topic2 Nullable(String),
            topic3 Nullable(String),
            data String
        ) ENGINE = ReplacingMergeTree()
        PARTITION BY toStartOfMonth(block_timestamp)
        ORDER BY (block_number, tx_hash, log_index)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_market_params (
            market_id String,
            loan_token String,
            collateral_token String,
            loan_symbol String,
            collateral_symbol String,
            loan_decimals UInt8,
            collateral_decimals UInt8,
            oracle String,
            irm String,
            lltv UInt256,
            creation_block UInt64 DEFAULT 0,
            creation_timestamp DateTime DEFAULT toDateTime(0),
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY market_id
        """
    )
    for alter in (
        "ALTER TABLE morpho_market_params ADD COLUMN IF NOT EXISTS creation_block UInt64 DEFAULT 0",
        "ALTER TABLE morpho_market_params ADD COLUMN IF NOT EXISTS creation_timestamp DateTime DEFAULT toDateTime(0)",
        "ALTER TABLE morpho_market_params ADD COLUMN IF NOT EXISTS updated_at DateTime DEFAULT now()",
    ):
        ch.command(alter)
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_market_state (
            market_id String,
            total_supply_assets String,
            total_supply_shares String,
            total_borrow_assets String,
            total_borrow_shares String,
            collateral_assets String,
            fee_wad String,
            last_borrow_rate_wad String,
            last_update_timestamp DateTime DEFAULT toDateTime(0),
            last_event_block UInt64,
            last_event_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY market_id
        """
    )
    ch.command("ALTER TABLE morpho_market_state ADD COLUMN IF NOT EXISTS last_update_timestamp DateTime DEFAULT toDateTime(0)")
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_market_positions (
            market_id String,
            user String,
            supply_shares String,
            borrow_shares String,
            collateral_assets String,
            last_event_block UInt64,
            last_event_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (market_id, user)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_market_events (
            block_number UInt64,
            timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            market_id String,
            event_name LowCardinality(String),
            caller String,
            on_behalf String,
            receiver String,
            assets String,
            shares String,
            collateral_assets String,
            repaid_assets String,
            repaid_shares String,
            seized_assets String,
            bad_debt_assets String,
            bad_debt_shares String,
            interest_assets String,
            fee_shares String,
            fee_wad String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (market_id, block_number, tx_hash, log_index, event_name)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_market_oracle_support (
            market_id String,
            oracle_support LowCardinality(String),
            loan_symbol String,
            collateral_symbol String,
            loan_price_feeds Array(String),
            collateral_price_feeds Array(String),
            reason String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY market_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_market_metrics (
            timestamp DateTime,
            market_id String,
            entity_id String,
            loan_symbol LowCardinality(String),
            collateral_symbol LowCardinality(String),
            supply_usd Float64,
            borrow_usd Float64,
            collateral_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            loan_price_usd Float64,
            collateral_price_usd Float64,
            lltv Float64,
            oracle String,
            oracle_support LowCardinality(String),
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (market_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {MORPHO_CHAINLINK_TIMESERIES_TABLE} (
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
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )

    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_reserve_state (
            token String,
            symbol LowCardinality(String),
            decimals UInt8,
            total_supply_tokens Float64,
            total_borrow_tokens Float64,
            utilization Float64,
            borrow_apy Float64,
            supply_apy Float64,
            fee Float64,
            supply_exchange_price String,
            borrow_exchange_price String,
            last_event_block UInt64,
            last_event_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY token
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_reserve_oracle_support (
            token String,
            symbol LowCardinality(String),
            oracle_support LowCardinality(String),
            price_feeds Array(String),
            reason String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY token
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_reserve_metrics (
            timestamp DateTime,
            token String,
            entity_id String,
            symbol LowCardinality(String),
            total_supply_tokens Float64,
            total_borrow_tokens Float64,
            supply_usd Float64,
            borrow_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            oracle_support LowCardinality(String),
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (token, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {FLUID_TIMESERIES_TABLE} (
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
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_factory_raw_events (
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name LowCardinality(String),
            topic0 String,
            topic1 Nullable(String),
            topic2 Nullable(String),
            topic3 Nullable(String),
            data String
        ) ENGINE = ReplacingMergeTree()
        PARTITION BY toStartOfMonth(block_timestamp)
        ORDER BY (block_number, tx_hash, log_index)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_vault_backfill_raw_events (
            block_number UInt64,
            block_timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            contract String,
            event_name LowCardinality(String),
            topic0 String,
            topic1 Nullable(String),
            topic2 Nullable(String),
            topic3 Nullable(String),
            data String
        ) ENGINE = ReplacingMergeTree()
        PARTITION BY toStartOfMonth(block_timestamp)
        ORDER BY (block_number, tx_hash, log_index)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_vault_registry (
            vault_address String,
            name String,
            asset_symbol LowCardinality(String),
            asset_address String,
            owner String DEFAULT '',
            curator String DEFAULT '',
            guardian String DEFAULT '',
            allocator String DEFAULT '',
            fee_wad String DEFAULT '0',
            fee_recipient String DEFAULT '',
            timelock UInt64 DEFAULT 0,
            source LowCardinality(String) DEFAULT 'seed',
            active UInt8 DEFAULT 1,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY vault_address
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_vault_state (
            timestamp DateTime,
            block_number UInt64,
            vault_address String,
            total_assets String,
            total_supply String,
            share_price_usd Float64,
            tvl_usd Float64,
            asset_price_usd Float64,
            is_canonical_tvl UInt8 DEFAULT 0,
            snapshot_status LowCardinality(String) DEFAULT '',
            error String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_address, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_vault_allocations (
            timestamp DateTime,
            block_number UInt64,
            vault_address String,
            market_id String,
            cap String,
            supplied_assets String,
            supplied_usd Float64,
            allocation_share Float64,
            snapshot_status LowCardinality(String) DEFAULT '',
            error String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_address, market_id, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_vault_flows_hourly (
            timestamp DateTime,
            vault_address String,
            asset_symbol LowCardinality(String),
            deposit_assets String,
            withdraw_assets String,
            deposit_shares String,
            withdraw_shares String,
            transfer_shares String,
            deposit_usd Float64,
            withdraw_usd Float64,
            net_flow_usd Float64,
            event_count UInt64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_address, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS metamorpho_vault_events (
            block_number UInt64,
            timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            vault_address String,
            event_name LowCardinality(String),
            caller String,
            owner String,
            receiver String,
            market_id String,
            assets String,
            shares String,
            raw_data String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_address, block_number, tx_hash, log_index, event_name)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )

    try:
        ch.command(
            """
            INSERT INTO metamorpho_vault_registry
                (vault_address, name, asset_symbol, asset_address, source, active)
            SELECT lower(vault_address), name, asset_symbol, lower(asset_address), 'legacy_seed', 1
            FROM morpho_vault_meta
            WHERE lower(vault_address) NOT IN (
                SELECT vault_address FROM metamorpho_vault_registry FINAL
            )
            """
        )
    except Exception:
        pass

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
            WHERE protocol IN ('AAVE_MARKET', 'SPARK_MARKET', 'EULER_MARKET', 'FLUID_MARKET', 'MORPHO_MARKET')
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
            WHERE protocol IN ('AAVE_MARKET', 'SPARK_MARKET', 'EULER_MARKET', 'FLUID_MARKET', 'MORPHO_MARKET')
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
