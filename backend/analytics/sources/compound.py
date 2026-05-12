"""Compound v2 and Compound V3 Ethereum market analytics sources."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from typing import Optional

import hypersync
import pandas as pd

from ..base import (
    BaseSource,
    forward_fill_hourly,
    insert_df_batched,
    insert_rows_batched,
    refresh_api_protocol_tvl_weekly,
    rewrite_protocol_window_if_enabled,
    upsert_api_market_latest,
    upsert_market_timeseries,
)
from ..protocols import COMPOUND_V2_MARKET, COMPOUND_V3_MARKET
from ..tokens import TOKENS, get_chainlink_prices, get_usd_price

log = logging.getLogger("indexer.compound")

ETH_PSEUDO_ADDRESS = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
COMPOUND_V2_UNITROLLER = "0x3d9819210a31b4961b30ef54be2aed79b9c9cd3b"
COMPOUND_V2_GENESIS_BLOCK = 7_710_677
COMPOUND_V3_USDC = "0xc3d688b66703497daa19211eedff47f25384cdc3"
COMPOUND_V3_WETH = "0xa17581a9e3356d9a858b789d68b4d866e593ae94"
COMPOUND_V3_CONFIGURATOR = "0x316f9708bb98af7da9c68c1c3b5e79039cd336e3"
COMPOUND_V3_GENESIS_BLOCK = 15_300_000
COMPOUND_V3_USDT = "0x3afdc9bca9213a35503b077a6072f3d0d5ab0840"
COMPOUND_V3_WBTC = "0xe85dc543813b8c2cfeaac371517b925a166a9293"
COMPOUND_V3_WSTETH = "0x3d0bb1ccab520a66e607822fc55bc921738fafe3"
COMPOUND_V3_USDS = "0x5d409e56d886231adaf00c8775665ad0f9897b56"
COMPOUND_V3_LATEST_ENRICHED_TABLE = "api_compound_v3_market_latest_enriched"
COMPOUND_V3_COLLATERAL_LATEST_TABLE = "api_compound_v3_collateral_latest"
COMPOUND_V3_FLOW_DAILY_TABLE = "api_compound_v3_market_flow_daily_agg"
SECONDS_PER_YEAR = 31_536_000
BLOCKS_PER_YEAR = 2_102_400
WAD = 10**18
BASE_INDEX_SCALE = 10**15

TOPIC_V2_MINT = "0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f"
TOPIC_V2_REDEEM = "0xe5b754fb1abb7f01b499791d0b820ae3b6af3424ac1c59768edb53f4ec31a929"
TOPIC_V2_BORROW = "0x13ed6866d4e1ee6da46f845c46d7e54120883d75c5ea9a2dacc1c4ca8984ab80"
TOPIC_V2_REPAY = "0x1a2a22cb034d26d1854bdc6666a5b91fe25efbbb5dcad3b0355478d6f5c362a1"
TOPIC_V2_LIQUIDATE = "0x298637f684da70674f26509b10f07ec2fbc77a335ab1e7d6215a4b2484d8bb52"
TOPIC_V2_ACCRUE = "0x4dec04e750ca11537cabcd8a9eab06494de08da3735bc8871cd41250e190bc04"
TOPIC_V2_RESERVE_FACTOR = "0xaaa68312e2ea9d50e16af5068410ab56e1a1fd06037b1a35664812c30f821460"
TOPIC_V2_RESERVES_ADDED = "0xa91e67c5ea634cd43a12c5a482724b03de01e85ca68702a53d0c2f45cb7c1dc5"
TOPIC_V2_RESERVES_REDUCED = "0x3bad0c59cf2f06e7314077049f48a93578cd16f5ef92329f1dab1420a99c177e"

TOPIC_V3_SUPPLY = "0xd1cf3d156d5f8f0d50f6c122ed609cec09d35c9b9fb3fff6ea0959134dae424e"
TOPIC_V3_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_V3_WITHDRAW = "0x9b1bfa7fa9ee420a16e124f794c35ac9f90472acc99140eb2f6447c714cad8eb"
TOPIC_V3_SUPPLY_COLLATERAL = "0xfa56f7b24f17183d81894d3ac2ee654e3c26388d17a28dbd9549b8114304e1f4"
TOPIC_V3_TRANSFER_COLLATERAL = "0x29db89d45e1a802b4d55e202984fce9faf1d30aedf86503ff1ea0ed9ebb64201"
TOPIC_V3_WITHDRAW_COLLATERAL = "0xd6d480d5b3068db003533b170d67561494d72e3bf9fa40a266471351ebba9e16"
TOPIC_V3_ABSORB_DEBT = "0x1547a878dc89ad3c367b6338b4be6a65a5dd74fb77ae044da1e8747ef1f4f62f"
TOPIC_V3_ABSORB_COLLATERAL = "0x9850ab1af75177e4a9201c65a2cf7976d5d28e40ef63494b44366f86b2f9412e"
TOPIC_V3_BUY_COLLATERAL = "0xf891b2a411b0e66a5f0a6ff1368670fefa287a13f541eb633a386a1a9cc7046b"
TOPIC_V3_WITHDRAW_RESERVES = "0xec4431f2ba1a9382f6b0c4352b888cba6f7db91667d9f776abe5ad8ddc5401b6"
TOPIC_V3_COMET_DEPLOYED = "0x3da528dfe78562a1f409134989443b5f21ee92023a64b90dedeb2002415189b6"
TOPIC_V3_SET_SUPPLY_KINK = "0x35ebb489f572b08259fa0a007e62d8390043159fcf4a6c9a517dd1eb4dc77dfc"
TOPIC_V3_SET_SUPPLY_RATE_LOW = "0xc936b3eb07b584b686d11042214266fed11036dbc226159d67b3cfaa4ce26f78"
TOPIC_V3_SET_SUPPLY_RATE_HIGH = "0x9fd77f9236bc26e7c30eaa679ccff4200f9a813e5950b2904c29f11d0b5f2a5e"
TOPIC_V3_SET_SUPPLY_RATE_BASE = "0x56027dd4756fabe0c40c7de2c732b95d170796feaf975883a17a087b3718e6b9"
TOPIC_V3_SET_BORROW_KINK = "0xe4e8221220a251ab6772c13e04f6c54532602ec260a26f48ff23fa8b11f41be7"
TOPIC_V3_SET_BORROW_RATE_LOW = "0xe5d8ea5f9f9bc1e3132e593561d5b440062de5e0d5585a7046c87ca172a60d46"
TOPIC_V3_SET_BORROW_RATE_HIGH = "0xec47838861ac4faa72ef1cf4d982f923880203912cd0c53d6fea2e611562f82c"
TOPIC_V3_SET_BORROW_RATE_BASE = "0x7e237d05d262a573a6b2d126c2065264a3bcabb68e67a1d5a1e88b1dbef6666d"

V2_EVENT_MAP = {
    TOPIC_V2_MINT: "Mint",
    TOPIC_V2_REDEEM: "Redeem",
    TOPIC_V2_BORROW: "Borrow",
    TOPIC_V2_REPAY: "RepayBorrow",
    TOPIC_V2_LIQUIDATE: "LiquidateBorrow",
    TOPIC_V2_ACCRUE: "AccrueInterest",
    TOPIC_V2_RESERVE_FACTOR: "NewReserveFactor",
    TOPIC_V2_RESERVES_ADDED: "ReservesAdded",
    TOPIC_V2_RESERVES_REDUCED: "ReservesReduced",
}

V3_EVENT_MAP = {
    TOPIC_V3_SUPPLY: "Supply",
    TOPIC_V3_TRANSFER: "Transfer",
    TOPIC_V3_WITHDRAW: "Withdraw",
    TOPIC_V3_SUPPLY_COLLATERAL: "SupplyCollateral",
    TOPIC_V3_TRANSFER_COLLATERAL: "TransferCollateral",
    TOPIC_V3_WITHDRAW_COLLATERAL: "WithdrawCollateral",
    TOPIC_V3_ABSORB_DEBT: "AbsorbDebt",
    TOPIC_V3_ABSORB_COLLATERAL: "AbsorbCollateral",
    TOPIC_V3_BUY_COLLATERAL: "BuyCollateral",
    TOPIC_V3_WITHDRAW_RESERVES: "WithdrawReserves",
    TOPIC_V3_COMET_DEPLOYED: "CometDeployed",
    TOPIC_V3_SET_SUPPLY_KINK: "SetSupplyKink",
    TOPIC_V3_SET_SUPPLY_RATE_LOW: "SetSupplyPerYearInterestRateSlopeLow",
    TOPIC_V3_SET_SUPPLY_RATE_HIGH: "SetSupplyPerYearInterestRateSlopeHigh",
    TOPIC_V3_SET_SUPPLY_RATE_BASE: "SetSupplyPerYearInterestRateBase",
    TOPIC_V3_SET_BORROW_KINK: "SetBorrowKink",
    TOPIC_V3_SET_BORROW_RATE_LOW: "SetBorrowPerYearInterestRateSlopeLow",
    TOPIC_V3_SET_BORROW_RATE_HIGH: "SetBorrowPerYearInterestRateSlopeHigh",
    TOPIC_V3_SET_BORROW_RATE_BASE: "SetBorrowPerYearInterestRateBase",
}

V3_CONFIG_FIELD_MAP = {
    "SetSupplyKink": "supply_kink",
    "SetSupplyPerYearInterestRateSlopeLow": "supply_slope_low",
    "SetSupplyPerYearInterestRateSlopeHigh": "supply_slope_high",
    "SetSupplyPerYearInterestRateBase": "supply_base",
    "SetBorrowKink": "borrow_kink",
    "SetBorrowPerYearInterestRateSlopeLow": "borrow_slope_low",
    "SetBorrowPerYearInterestRateSlopeHigh": "borrow_slope_high",
    "SetBorrowPerYearInterestRateBase": "borrow_base",
}

STATIC_V2_MARKETS = {
    "0x4ddc2d193948926d02f9b1fe9e1daa0718270ed5": ("cETH", ETH_PSEUDO_ADDRESS, "ETH", 18, 8),
    "0x5d3a536e4d6dbd6114cc1ead35777bab948e3643": ("cDAI", "0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", 18, 8),
    "0x39aa39c021dfbae8fac545936693ac917d5e7563": ("cUSDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6, 8),
    "0xf650c3d88d12db855b8bf7d11be6c55a4e07dcc9": ("cUSDT", "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6, 8),
    "0xccf4429db6322d5c611ee964527d42e5d685dd6a": ("cWBTC2", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8, 8),
    "0x35a18000230da775cac24873d00ff85bccded550": ("cUNI", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "UNI", 18, 8),
    "0x70e36f6bf80a52b3b46b3af8e106cc0ed743e8e4": ("cCOMP", "0xc00e94cb662c3520282e6f5717214004a7f26888", "COMP", 18, 8),
    "0xface851a4921ce59e912d19329929ce6da6eb0c7": ("cLINK", "0x514910771af9ca656af840dff83e8264ecf986ca", "LINK", 18, 8),
}

STATIC_V3_COMETS = {
    COMPOUND_V3_USDC: ("cUSDCv3", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6, 0.80, 0.0325, 0.40, 0.0, 0.80, 0.035, 0.25, 0.015),
    COMPOUND_V3_WETH: ("cWETHv3", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", 18, 0.90, 0.0283824, 0.606656770591536, 0.0, 0.90, 0.051715000017648, 0.5171500338884639, 0.009945209642544),
    COMPOUND_V3_USDT: ("cUSDTv3", "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", 6, 0.90, 0.059, 2.90, 0.0, 0.90, 0.061, 3.20, 0.015),
    COMPOUND_V3_WBTC: ("cWBTCv3", "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", "WBTC", 8, 0.85, 0.012, 1.0, 0.0, 0.85, 0.014, 1.15, 0.01),
    COMPOUND_V3_WSTETH: ("cWstETHv3", "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0", "wstETH", 18, 0.85, 0.012, 1.0, 0.0, 0.85, 0.014, 1.15, 0.01),
    COMPOUND_V3_USDS: ("cUSDSv3", "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS", 18, 0.90, 0.039, 3.60, 0.0, 0.90, 0.0333, 4.0, 0.015),
}


@dataclass
class CompoundV2Market:
    ctoken: str
    symbol: str
    underlying: str
    underlying_symbol: str
    underlying_decimals: int
    ctoken_decimals: int = 8
    collateral_factor: float = 0.0


@dataclass
class CompoundV2State:
    total_supply_ctokens: int = 0
    total_borrows: int = 0
    total_reserves: int = 0
    cash: int = 0
    borrow_index: int = WAD
    reserve_factor: float = 0.0
    exchange_rate: int = 0
    supply_apy: float = 0.0
    borrow_apy: float = 0.0
    last_event_block: int = 0
    last_event_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


@dataclass
class CompoundV3Comet:
    comet: str
    symbol: str
    base_token: str
    base_symbol: str
    base_decimals: int
    supply_kink: float = 0.0
    supply_slope_low: float = 0.0
    supply_slope_high: float = 0.0
    supply_base: float = 0.0
    borrow_kink: float = 0.0
    borrow_slope_low: float = 0.0
    borrow_slope_high: float = 0.0
    borrow_base: float = 0.0


@dataclass
class CompoundV3State:
    total_supply_base: int = 0
    total_borrow_base: int = 0
    total_supply_principal: int = 0
    total_borrow_principal: int = 0
    base_supply_index: int = BASE_INDEX_SCALE
    base_borrow_index: int = BASE_INDEX_SCALE
    reserves_base: int = 0
    last_event_block: int = 0
    last_event_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)
    last_accrual_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


def _topic_address(topics: list[str], idx: int) -> str:
    if idx >= len(topics) or not topics[idx]:
        return ""
    return "0x" + str(topics[idx]).lower().removeprefix("0x")[-40:]


def _words(data: str | None) -> list[int]:
    raw = str(data or "").removeprefix("0x")
    if len(raw) % 64 != 0:
        raw = raw[: len(raw) - (len(raw) % 64)]
    return [int(raw[i : i + 64], 16) for i in range(0, len(raw), 64) if len(raw[i : i + 64]) == 64]


def _block_ts(block_number: int, block_ts_map) -> datetime.datetime:
    ts = block_ts_map.get(block_number, datetime.datetime.now(datetime.UTC))
    return ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts


def _safe_ratio(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _clean_address(value: str | None) -> str:
    raw = str(value or "").lower()
    if raw.startswith("0x") and len(raw) >= 42:
        return "0x" + raw[-40:]
    return raw


def _token_meta(address: str, fallback: str = "") -> tuple[str, int]:
    if address.lower() == ETH_PSEUDO_ADDRESS:
        return "ETH", 18
    symbol, decimals = TOKENS.get(address.removeprefix("0x").lower(), (fallback or address[:10], 18))
    return str(symbol), int(decimals)


def _token_amount_from_raw(raw_value: object, decimals: int) -> float:
    try:
        raw_int = int(str(raw_value or "0"))
    except (TypeError, ValueError):
        return 0.0
    scale = 10 ** max(0, int(decimals or 0))
    return float(raw_int / scale) if scale else float(raw_int)


def _latest_feed_prices(ch, feeds: tuple[str, ...]) -> dict[str, float]:
    if ch is None or not feeds:
        return {}
    escaped_feeds = ", ".join("'" + str(feed).replace("'", "") + "'" for feed in feeds)
    try:
        rows = ch.query(f"""
            SELECT feed, argMax(price, timestamp) AS price
            FROM chainlink_prices
            WHERE feed IN ({escaped_feeds})
            GROUP BY feed
            HAVING price > 0
        """).result_rows
    except Exception:
        return {}
    return {str(row[0]): float(row[1] or 0.0) for row in rows}


def ensure_compound_v3_serving_tables(ch) -> None:
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {COMPOUND_V3_LATEST_ENRICHED_TABLE} (
            timestamp DateTime,
            entity_id String,
            symbol LowCardinality(String),
            protocol LowCardinality(String),
            supply_usd Float64,
            borrow_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            base_token String,
            base_symbol LowCardinality(String),
            base_decimals UInt8,
            total_supply_base String,
            total_borrow_base String,
            lltv_min Float64,
            lltv_max Float64,
            is_active UInt8,
            has_supply UInt8,
            has_borrow UInt8,
            last_event_timestamp DateTime,
            last_priced_timestamp DateTime,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY entity_id
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {COMPOUND_V3_COLLATERAL_LATEST_TABLE} (
            comet String,
            asset String,
            symbol LowCardinality(String),
            price_feed String,
            oracle_name LowCardinality(String),
            token_decimals UInt8,
            price_usd Float64,
            borrow_collateral_factor Float64,
            liquidate_collateral_factor Float64,
            liquidation_factor Float64,
            supply_cap String,
            supply_cap_tokens Float64,
            total_collateral String,
            total_collateral_tokens Float64,
            collateral_usd Float64,
            borrow_enabled UInt8,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (comet, asset)
        """
    )
    ch.command(
        f"""
        CREATE TABLE IF NOT EXISTS {COMPOUND_V3_FLOW_DAILY_TABLE} (
            day DateTime,
            entity_id String,
            symbol LowCardinality(String),
            supply_inflow_usd Float64,
            supply_outflow_usd Float64,
            borrow_inflow_usd Float64,
            borrow_outflow_usd Float64,
            net_supply_flow_usd Float64,
            net_borrow_flow_usd Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(day)
        ORDER BY (entity_id, day)
        TTL day + INTERVAL 36 MONTH DELETE
        """
    )


