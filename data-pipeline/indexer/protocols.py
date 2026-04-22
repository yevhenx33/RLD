"""Canonical protocol identifiers and shared mappings for data-pipeline."""

AAVE_MARKET = "AAVE_MARKET"
MORPHO_MARKET = "MORPHO_MARKET"
FLUID_MARKET = "FLUID_MARKET"
CHAINLINK_PRICES = "CHAINLINK_PRICES"
SOFR_RATES = "SOFR_RATES"
MORPHO_ALLOCATION = "MORPHO_ALLOCATION"
MORPHO_VAULT = "MORPHO_VAULT"

# Ready gate defaults for launch-critical data freshness.
READY_PROTOCOLS_DEFAULT = (
    AAVE_MARKET,
    MORPHO_MARKET,
    CHAINLINK_PRICES,
)

# Raw mempool table per protocol.
RAW_TABLE_BY_PROTOCOL = {
    AAVE_MARKET: "aave_events",
    MORPHO_MARKET: "morpho_events",
    FLUID_MARKET: "fluid_events",
    CHAINLINK_PRICES: "chainlink_events",
    SOFR_RATES: "raw_sofr_rates",
}

RAW_HEAD_QUERY_BY_PROTOCOL = {
    AAVE_MARKET: "SELECT max(block_number) FROM aave_events",
    MORPHO_MARKET: "SELECT max(block_number) FROM morpho_events",
    FLUID_MARKET: "SELECT max(block_number) FROM fluid_events",
    CHAINLINK_PRICES: "SELECT max(block_number) FROM chainlink_events",
    # Offchain source: normalize timestamp to pseudo-block (unix seconds).
    SOFR_RATES: "SELECT toUInt64(max(toUnixTimestamp(timestamp))) FROM raw_sofr_rates",
}

# processor_state aliases retained for backward compatibility.
PROCESSOR_STATE_ALIASES = {
    AAVE_MARKET: (AAVE_MARKET,),
    MORPHO_MARKET: (MORPHO_MARKET,),
    FLUID_MARKET: (FLUID_MARKET,),
    CHAINLINK_PRICES: (CHAINLINK_PRICES, "CHAINLINK"),
    SOFR_RATES: (SOFR_RATES,),
}

