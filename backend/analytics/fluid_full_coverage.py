"""Fluid full-coverage product schema and helpers.

These tables separate canonical Liquidity Layer reserve TVL from Fluid product
exposure surfaces. Product rows are not additive TVL unless marked canonical.
"""

from __future__ import annotations

from dataclasses import dataclass

ETHEREUM_CHAIN_ID = 1
FLUID_LIQUIDITY = "0x52aa899454998be5b000ad077a46bbe360f4e497"
FLUID_LENDING_FACTORY = "0x54b91a0d94cb471f37f949c60f7fa7935b551d03"
FLUID_VAULT_FACTORY = "0x324c5dc1fc42c7a4d43d92df1eba58a54d13bf2d"
FLUID_DEX_FACTORY = "0x91716c4eda1fb55e84bf8b4c7085f84285c19085"
FLUID_VAULT_RESOLVER = "0x814c8c7ceb1411b364c2940c4b9380e739e06686"
FLUID_DEX_RESOLVER = "0x71783f64719899319b56bda4f27e1219d9af9a3d"
FLUID_REVENUE_RESOLVER = "0xfe4affad55c7aec012346195654634f7c786fa2c"
FLUID_STETH_RESOLVER = ""
FLUID_PRODUCTS = ("LIQUIDITY", "FTOKEN", "VAULT", "DEX", "REVENUE", "STETH")
FLUID_PRODUCT_PROTOCOLS = ("FLUID_FTOKEN", "FLUID_VAULT", "FLUID_DEX", "FLUID_REVENUE", "FLUID_STETH")

# Assets that must not be priced by a silent BTC/USD 1:1 shortcut. They are only
# PRICED when a worker/resolver has written an explicit OK oracle snapshot.
BTC_DERIVATIVE_SYMBOLS = {"LBTC", "eBTC", "tBTC", "FBTC", "BTC.b"}

# Currently observed Fluid assets whose prices depend on wrappers, capped rates,
# Redstone, TWAPs, accountants, or protocol-specific rate providers.
FLUID_WRAPPER_ORACLE_HINTS = {
    "sUSDe": "ERC4626/Ethena share rate + USDe / USD",
    "USR": "Resolv/USR oracle or peg governance source",
    "wstUSR": "wstUSR wrapper share rate + USR price",
    "XAUt": "XAUt / USD or gold oracle feed",
    "fxUSD": "fxUSD oracle or peg governance source",
    "deUSD": "deUSD oracle or peg governance source",
    "USDTB": "USDTB oracle or peg governance source",
    "iUSD": "iUSD oracle or peg governance source",
    "sUSDS": "Savings USDS share rate + USDS / USD",
    "syrupUSDC": "Maple syrupUSDC exchange rate + USDC / USD",
    "srUSDe": "Ethena srUSDe share rate + USDe / USD",
    "csUSDL": "Concrete/Veda accountant + USDC/USD base",
    "syrupUSDT": "Maple syrupUSDT exchange rate + USDT / USD",
    "jrUSDe": "Ethena jrUSDe share rate + USDe / USD",
    "FLUID": "FLUID token market oracle",
    "RLP": "RLP price provider",
    "ezETH": "Renzo ezETH / ETH oracle + ETH / USD",
    "mETH": "Mantle mETH / ETH oracle + ETH / USD",
    "osETH": "StakeWise osETH / ETH oracle + ETH / USD",
    "rsETH": "Kelp rsETH / ETH oracle + ETH / USD",
    "weETHs": "weETHs wrapper rate + ETH / USD",
    "LBTC": "Lombard BTC derivative oracle + BTC / USD",
    "eBTC": "Ether.fi eBTC oracle + BTC / USD",
    "tBTC": "Threshold tBTC oracle + BTC / USD",
}


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
    return FLUID_WRAPPER_ORACLE_HINTS.get(symbol, "")


def needs_explicit_snapshot(symbol: str) -> bool:
    return symbol in BTC_DERIVATIVE_SYMBOLS or bool(support_hint(symbol))