def refresh_compound_v3_serving_tables(ch, min_ts=None, max_ts=None) -> int:
    if ch is None:
        return 0
    ensure_compound_v3_serving_tables(ch)
    rows_written = 0
    ch.command(
        f"""
        INSERT INTO {COMPOUND_V3_LATEST_ENRICHED_TABLE} (
            timestamp, entity_id, symbol, protocol, supply_usd, borrow_usd,
            supply_apy, borrow_apy, utilization, price_usd, base_token,
            base_symbol, base_decimals, total_supply_base, total_borrow_base,
            lltv_min, lltv_max, is_active, has_supply, has_borrow,
            last_event_timestamp, last_priced_timestamp
        )
        SELECT
            l.timestamp,
            l.entity_id,
            l.symbol,
            '{COMPOUND_V3_MARKET}' AS protocol,
            toFloat64(l.supply_usd) AS supply_usd,
            toFloat64(l.borrow_usd) AS borrow_usd,
            toFloat64(l.supply_apy) AS supply_apy,
            toFloat64(l.borrow_apy) AS borrow_apy,
            if(toFloat64(l.supply_usd) > 0, toFloat64(l.borrow_usd) / toFloat64(l.supply_usd), 0.0) AS utilization,
            toFloat64(l.price_usd) AS price_usd,
            if(r.comet != '' AND r.base_token != '', r.base_token, l.target_id) AS base_token,
            if(r.comet != '' AND r.base_symbol != '', r.base_symbol, l.symbol) AS base_symbol,
            toUInt8(if(r.comet != '', r.base_decimals, 0)) AS base_decimals,
            if(s.comet != '', s.total_supply_base, '') AS total_supply_base,
            if(s.comet != '', s.total_borrow_base, '') AS total_borrow_base,
            ifNull(ltv.lltv_min, 0.0) AS lltv_min,
            ifNull(ltv.lltv_max, 0.0) AS lltv_max,
            toUInt8(if(s.comet != '', s.last_event_timestamp > toDateTime(0), 0)) AS is_active,
            toUInt8(toFloat64(l.supply_usd) > 0) AS has_supply,
            toUInt8(toFloat64(l.borrow_usd) > 0) AS has_borrow,
            if(s.comet != '', s.last_event_timestamp, toDateTime(0)) AS last_event_timestamp,
            l.timestamp AS last_priced_timestamp
        FROM (
            SELECT entity_id, symbol, target_id, timestamp, supply_usd, borrow_usd,
                   supply_apy, borrow_apy, price_usd
            FROM api_market_latest FINAL
            WHERE protocol = '{COMPOUND_V3_MARKET}'
        ) AS l
        LEFT JOIN (
            SELECT comet, argMax(base_token, updated_at) AS base_token,
                   argMax(base_symbol, updated_at) AS base_symbol,
                   argMax(base_decimals, updated_at) AS base_decimals
            FROM compound_v3_comet_registry FINAL
            WHERE active = 1
            GROUP BY comet
        ) AS r ON r.comet = l.entity_id
        LEFT JOIN (SELECT * FROM compound_v3_comet_state FINAL) AS s
          ON s.comet = l.entity_id
        LEFT JOIN (
            SELECT comet,
                   min(borrow_collateral_factor) AS lltv_min,
                   max(borrow_collateral_factor) AS lltv_max
            FROM compound_v3_collateral_registry FINAL
            WHERE active = 1 AND borrow_collateral_factor > 0
            GROUP BY comet
        ) AS ltv ON ltv.comet = l.entity_id
        """
    )
    try:
        rows_written += int(ch.command(f"SELECT count() FROM {COMPOUND_V3_LATEST_ENRICHED_TABLE} FINAL") or 0)
    except Exception:
        pass

    try:
        eth_price, btc_price = get_chainlink_prices(ch)
    except Exception:
        eth_price, btc_price = 2000.0, 70000.0
    extra_prices = _latest_feed_prices(
        ch,
        (
            "USDC / USD", "USDT / USD", "USDe / USD", "USDS / USD",
            "ETH / USD", "BTC / USD", "STETH / USD", "STETH / ETH",
            "weETH / ETH", "weETH/ETH exchange rate", "RETH / ETH",
            "WBTC / BTC", "cbBTC / USD", "TBTC / USD", "LINK / USD",
            "UNI / USD", "MKR / USD", "SKY / USD", "COMP / USD",
        ),
    )
    collateral_rows = ch.query(
        """
        SELECT c.comet, c.asset, c.price_feed, c.borrow_collateral_factor,
               c.liquidate_collateral_factor, c.liquidation_factor, c.supply_cap,
               ifNull(s.total_collateral, '0') AS total_collateral
        FROM (
            SELECT comet, asset,
                   argMax(price_feed, updated_at) AS price_feed,
                   argMax(borrow_collateral_factor, updated_at) AS borrow_collateral_factor,
                   argMax(liquidate_collateral_factor, updated_at) AS liquidate_collateral_factor,
                   argMax(liquidation_factor, updated_at) AS liquidation_factor,
                   argMax(supply_cap, updated_at) AS supply_cap
            FROM compound_v3_collateral_registry FINAL
            WHERE active = 1
            GROUP BY comet, asset
        ) AS c
        LEFT JOIN (
            SELECT comet, asset, argMax(total_collateral, updated_at) AS total_collateral
            FROM compound_v3_collateral_state FINAL
            GROUP BY comet, asset
        ) AS s ON s.comet = c.comet AND s.asset = c.asset
        """
    ).result_rows
    output_rows = []
    for row in collateral_rows:
        comet, asset, price_feed, borrow_cf, liquidate_cf, liquidation_factor, supply_cap, total_collateral = row
        symbol, decimals = _token_meta(str(asset or ""))
        if symbol == "UNKNOWN" and asset:
            symbol = f"{str(asset)[:6]}...{str(asset)[-4:]}"
        supply_cap_tokens = _token_amount_from_raw(supply_cap, decimals)
        total_collateral_tokens = _token_amount_from_raw(total_collateral, decimals)
        price_usd = (
            extra_prices.get("COMP / USD", 50.0)
            if symbol == "COMP"
            else get_usd_price(symbol, eth_price, btc_price, extra_prices)
        )
        output_rows.append([
            str(comet or ""),
            str(asset or ""),
            symbol,
            str(price_feed or ""),
            "Compound Price Feed",
            int(decimals),
            float(price_usd or 0.0),
            float(borrow_cf or 0.0),
            float(liquidate_cf or 0.0),
            float(liquidation_factor or 0.0),
            str(supply_cap or "0"),
            float(supply_cap_tokens),
            str(total_collateral or "0"),
            float(total_collateral_tokens),
            float(total_collateral_tokens * float(price_usd or 0.0)),
            1 if float(borrow_cf or 0.0) > 0.0 else 0,
        ])
    if output_rows:
        insert_rows_batched(
            ch,
            COMPOUND_V3_COLLATERAL_LATEST_TABLE,
            output_rows,
            [
                "comet", "asset", "symbol", "price_feed", "oracle_name",
                "token_decimals", "price_usd", "borrow_collateral_factor",
                "liquidate_collateral_factor", "liquidation_factor", "supply_cap",
                "supply_cap_tokens", "total_collateral", "total_collateral_tokens",
                "collateral_usd", "borrow_enabled",
            ],
        )
        rows_written += len(output_rows)

    if min_ts is None or max_ts is None:
        window_filter = ""
    else:
        try:
            min_dt = pd.to_datetime(min_ts) - pd.Timedelta(days=2)
            max_dt = pd.to_datetime(max_ts) + pd.Timedelta(days=1)
            window_filter = (
                f"WHERE day >= toDateTime('{min_dt.strftime('%Y-%m-%d %H:%M:%S')}') "
                f"AND day <= toDateTime('{max_dt.strftime('%Y-%m-%d %H:%M:%S')}')"
            )
        except Exception:
            window_filter = ""
    ch.command(
        f"""
        INSERT INTO {COMPOUND_V3_FLOW_DAILY_TABLE} (
            day, entity_id, symbol, supply_inflow_usd, supply_outflow_usd,
            borrow_inflow_usd, borrow_outflow_usd, net_supply_flow_usd,
            net_borrow_flow_usd
        )
        SELECT
            day,
            entity_id,
            symbol,
            greatest(0.0, supply_delta) AS supply_inflow_usd,
            greatest(0.0, -supply_delta) AS supply_outflow_usd,
            greatest(0.0, borrow_delta) AS borrow_inflow_usd,
            greatest(0.0, -borrow_delta) AS borrow_outflow_usd,
            supply_delta AS net_supply_flow_usd,
            borrow_delta AS net_borrow_flow_usd
        FROM (
            SELECT
                day,
                entity_id,
                symbol,
                supply_usd - lagInFrame(supply_usd, 1, supply_usd) OVER (PARTITION BY entity_id ORDER BY day ASC) AS supply_delta,
                borrow_usd - lagInFrame(borrow_usd, 1, borrow_usd) OVER (PARTITION BY entity_id ORDER BY day ASC) AS borrow_delta
            FROM (
                SELECT
                    toStartOfDay(timestamp) AS day,
                    entity_id,
                    argMax(symbol, tuple(timestamp, inserted_at)) AS symbol,
                    argMax(toFloat64(supply_usd), tuple(timestamp, inserted_at)) AS supply_usd,
                    argMax(toFloat64(borrow_usd), tuple(timestamp, inserted_at)) AS borrow_usd
                FROM compound_v3_comet_metrics FINAL
                GROUP BY day, entity_id
            )
            {window_filter}
            ORDER BY entity_id ASC, day ASC
        )
        """
    )
    try:
        rows_written += int(ch.command(f"SELECT count() FROM {COMPOUND_V3_FLOW_DAILY_TABLE} FINAL") or 0)
    except Exception:
        pass
    return rows_written


