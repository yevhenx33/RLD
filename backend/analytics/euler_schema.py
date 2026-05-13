"""ClickHouse DDL for the Euler V2 EVK analytics source."""


def ensure_euler_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_events (
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
        ORDER BY (block_number, tx_hash, log_index, contract, topic0)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_registry (
            vault_address String,
            asset_address String,
            asset_symbol LowCardinality(String),
            asset_decimals UInt8,
            verified UInt8,
            source LowCardinality(String),
            created_block UInt64 DEFAULT 0,
            created_timestamp DateTime DEFAULT toDateTime(0),
            name String DEFAULT '',
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY vault_address
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_state (
            vault_address String,
            total_shares String,
            total_borrows String,
            accumulated_fees String,
            cash String,
            interest_accumulator String,
            interest_rate String,
            interest_fee String,
            last_event_block UInt64,
            last_event_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY vault_address
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_events (
            block_number UInt64,
            timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            vault_address String,
            event_name LowCardinality(String),
            account String,
            sender String,
            receiver String,
            owner String,
            collateral String,
            assets String,
            shares String,
            repay_assets String,
            yield_balance String,
            raw_data String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_address, block_number, tx_hash, log_index, event_name)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_oracle_support (
            vault_address String,
            asset_address String,
            asset_symbol LowCardinality(String),
            verified UInt8,
            oracle_support LowCardinality(String),
            price_feeds Array(String),
            reason String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY vault_address
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_market_params (
            vault_address String,
            block_number UInt64,
            timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            interest_rate_model String,
            supply_cap_raw String,
            borrow_cap_raw String,
            config_flags UInt32,
            interest_fee UInt16,
            max_liquidation_discount UInt16,
            liquidation_cool_off_time UInt16,
            hook_target String,
            hooked_ops UInt32,
            oracle String,
            unit_of_account String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY vault_address
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_ltv_config (
            vault_address String,
            collateral_address String,
            block_number UInt64,
            timestamp DateTime,
            tx_hash String,
            log_index UInt32,
            borrow_ltv UInt16,
            liquidation_ltv UInt16,
            initial_liquidation_ltv UInt16,
            target_timestamp UInt64,
            ramp_duration UInt32,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (vault_address, collateral_address)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_vault_metrics (
            timestamp DateTime,
            vault_address String,
            entity_id String,
            asset_symbol LowCardinality(String),
            asset_address String,
            total_assets_raw String,
            total_borrows_raw String,
            supply_usd Float64,
            borrow_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            interest_fee Float64,
            oracle_support LowCardinality(String),
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (vault_address, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS euler_timeseries (
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
