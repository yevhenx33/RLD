"""Fluid full-coverage product schema and helpers.

These tables separate canonical Liquidity Layer reserve TVL from Fluid product
exposure surfaces. Product rows are not additive TVL unless marked canonical.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fluid_registry import (
    ETHEREUM_CHAIN_ID,
    FLUID_DEX_FACTORY,
    FLUID_DEX_RESOLVER,
    FLUID_LENDING_FACTORY,
    FLUID_LIQUIDITY,
    FLUID_PRODUCTS,
    FLUID_PRODUCT_PROTOCOLS,
    FLUID_REVENUE_RESOLVER,
    FLUID_STETH_RESOLVER,
    FLUID_VAULT_FACTORY,
    FLUID_VAULT_RESOLVER,
    needs_explicit_snapshot as _registry_needs_explicit_snapshot,
    support_hint as _registry_support_hint,
)


@dataclass(frozen=True)
class FluidContract:
    chain_id: int
    product_type: str
    contract: str
    factory: str = ""
    name: str = ""
    created_block: int = 0
    active: bool = True
    resolver: str = ""
    metadata: str = ""


def normalize_address(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).lower()
    return value if value.startswith("0x") else "0x" + value


def ensure_fluid_full_coverage_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_contract_registry (
            chain_id UInt32,
            product_type LowCardinality(String),
            contract String,
            factory String,
            name String,
            created_block UInt64,
            active UInt8,
            resolver String,
            metadata String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (chain_id, product_type, contract)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_product_snapshots (
            chain_id UInt32,
            product_type LowCardinality(String),
            product_id String,
            timestamp DateTime,
            block_number UInt64,
            symbol LowCardinality(String),
            underlying String,
            collateral_token String,
            debt_token String,
            supply_usd Float64,
            borrow_usd Float64,
            collateral_usd Float64,
            liquidity_usd Float64,
            volume_usd Float64,
            fees_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            ltv Float64,
            liquidation_threshold Float64,
            position_count UInt64,
            is_canonical_tvl UInt8,
            pricing_status LowCardinality(String),
            oracle_status LowCardinality(String),
            snapshot_status LowCardinality(String),
            provenance String,
            error String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (chain_id, product_type, product_id, timestamp, block_number)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )

    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_product_components (
            chain_id UInt32,
            product_type LowCardinality(String),
            product_id String,
            timestamp DateTime,
            block_number UInt64,
            component_type LowCardinality(String),
            token String,
            symbol LowCardinality(String),
            raw_amount String,
            decimals UInt8,
            price_usd Float64,
            amount_usd Float64,
            pricing_status LowCardinality(String),
            oracle_status LowCardinality(String),
            provenance String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (chain_id, product_type, product_id, timestamp, block_number, component_type, token)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_asset_oracle_support (
            chain_id UInt32,
            asset String,
            symbol LowCardinality(String),
            oracle_support LowCardinality(String),
            oracle_type LowCardinality(String),
            price_feeds Array(String),
            snapshot_subjects Array(String),
            reason String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (chain_id, asset)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_rpc_validation_runs (
            run_id String,
            chain_id UInt32,
            target LowCardinality(String),
            started_at DateTime,
            finished_at DateTime,
            checked_count UInt32,
            mismatch_count UInt32,
            max_relative_supply_diff Float64,
            max_relative_borrow_diff Float64,
            status LowCardinality(String),
            details String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (chain_id, target, run_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS fluid_product_raw_events (
            chain_id UInt32,
            product_type LowCardinality(String),
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
        ORDER BY (chain_id, product_type, block_number, log_index, contract, topic0)
        TTL block_timestamp + INTERVAL 72 MONTH DELETE
        """
    )


def seed_core_fluid_contracts(ch) -> int:
    rows = [
        [
            ETHEREUM_CHAIN_ID,
            "LIQUIDITY",
            normalize_address(FLUID_LIQUIDITY),
            "",
            "Fluid Liquidity Layer",
            19_258_464,
            1,
            "",
            "canonical_reserve_tvl=true",
        ],
        [
            ETHEREUM_CHAIN_ID,
            "FTOKEN",
            normalize_address(FLUID_LENDING_FACTORY),
            "",
            "Fluid Lending Factory",
            0,
            1,
            "",
            "discovers_fTokens=true",
        ],
        [
            ETHEREUM_CHAIN_ID,
            "VAULT",
            normalize_address(FLUID_VAULT_FACTORY),
            "",
            "Fluid Vault Factory",
            0,
            1,
            "",
            "discovers_vaults=true",
        ],
        [
            ETHEREUM_CHAIN_ID,
            "DEX",
            normalize_address(FLUID_DEX_FACTORY),
            "",
            "Fluid DEX Factory",
            23_926_343,
            1,
            normalize_address(FLUID_DEX_RESOLVER),
            "discovers_dexes=true",
        ],
        [
            ETHEREUM_CHAIN_ID,
            "VAULT",
            normalize_address(FLUID_VAULT_RESOLVER),
            normalize_address(FLUID_VAULT_FACTORY),
            "Fluid Vault Resolver",
            0,
            1,
            normalize_address(FLUID_VAULT_RESOLVER),
            "resolver_for=vaults",
        ],
        [
            ETHEREUM_CHAIN_ID,
            "REVENUE",
            normalize_address(FLUID_REVENUE_RESOLVER),
            normalize_address(FLUID_LIQUIDITY),
            "Fluid Revenue Resolver",
            20_138_675,
            1,
            normalize_address(FLUID_REVENUE_RESOLVER),
            "resolver_for=revenue",
        ],
        [
            ETHEREUM_CHAIN_ID,
            "STETH",
            normalize_address(FLUID_STETH_RESOLVER),
            normalize_address(FLUID_LIQUIDITY),
            "Fluid stETH Resolver",
            0,
            0,
            normalize_address(FLUID_STETH_RESOLVER),
            "resolver_unverified_on_ethereum=true",
        ],
    ]
    ch.insert(
        "fluid_contract_registry",
        rows,
        column_names=["chain_id", "product_type", "contract", "factory", "name", "created_block", "active", "resolver", "metadata"],
    )
    return len(rows)


def support_hint(symbol: str) -> str:
    return _registry_support_hint(symbol)


def needs_explicit_snapshot(symbol: str) -> bool:
    return _registry_needs_explicit_snapshot(symbol)