def _per_block_rate_from_index(previous_index: int, next_index: int, previous_ts, next_ts) -> float:
    if previous_index <= 0 or next_index <= previous_index:
        return 0.0
    elapsed = max(1.0, (next_ts - previous_ts).total_seconds())
    return min(max((next_index / previous_index - 1.0) * (SECONDS_PER_YEAR / elapsed), 0.0), 10.0)


def _v3_rate(utilization: float, kink: float, slope_low: float, slope_high: float, base: float) -> float:
    util = max(0.0, min(float(utilization), 1.0))
    if kink <= 0 or util <= kink:
        return max(0.0, base + slope_low * util)
    return max(0.0, base + slope_low * kink + slope_high * (util - kink))


def ensure_compound_tables(ch) -> None:
    for table in ("compound_v2_events", "compound_v3_events"):
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
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v2_market_registry (
            ctoken String,
            symbol LowCardinality(String),
            underlying String,
            underlying_symbol LowCardinality(String),
            underlying_decimals UInt8,
            ctoken_decimals UInt8 DEFAULT 8,
            collateral_factor Float64 DEFAULT 0,
            source LowCardinality(String) DEFAULT 'seed',
            active UInt8 DEFAULT 1,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY ctoken
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v2_market_state (
            ctoken String,
            total_supply_ctokens String,
            total_borrows String,
            total_reserves String,
            cash String,
            borrow_index String,
            reserve_factor Float64 DEFAULT 0,
            exchange_rate String DEFAULT '0',
            supply_apy Float64 DEFAULT 0,
            borrow_apy Float64 DEFAULT 0,
            last_event_block UInt64,
            last_event_timestamp DateTime,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY ctoken
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v2_market_metrics (
            timestamp DateTime,
            ctoken String,
            entity_id String,
            symbol LowCardinality(String),
            underlying String,
            supply_usd Float64,
            borrow_usd Float64,
            cash_usd Float64,
            reserves_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            collateral_factor Float64 DEFAULT 0,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (ctoken, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v3_comet_registry (
            comet String,
            symbol LowCardinality(String),
            base_token String,
            base_symbol LowCardinality(String),
            base_decimals UInt8,
            supply_kink Float64 DEFAULT 0,
            supply_slope_low Float64 DEFAULT 0,
            supply_slope_high Float64 DEFAULT 0,
            supply_base Float64 DEFAULT 0,
            borrow_kink Float64 DEFAULT 0,
            borrow_slope_low Float64 DEFAULT 0,
            borrow_slope_high Float64 DEFAULT 0,
            borrow_base Float64 DEFAULT 0,
            source LowCardinality(String) DEFAULT 'seed',
            active UInt8 DEFAULT 1,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY comet
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v3_collateral_registry (
            comet String,
            asset String,
            price_feed String DEFAULT '',
            scale String DEFAULT '0',
            borrow_collateral_factor Float64 DEFAULT 0,
            liquidate_collateral_factor Float64 DEFAULT 0,
            liquidation_factor Float64 DEFAULT 0,
            supply_cap String DEFAULT '0',
            source LowCardinality(String) DEFAULT 'seed',
            active UInt8 DEFAULT 1,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (comet, asset)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v3_comet_state (
            comet String,
            total_supply_base String,
            total_borrow_base String,
            total_supply_principal String DEFAULT '0',
            total_borrow_principal String DEFAULT '0',
            base_supply_index String DEFAULT '1000000000000000',
            base_borrow_index String DEFAULT '1000000000000000',
            reserves_base String,
            last_event_block UInt64,
            last_event_timestamp DateTime,
            last_accrual_timestamp DateTime DEFAULT last_event_timestamp,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY comet
        """
    )
    for column, column_type in (
        ("total_supply_principal", "String DEFAULT '0'"),
        ("total_borrow_principal", "String DEFAULT '0'"),
        ("base_supply_index", "String DEFAULT '1000000000000000'"),
        ("base_borrow_index", "String DEFAULT '1000000000000000'"),
        ("last_accrual_timestamp", "DateTime DEFAULT last_event_timestamp"),
    ):
        ch.command(f"ALTER TABLE compound_v3_comet_state ADD COLUMN IF NOT EXISTS {column} {column_type}")
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v3_account_state (
            comet String,
            account String,
            base_balance String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (comet, account)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v3_collateral_state (
            comet String,
            asset String,
            total_collateral String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (comet, asset)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_v3_comet_metrics (
            timestamp DateTime,
            comet String,
            entity_id String,
            symbol LowCardinality(String),
            base_token String,
            supply_usd Float64,
            borrow_usd Float64,
            reserves_usd Float64,
            supply_apy Float64,
            borrow_apy Float64,
            utilization Float64,
            price_usd Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (comet, timestamp)
        TTL timestamp + INTERVAL 36 MONTH DELETE
        """
    )
    ensure_compound_v3_serving_tables(ch)
    for table in ("compound_v2_timeseries", "compound_v3_timeseries"):
        ch.command(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
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
        CREATE TABLE IF NOT EXISTS compound_rpc_anchor_runs (
            run_id String,
            protocol LowCardinality(String),
            block_number UInt64,
            status LowCardinality(String),
            checked_markets UInt64,
            drifted_markets UInt64,
            max_notional_drift Float64,
            max_apy_drift Float64,
            error String DEFAULT '',
            inserted_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (protocol, inserted_at, run_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS compound_rpc_anchor_diffs (
            run_id String,
            protocol LowCardinality(String),
            block_number UInt64,
            market_id String,
            field LowCardinality(String),
            indexed_value String,
            rpc_value String,
            drift Float64,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (protocol, run_id, market_id, field)
        """
    )


class CompoundV2Source(BaseSource):
    name = COMPOUND_V2_MARKET
    contracts: list[str] = []
    topics = list(V2_EVENT_MAP.keys())
    raw_table = "compound_v2_events"
    genesis_block = COMPOUND_V2_GENESIS_BLOCK

    def __init__(self):
        self._markets: dict[str, CompoundV2Market] = {}
        self._states: dict[str, CompoundV2State] = {}
        self._touched: set[str] = set()
        self._initialized = False

    def log_selection(self) -> hypersync.LogSelection:
        if self._markets:
            return hypersync.LogSelection(address=sorted(self._markets), topics=[self.topics])
        return hypersync.LogSelection(address=sorted(STATIC_V2_MARKETS), topics=[self.topics])

    def route(self, log_entry) -> bool:
        address = _clean_address(getattr(log_entry, "address", ""))
        return address in self._markets or address in STATIC_V2_MARKETS

    def get_cursor(self, ch) -> int:
        ensure_compound_tables(ch)
        self._load_registry(ch)
        self._load_state(ch)
        self._initialized = True
        result = ch.command("SELECT max(block_number) FROM compound_v2_events")
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        return V2_EVENT_MAP.get(topics[0], "") if topics else ""

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        if not topics:
            return None
        event_name = V2_EVENT_MAP.get(topics[0])
        if not event_name:
            return None
        return {
            "event_name": event_name,
            "ctoken": _clean_address(log_entry.address),
            "block_number": int(log_entry.block_number),
            "timestamp": _block_ts(log_entry.block_number, block_ts_map),
            "tx_hash": str(getattr(log_entry, "transaction_hash", "") or ""),
            "log_index": int(getattr(log_entry, "log_index", 0) or 0),
            "words": _words(log_entry.data),
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0
        self._load_registry(ch)
        self._load_state(ch)
        metrics: list[dict] = []
        for row in sorted(decoded_rows, key=lambda item: (item["block_number"], item["log_index"])):
            metric = self._apply_event(row, ch)
            if metric:
                metrics.append(metric)
        self._persist_state(ch)
        if not metrics:
            return 0
        metrics_df = pd.DataFrame(metrics)
        insert_df_batched(ch, "compound_v2_market_metrics", metrics_df)
        final = pd.DataFrame(
            {
                "timestamp": metrics_df["timestamp"],
                "protocol": COMPOUND_V2_MARKET,
                "symbol": metrics_df["symbol"],
                "entity_id": metrics_df["entity_id"],
                "target_id": metrics_df["underlying"],
                "supply_usd": metrics_df["supply_usd"],
                "borrow_usd": metrics_df["borrow_usd"],
                "supply_apy": metrics_df["supply_apy"],
                "borrow_apy": metrics_df["borrow_apy"],
                "utilization": metrics_df["utilization"],
                "price_usd": metrics_df["price_usd"],
            }
        )
        final = final[(final["supply_usd"] > 0) | (final["borrow_usd"] > 0)]
        if final.empty:
            return 0
        final = forward_fill_hourly(final, ch, COMPOUND_V2_MARKET, compound=False)
        min_ts_dt = pd.to_datetime(final["timestamp"].min())
        max_ts_dt = pd.to_datetime(final["timestamp"].max())
        min_ts = min_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        max_ts = max_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        rewrite_protocol_window_if_enabled(ch, self.output_table, COMPOUND_V2_MARKET, min_ts, max_ts)
        insert_df_batched(ch, self.output_table, final)
        upsert_market_timeseries(ch, final)
        upsert_api_market_latest(ch, latest_snapshot)
        refresh_compound_v3_serving_tables(ch, min_ts_dt, max_ts_dt)
        refresh_api_protocol_tvl_weekly(ch, min_ts_dt, max_ts_dt)
        return len(final)

    def _load_registry(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT ctoken, argMax(symbol, updated_at), argMax(underlying, updated_at),
                       argMax(underlying_symbol, updated_at), argMax(underlying_decimals, updated_at),
                       argMax(ctoken_decimals, updated_at), argMax(collateral_factor, updated_at)
                FROM compound_v2_market_registry
                WHERE active = 1
                GROUP BY ctoken
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            ctoken = _clean_address(row[0])
            self._markets[ctoken] = CompoundV2Market(
                ctoken=ctoken,
                symbol=str(row[1] or ""),
                underlying=_clean_address(row[2]),
                underlying_symbol=str(row[3] or ""),
                underlying_decimals=int(row[4] or 18),
                ctoken_decimals=int(row[5] or 8),
                collateral_factor=float(row[6] or 0.0),
            )
        if not self._markets:
            for ctoken, (symbol, underlying, underlying_symbol, underlying_decimals, ctoken_decimals) in STATIC_V2_MARKETS.items():
                self._markets[ctoken] = CompoundV2Market(ctoken, symbol, underlying, underlying_symbol, underlying_decimals, ctoken_decimals)

    def _load_state(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT ctoken, total_supply_ctokens, total_borrows, total_reserves, cash, borrow_index,
                       reserve_factor, exchange_rate, supply_apy, borrow_apy, last_event_block, last_event_timestamp
                FROM compound_v2_market_state FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            ctoken = _clean_address(row[0])
            self._states[ctoken] = CompoundV2State(
                total_supply_ctokens=int(row[1] or 0),
                total_borrows=int(row[2] or 0),
                total_reserves=int(row[3] or 0),
                cash=int(row[4] or 0),
                borrow_index=int(row[5] or WAD),
                reserve_factor=float(row[6] or 0.0),
                exchange_rate=int(row[7] or 0),
                supply_apy=float(row[8] or 0.0),
                borrow_apy=float(row[9] or 0.0),
                last_event_block=int(row[10] or 0),
                last_event_timestamp=row[11] or datetime.datetime(1970, 1, 1),
            )

    def _apply_event(self, row: dict, ch=None) -> Optional[dict]:
        ctoken = _clean_address(row["ctoken"])
        market = self._markets.get(ctoken)
        if not market:
            return None
        state = self._states.setdefault(ctoken, CompoundV2State())
        words = row.get("words") or []
        event_name = row["event_name"]
        ts = row["timestamp"]
        if event_name == "Mint" and len(words) >= 3:
            state.cash += int(words[1])
            state.total_supply_ctokens += int(words[2])
        elif event_name == "Redeem" and len(words) >= 3:
            state.cash = max(0, state.cash - int(words[1]))
            state.total_supply_ctokens = max(0, state.total_supply_ctokens - int(words[2]))
        elif event_name == "Borrow" and len(words) >= 4:
            state.cash = max(0, state.cash - int(words[1]))
            state.total_borrows = int(words[3])
        elif event_name == "RepayBorrow" and len(words) >= 4:
            state.cash += int(words[2])
            state.total_borrows = int(words[4]) if len(words) >= 5 else int(words[3])
        elif event_name == "LiquidateBorrow" and len(words) >= 5:
            state.cash += int(words[2])
            state.total_borrows = max(0, state.total_borrows - int(words[2]))
        elif event_name == "AccrueInterest" and len(words) >= 4:
            previous_index = state.borrow_index
            previous_ts = state.last_event_timestamp
            state.cash = int(words[0])
            interest = int(words[1])
            state.borrow_index = int(words[2]) or state.borrow_index
            state.total_borrows = int(words[3])
            state.total_reserves += int(interest * state.reserve_factor)
            observed_borrow = _per_block_rate_from_index(previous_index, state.borrow_index, previous_ts, ts)
            if observed_borrow > 0:
                state.borrow_apy = observed_borrow
        elif event_name == "NewReserveFactor" and len(words) >= 2:
            state.reserve_factor = float(words[1]) / WAD
        elif event_name == "ReservesAdded" and len(words) >= 3:
            state.cash += int(words[1])
            state.total_reserves = int(words[2])
        elif event_name == "ReservesReduced" and len(words) >= 3:
            state.cash = max(0, state.cash - int(words[1]))
            state.total_reserves = int(words[2])
        else:
            return None
        state.last_event_block = int(row["block_number"])
        state.last_event_timestamp = ts
        self._touched.add(ctoken)
        return self._metric(market, state, ts, ch)

    def _metric(self, market: CompoundV2Market, state: CompoundV2State, ts: datetime.datetime, ch=None) -> dict:
        eth_price, btc_price = get_chainlink_prices(ch)
        price = get_usd_price(market.underlying_symbol, eth_price, btc_price)
        supply_raw = max(0, state.cash + state.total_borrows - state.total_reserves)
        borrow_raw = max(0, state.total_borrows)
        scale = float(10 ** market.underlying_decimals)
        supply_usd = supply_raw / scale * price
        borrow_usd = borrow_raw / scale * price
        utilization = min(1.0, _safe_ratio(borrow_raw, supply_raw)) if supply_raw > 0 else 0.0
        state.supply_apy = max(0.0, state.borrow_apy * utilization * (1.0 - state.reserve_factor))
        if state.total_supply_ctokens > 0:
            state.exchange_rate = int((supply_raw * (10 ** market.ctoken_decimals)) / state.total_supply_ctokens)
        return {
            "timestamp": ts,
            "ctoken": market.ctoken,
            "entity_id": market.ctoken,
            "symbol": market.underlying_symbol,
            "underlying": market.underlying,
            "supply_usd": float(supply_usd),
            "borrow_usd": float(borrow_usd),
            "cash_usd": float(state.cash / scale * price),
            "reserves_usd": float(state.total_reserves / scale * price),
            "supply_apy": float(state.supply_apy),
            "borrow_apy": float(state.borrow_apy),
            "utilization": float(utilization),
            "price_usd": float(price),
            "collateral_factor": float(market.collateral_factor),
        }

    def _persist_state(self, ch) -> None:
        rows = []
        for ctoken in sorted(self._touched):
            state = self._states.get(ctoken)
            if not state:
                continue
            rows.append([
                ctoken,
                str(state.total_supply_ctokens),
                str(state.total_borrows),
                str(state.total_reserves),
                str(state.cash),
                str(state.borrow_index),
                float(state.reserve_factor),
                str(state.exchange_rate),
                float(state.supply_apy),
                float(state.borrow_apy),
                int(state.last_event_block),
                state.last_event_timestamp,
            ])
        if rows:
            insert_rows_batched(
                ch,
                "compound_v2_market_state",
                rows,
                [
                    "ctoken",
                    "total_supply_ctokens",
                    "total_borrows",
                    "total_reserves",
                    "cash",
                    "borrow_index",
                    "reserve_factor",
                    "exchange_rate",
                    "supply_apy",
                    "borrow_apy",
                    "last_event_block",
                    "last_event_timestamp",
                ],
            )
        self._touched.clear()


class CompoundV3Source(BaseSource):
    name = COMPOUND_V3_MARKET
    contracts: list[str] = []
    topics = list(V3_EVENT_MAP.keys())
    raw_table = "compound_v3_events"
    genesis_block = COMPOUND_V3_GENESIS_BLOCK

    def __init__(self):
        self._comets: dict[str, CompoundV3Comet] = {}
        self._pending_comets: dict[str, CompoundV3Comet] = {}
        self._states: dict[str, CompoundV3State] = {}
        self._accounts: dict[tuple[str, str], int] = {}
        self._collateral: dict[tuple[str, str], int] = {}
        self._touched_comets: set[str] = set()
        self._touched_accounts: set[tuple[str, str]] = set()
        self._touched_collateral: set[tuple[str, str]] = set()
        self._price_cache: Optional[tuple[float, float]] = None

    def log_selection(self) -> hypersync.LogSelection:
        addresses = sorted(set(self._comets) | set(STATIC_V3_COMETS) | {COMPOUND_V3_CONFIGURATOR})
        if self._comets:
            return hypersync.LogSelection(address=addresses, topics=[self.topics])
        return hypersync.LogSelection(address=addresses, topics=[self.topics])

    def route(self, log_entry) -> bool:
        address = _clean_address(getattr(log_entry, "address", ""))
        return address == COMPOUND_V3_CONFIGURATOR or address in self._comets or address in STATIC_V3_COMETS

    def get_cursor(self, ch) -> int:
        ensure_compound_tables(ch)
        self._load_registry(ch)
        self._load_state(ch)
        self._load_accounts(ch)
        self._load_collateral(ch)
        result = ch.command("SELECT max(block_number) FROM compound_v3_events")
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        return V3_EVENT_MAP.get(topics[0], "") if topics else ""

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        if not topics:
            return None
        event_name = V3_EVENT_MAP.get(topics[0])
        if not event_name:
            return None
        return {
            "event_name": event_name,
            "comet": _clean_address(log_entry.address),
            "block_number": int(log_entry.block_number),
            "timestamp": _block_ts(log_entry.block_number, block_ts_map),
            "tx_hash": str(getattr(log_entry, "transaction_hash", "") or ""),
            "log_index": int(getattr(log_entry, "log_index", 0) or 0),
            "topics": topics,
            "words": _words(log_entry.data),
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0
        self._load_registry(ch)
        self._load_state(ch)
        self._load_accounts(ch)
        self._load_collateral(ch)
        metrics: list[dict] = []
        current_tx = None
        tx_rows: list[dict] = []
        self._price_cache = get_chainlink_prices(ch) if ch is not None else None
        try:
            ordered_rows = sorted(
                decoded_rows,
                key=lambda item: (item["block_number"], item["tx_hash"], item["log_index"]),
            )
            for row in ordered_rows:
                tx_key = (row["block_number"], row["tx_hash"], row["comet"])
                if current_tx is not None and tx_key != current_tx:
                    metric = self._apply_tx(tx_rows, ch)
                    if metric:
                        metrics.append(metric)
                    tx_rows = []
                current_tx = tx_key
                tx_rows.append(row)
            if tx_rows:
                metric = self._apply_tx(tx_rows, ch)
                if metric:
                    metrics.append(metric)
        finally:
            self._price_cache = None
        self._persist_state(ch)
        if not metrics:
            return 0
        metrics_df = pd.DataFrame(metrics)
        insert_df_batched(ch, "compound_v3_comet_metrics", metrics_df)
        final = pd.DataFrame(
            {
                "timestamp": metrics_df["timestamp"],
                "protocol": COMPOUND_V3_MARKET,
                "symbol": metrics_df["symbol"],
                "entity_id": metrics_df["entity_id"],
                "target_id": metrics_df["base_token"],
                "supply_usd": metrics_df["supply_usd"],
                "borrow_usd": metrics_df["borrow_usd"],
                "supply_apy": metrics_df["supply_apy"],
                "borrow_apy": metrics_df["borrow_apy"],
                "utilization": metrics_df["utilization"],
                "price_usd": metrics_df["price_usd"],
            }
        )
        final = final[(final["supply_usd"] > 0) | (final["borrow_usd"] > 0)]
        if final.empty:
            return 0
        latest_snapshot = final.copy()
        final = forward_fill_hourly(final, ch, COMPOUND_V3_MARKET, compound=False)
        min_ts_dt = pd.to_datetime(final["timestamp"].min())
        max_ts_dt = pd.to_datetime(final["timestamp"].max())
        min_ts = min_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        max_ts = max_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        rewrite_protocol_window_if_enabled(ch, self.output_table, COMPOUND_V3_MARKET, min_ts, max_ts)
        insert_df_batched(ch, self.output_table, final)
        upsert_market_timeseries(ch, final)
        upsert_api_market_latest(ch, latest_snapshot)
        refresh_api_protocol_tvl_weekly(ch, min_ts_dt, max_ts_dt)
        return len(final)

    def _load_registry(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT comet, argMax(symbol, updated_at), argMax(base_token, updated_at),
                       argMax(base_symbol, updated_at), argMax(base_decimals, updated_at),
                       argMax(supply_kink, updated_at), argMax(supply_slope_low, updated_at),
                       argMax(supply_slope_high, updated_at), argMax(supply_base, updated_at),
                       argMax(borrow_kink, updated_at), argMax(borrow_slope_low, updated_at),
                       argMax(borrow_slope_high, updated_at), argMax(borrow_base, updated_at)
                FROM compound_v3_comet_registry
                WHERE active = 1
                GROUP BY comet
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            comet = _clean_address(row[0])
            self._comets[comet] = CompoundV3Comet(
                comet=comet,
                symbol=str(row[1] or ""),
                base_token=_clean_address(row[2]),
                base_symbol=str(row[3] or ""),
                base_decimals=int(row[4] or 18),
                supply_kink=float(row[5] or 0.0),
                supply_slope_low=float(row[6] or 0.0),
                supply_slope_high=float(row[7] or 0.0),
                supply_base=float(row[8] or 0.0),
                borrow_kink=float(row[9] or 0.0),
                borrow_slope_low=float(row[10] or 0.0),
                borrow_slope_high=float(row[11] or 0.0),
                borrow_base=float(row[12] or 0.0),
            )
            self._pending_comets[comet] = CompoundV3Comet(**self._comets[comet].__dict__)
        for comet, values in STATIC_V3_COMETS.items():
            if comet in self._comets:
                continue
            self._comets[comet] = CompoundV3Comet(comet, *values)
            self._pending_comets[comet] = CompoundV3Comet(**self._comets[comet].__dict__)

    def _load_state(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT comet, total_supply_base, total_borrow_base,
                       total_supply_principal, total_borrow_principal,
                       base_supply_index, base_borrow_index, reserves_base,
                       last_event_block, last_event_timestamp, last_accrual_timestamp
                FROM compound_v3_comet_state FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            comet = _clean_address(row[0])
            self._states[comet] = CompoundV3State(
                total_supply_base=int(row[1] or 0),
                total_borrow_base=int(row[2] or 0),
                total_supply_principal=int(row[3] or row[1] or 0),
                total_borrow_principal=int(row[4] or row[2] or 0),
                base_supply_index=int(row[5] or BASE_INDEX_SCALE),
                base_borrow_index=int(row[6] or BASE_INDEX_SCALE),
                reserves_base=int(row[7] or 0),
                last_event_block=int(row[8] or 0),
                last_event_timestamp=row[9] or datetime.datetime(1970, 1, 1),
                last_accrual_timestamp=row[10] or row[9] or datetime.datetime(1970, 1, 1),
            )

    def _load_accounts(self, ch) -> None:
        try:
            rows = ch.query("SELECT comet, account, base_balance FROM compound_v3_account_state FINAL WHERE base_balance != '0'").result_rows
        except Exception:
            rows = []
        for comet, account, balance in rows:
            self._accounts[(_clean_address(comet), _clean_address(account))] = int(balance or 0)

    def _load_collateral(self, ch) -> None:
        try:
            rows = ch.query("SELECT comet, asset, total_collateral FROM compound_v3_collateral_state FINAL WHERE total_collateral != '0'").result_rows
        except Exception:
            rows = []
        for comet, asset, total in rows:
            self._collateral[(_clean_address(comet), _clean_address(asset))] = int(total or 0)

    def _apply_tx(self, rows: list[dict], ch=None) -> Optional[dict]:
        if not rows:
            return None
        if _clean_address(rows[0]["comet"]) == COMPOUND_V3_CONFIGURATOR:
            self._apply_config_tx(rows)
            return None
        comet_addr = _clean_address(rows[0]["comet"])
        comet = self._comets.get(comet_addr)
        if not comet:
            return None
        state = self._states.setdefault(comet_addr, CompoundV3State())
        ts = rows[-1]["timestamp"]
        self._accrue_v3(comet, state, ts)

        buy_collateral_withdraws: dict[str, int] = {}
        for row in rows:
            if row["event_name"] != "BuyCollateral":
                continue
            topics = row["topics"]
            words = row["words"]
            asset = _topic_address(topics, 2)
            collateral_amount = int(words[1]) if len(words) > 1 else 0
            if asset and collateral_amount > 0:
                buy_collateral_withdraws[asset] = buy_collateral_withdraws.get(asset, 0) + collateral_amount

        supply = withdraw = absorb = mint = burn = 0
        changed = False
        for row in rows:
            event_name = row["event_name"]
            topics = row["topics"]
            words = row["words"]
            amount = int(words[0]) if words else 0
            if event_name == "Supply":
                supply += amount
                changed = True
            elif event_name == "Withdraw":
                withdraw += amount
                changed = True
            elif event_name == "AbsorbDebt":
                absorb += amount
                changed = True
            elif event_name == "Transfer":
                src = _topic_address(topics, 1)
                dst = _topic_address(topics, 2)
                if src and int(src, 16) == 0:
                    mint += amount
                    changed = True
                elif dst and int(dst, 16) == 0:
                    burn += amount
                    changed = True
            elif event_name == "WithdrawReserves" and len(words) >= 1:
                state.reserves_base = max(0, state.reserves_base - amount)
                changed = True
            elif event_name == "WithdrawCollateral":
                asset = _topic_address(topics, 3)
                if asset and buy_collateral_withdraws.get(asset, 0) >= amount > 0:
                    buy_collateral_withdraws[asset] -= amount
                    continue
                changed = self._apply_collateral_event(comet_addr, event_name, topics, words) or changed
            elif event_name in {"SupplyCollateral", "TransferCollateral", "AbsorbCollateral", "BuyCollateral"}:
                changed = self._apply_collateral_event(comet_addr, event_name, topics, words) or changed

        if mint or burn or supply or withdraw or absorb:
            state.total_supply_principal = max(
                0,
                state.total_supply_principal
                + self._principal_supply(mint, state)
                - self._principal_supply(burn, state),
            )
            transfer_only_net = mint - burn if not (supply or withdraw or absorb) else 0
            borrow_present = max(withdraw - burn, 0) + max(transfer_only_net, 0)
            repay_present = max(supply + absorb - mint, 0) + max(-transfer_only_net, 0)
            state.total_borrow_principal = max(
                0,
                state.total_borrow_principal
                + self._principal_borrow(borrow_present, state)
                - self._principal_borrow(repay_present, state),
            )
            self._refresh_v3_present(state)

        if not changed:
            return None
        state.last_event_block = max(int(row["block_number"]) for row in rows)
        state.last_event_timestamp = ts
        self._touched_comets.add(comet_addr)
        return self._metric(comet, state, ts, ch)

    def _apply_config_tx(self, rows: list[dict]) -> None:
        for row in rows:
            event_name = row["event_name"]
            topics = row["topics"]
            words = row["words"]
            if event_name == "CometDeployed":
                comet_addr = _topic_address(topics, 1)
                pending = self._pending_comets.get(comet_addr)
                if pending:
                    self._comets[comet_addr] = CompoundV3Comet(**pending.__dict__)
                    self._touched_comets.add(comet_addr)
                continue
            field = V3_CONFIG_FIELD_MAP.get(event_name)
            comet_addr = _topic_address(topics, 1)
            if not field or not comet_addr or len(words) < 2:
                continue
            comet = self._pending_comets.get(comet_addr) or self._comets.get(comet_addr)
            if not comet:
                continue
            pending = CompoundV3Comet(**comet.__dict__)
            value = float(words[1]) / WAD
            setattr(pending, field, value)
            self._pending_comets[comet_addr] = pending

    def _accrue_v3(self, comet: CompoundV3Comet, state: CompoundV3State, ts: datetime.datetime) -> None:
        if state.last_accrual_timestamp.year <= 1970:
            state.last_accrual_timestamp = ts
            self._refresh_v3_present(state)
            return
        elapsed = max(0.0, (ts - state.last_accrual_timestamp).total_seconds())
        if elapsed <= 0:
            return
        self._refresh_v3_present(state)
        utilization = state.total_borrow_base / state.total_supply_base if state.total_supply_base > 0 else 0.0
        supply_rate = _v3_rate(utilization, comet.supply_kink, comet.supply_slope_low, comet.supply_slope_high, comet.supply_base)
        borrow_rate = _v3_rate(utilization, comet.borrow_kink, comet.borrow_slope_low, comet.borrow_slope_high, comet.borrow_base)
        state.base_supply_index += int(state.base_supply_index * supply_rate * elapsed / SECONDS_PER_YEAR)
        state.base_borrow_index += int(state.base_borrow_index * borrow_rate * elapsed / SECONDS_PER_YEAR)
        state.last_accrual_timestamp = ts
        self._refresh_v3_present(state)

    def _refresh_v3_present(self, state: CompoundV3State) -> None:
        state.total_supply_base = self._present_supply(state.total_supply_principal, state)
        state.total_borrow_base = self._present_borrow(state.total_borrow_principal, state)

    def _present_supply(self, principal: int, state: CompoundV3State) -> int:
        return int(principal) * int(state.base_supply_index) // BASE_INDEX_SCALE

    def _present_borrow(self, principal: int, state: CompoundV3State) -> int:
        return int(principal) * int(state.base_borrow_index) // BASE_INDEX_SCALE

    def _principal_supply(self, present: int, state: CompoundV3State) -> int:
        if present <= 0:
            return 0
        return int(present) * BASE_INDEX_SCALE // max(1, int(state.base_supply_index))

    def _principal_borrow(self, present: int, state: CompoundV3State) -> int:
        if present <= 0:
            return 0
        index = max(1, int(state.base_borrow_index))
        return (int(present) * BASE_INDEX_SCALE + index - 1) // index

    def _apply_collateral_event(self, comet: str, event_name: str, topics: list[str], words: list[int]) -> bool:
        if event_name in {"TransferCollateral", "BuyCollateral"}:
            return False
        asset = _topic_address(topics, 3)
        amount = int(words[0]) if words else 0
        sign = 1 if event_name == "SupplyCollateral" else -1
        if not asset:
            return False
        key = (comet, asset)
        self._collateral[key] = max(0, self._collateral.get(key, 0) + sign * amount)
        self._touched_collateral.add(key)
        return True

    def _metric(self, comet: CompoundV3Comet, state: CompoundV3State, ts: datetime.datetime, ch=None) -> dict:
        eth_price, btc_price = self._price_cache or get_chainlink_prices(ch)
        price = get_usd_price(comet.base_symbol, eth_price, btc_price)
        scale = float(10 ** comet.base_decimals)
        supply = max(0, state.total_supply_base)
        borrow = max(0, state.total_borrow_base)
        utilization = min(1.0, _safe_ratio(borrow, supply)) if supply > 0 else 0.0
        supply_apy = _v3_rate(utilization, comet.supply_kink, comet.supply_slope_low, comet.supply_slope_high, comet.supply_base)
        borrow_apy = _v3_rate(utilization, comet.borrow_kink, comet.borrow_slope_low, comet.borrow_slope_high, comet.borrow_base)
        return {
            "timestamp": ts,
            "comet": comet.comet,
            "entity_id": comet.comet,
            "symbol": comet.base_symbol,
            "base_token": comet.base_token,
            "supply_usd": float(supply / scale * price),
            "borrow_usd": float(borrow / scale * price),
            "reserves_usd": float(max(0, state.reserves_base) / scale * price),
            "supply_apy": float(supply_apy),
            "borrow_apy": float(borrow_apy),
            "utilization": float(utilization),
            "price_usd": float(price),
        }

    def _persist_state(self, ch) -> None:
        state_rows = []
        for comet in sorted(self._touched_comets):
            state = self._states.get(comet)
            if not state:
                continue
            self._refresh_v3_present(state)
            state_rows.append([
                comet,
                str(state.total_supply_base),
                str(state.total_borrow_base),
                str(state.total_supply_principal),
                str(state.total_borrow_principal),
                str(state.base_supply_index),
                str(state.base_borrow_index),
                str(state.reserves_base),
                int(state.last_event_block),
                state.last_event_timestamp,
                state.last_accrual_timestamp,
            ])
        if state_rows:
            insert_rows_batched(
                ch,
                "compound_v3_comet_state",
                state_rows,
                [
                    "comet",
                    "total_supply_base",
                    "total_borrow_base",
                    "total_supply_principal",
                    "total_borrow_principal",
                    "base_supply_index",
                    "base_borrow_index",
                    "reserves_base",
                    "last_event_block",
                    "last_event_timestamp",
                    "last_accrual_timestamp",
                ],
            )
        registry_rows = []
        for comet_addr in sorted(self._touched_comets):
            comet = self._comets.get(comet_addr)
            if not comet:
                continue
            registry_rows.append([
                comet.comet,
                comet.symbol,
                comet.base_token,
                comet.base_symbol,
                int(comet.base_decimals),
                float(comet.supply_kink),
                float(comet.supply_slope_low),
                float(comet.supply_slope_high),
                float(comet.supply_base),
                float(comet.borrow_kink),
                float(comet.borrow_slope_low),
                float(comet.borrow_slope_high),
                float(comet.borrow_base),
                "hypersync_config",
                1,
            ])
        if registry_rows:
            insert_rows_batched(
                ch,
                "compound_v3_comet_registry",
                registry_rows,
                [
                    "comet",
                    "symbol",
                    "base_token",
                    "base_symbol",
                    "base_decimals",
                    "supply_kink",
                    "supply_slope_low",
                    "supply_slope_high",
                    "supply_base",
                    "borrow_kink",
                    "borrow_slope_low",
                    "borrow_slope_high",
                    "borrow_base",
                    "source",
                    "active",
                ],
            )
        account_rows = [[comet, account, str(self._accounts.get((comet, account), 0))] for comet, account in sorted(self._touched_accounts)]
        if account_rows:
            insert_rows_batched(ch, "compound_v3_account_state", account_rows, ["comet", "account", "base_balance"])
        collateral_rows = [[comet, asset, str(self._collateral.get((comet, asset), 0))] for comet, asset in sorted(self._touched_collateral)]
        if collateral_rows:
            insert_rows_batched(ch, "compound_v3_collateral_state", collateral_rows, ["comet", "asset", "total_collateral"])
        self._touched_comets.clear()
        self._touched_accounts.clear()
        self._touched_collateral.clear()
