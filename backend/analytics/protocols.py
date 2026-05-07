"""Canonical protocol identifiers and shared mappings for analytics."""

AAVE_MARKET = "AAVE_MARKET"
FLUID_MARKET = "FLUID_MARKET"
FLUID_FTOKEN = "FLUID_FTOKEN"
FLUID_VAULT = "FLUID_VAULT"
FLUID_DEX = "FLUID_DEX"
FLUID_REVENUE = "FLUID_REVENUE"
FLUID_STETH = "FLUID_STETH"
MORPHO_MARKET = "MORPHO_MARKET"
METAMORPHO_FACTORY = "METAMORPHO_FACTORY"
METAMORPHO_VAULT = "METAMORPHO_VAULT"
METAMORPHO_VAULT_BACKFILL = "METAMORPHO_VAULT_BACKFILL"
CHAINLINK_PRICES = "CHAINLINK_PRICES"
SOFR_RATES = "SOFR_RATES"
PENDLE_ETHEREUM_PT_YT_PRICES = "PENDLE_ETHEREUM_PT_YT_PRICES"

# Ready gate defaults for launch-critical data freshness.
READY_PROTOCOLS_DEFAULT = (
    AAVE_MARKET,
    CHAINLINK_PRICES,
    SOFR_RATES,
)

# Raw mempool table per protocol.
RAW_TABLE_BY_PROTOCOL = {
    AAVE_MARKET: "aave_events",
    FLUID_MARKET: "fluid_events",
    FLUID_FTOKEN: "fluid_product_raw_events",
    FLUID_VAULT: "fluid_product_raw_events",
    FLUID_DEX: "fluid_product_raw_events",
    FLUID_REVENUE: "fluid_product_raw_events",
    FLUID_STETH: "fluid_product_raw_events",
    MORPHO_MARKET: "morpho_events",
    METAMORPHO_FACTORY: "metamorpho_factory_raw_events",
    METAMORPHO_VAULT: "metamorpho_vault_raw_events",
    METAMORPHO_VAULT_BACKFILL: "metamorpho_vault_backfill_raw_events",
    CHAINLINK_PRICES: "chainlink_events",
    SOFR_RATES: "raw_sofr_rates",
    PENDLE_ETHEREUM_PT_YT_PRICES: "pendle_eth_price_ohlcv",
}

RAW_HEAD_QUERY_BY_PROTOCOL = {
    AAVE_MARKET: "SELECT max(block_number) FROM aave_events",
    FLUID_MARKET: "SELECT max(block_number) FROM fluid_events",
    FLUID_FTOKEN: "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = 'FTOKEN'",
    FLUID_VAULT: "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = 'VAULT'",
    FLUID_DEX: "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = 'DEX'",
    FLUID_REVENUE: "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = 'REVENUE'",
    FLUID_STETH: "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = 'STETH'",
    MORPHO_MARKET: "SELECT max(block_number) FROM morpho_events",
    METAMORPHO_FACTORY: "SELECT max(block_number) FROM metamorpho_factory_raw_events",
    METAMORPHO_VAULT: "SELECT max(block_number) FROM metamorpho_vault_raw_events",
    METAMORPHO_VAULT_BACKFILL: "SELECT max(block_number) FROM metamorpho_vault_backfill_raw_events",
    CHAINLINK_PRICES: "SELECT max(block_number) FROM chainlink_events",
    # Offchain source: normalize timestamp to pseudo-block (unix seconds).
    SOFR_RATES: "SELECT toUInt64(max(toUnixTimestamp(timestamp))) FROM raw_sofr_rates",
    PENDLE_ETHEREUM_PT_YT_PRICES: "SELECT toUInt64(max(toUnixTimestamp(timestamp))) FROM pendle_eth_price_ohlcv",
}

# processor_state aliases retained for backward compatibility.
PROCESSOR_STATE_ALIASES = {
    AAVE_MARKET: (AAVE_MARKET,),
    FLUID_MARKET: (FLUID_MARKET,),
    FLUID_FTOKEN: (FLUID_FTOKEN,),
    FLUID_VAULT: (FLUID_VAULT,),
    FLUID_DEX: (FLUID_DEX,),
    FLUID_REVENUE: (FLUID_REVENUE,),
    FLUID_STETH: (FLUID_STETH,),
    MORPHO_MARKET: (MORPHO_MARKET,),
    METAMORPHO_FACTORY: (METAMORPHO_FACTORY,),
    METAMORPHO_VAULT: (METAMORPHO_VAULT,),
    METAMORPHO_VAULT_BACKFILL: (METAMORPHO_VAULT_BACKFILL,),
    CHAINLINK_PRICES: (CHAINLINK_PRICES, "CHAINLINK"),
    SOFR_RATES: (SOFR_RATES,),
    PENDLE_ETHEREUM_PT_YT_PRICES: (PENDLE_ETHEREUM_PT_YT_PRICES,),
}
