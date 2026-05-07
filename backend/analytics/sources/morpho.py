"""Morpho Blue market source for Chainlink-priced Ethereum markets.

This source rebuilds Morpho market state from raw singleton logs instead of
reading the legacy morpho_timeseries table. Markets whose loan and collateral
assets cannot be priced from Chainlink worker feeds are kept in metadata/status
tables but are not written to serving USD rows.
"""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass
from typing import Optional

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
from ..morpho_oracle_snapshots import (
    ZERO_ADDRESS,
    collateral_value_usd_from_oracle,
    ensure_morpho_oracle_snapshot_tables,
)
from ..protocols import MORPHO_MARKET
from ..tokens import BTC_ASSETS, ETH_ASSETS, STABLES, TOKENS

log = logging.getLogger("indexer.morpho")

MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
MORPHO_BLUE_GENESIS_BLOCK = 18_883_124
WAD = 10**18
SECONDS_PER_YEAR = 31_536_000

TOPIC_ACCRUE_INTEREST = "0x9d9bd501d0657d7dfe415f779a620a62b78bc508ddc0891fbbd8b7ac0f8fce87"
TOPIC_BORROW = "0x570954540bed6b1304a87dfe815a5eda4a648f7097a16240dcd85c9b5fd42a43"
TOPIC_CREATE_MARKET = "0xac4b2400f169220b0c0afdde7a0b32e775ba727ea1cb30b35f935cdaab8683ac"
TOPIC_LIQUIDATE = "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751452d2fe0e25010783bcab57a67e41"
TOPIC_REPAY = "0x52acb05cebbd3cd39715469f22afbf5a17496295ef3bc9bb5944056c63ccaa09"
TOPIC_SET_FEE = "0x139d6f58e9a127229667c8e3b36e88890a66cfc8ab1024ddc513e189e125b75b"
TOPIC_SUPPLY = "0xedf8870433c83823eb071d3df1caa8d008f12f6440918c20d75a3602cda30fe0"
TOPIC_SUPPLY_COLLATERAL = "0xa3b9472a1399e17e123f3c2e6586c23e504184d504de59cdaa2b375e880c6184"
TOPIC_WITHDRAW = "0xa56fc0ad5702ec05ce63666221f796fb62437c32db1aa1aa075fc6484cf58fbf"
TOPIC_WITHDRAW_COLLATERAL = "0xe80ebd7cc9223d7382aab2e0d1d6155c65651f83d53c8b9b06901d167e321142"

# Explicitly not SetFee; retained for regression tests against the legacy bug.
TOPIC_SET_AUTHORIZATION = "0xd5e969f01efe921d3f766bdebad25f0a05e3f237311f56482bf132d0326309c0"

EVENT_MAP = {
    TOPIC_ACCRUE_INTEREST: "AccrueInterest",
    TOPIC_BORROW: "Borrow",
    TOPIC_CREATE_MARKET: "CreateMarket",
    TOPIC_LIQUIDATE: "Liquidate",
    TOPIC_REPAY: "Repay",
    TOPIC_SET_FEE: "SetFee",
    TOPIC_SUPPLY: "Supply",
    TOPIC_SUPPLY_COLLATERAL: "SupplyCollateral",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_WITHDRAW_COLLATERAL: "WithdrawCollateral",
}

PRICE_FEED_ALIASES = {
    "ETH": ("ETH / USD",),
    "WETH": ("ETH / USD",),
    "BTC": ("BTC / USD",),
    "WBTC": ("WBTC / BTC", "BTC / USD"),
    "cbBTC": ("cbBTC / USD",),
    "CBBTC": ("cbBTC / USD",),
    "LBTC": ("LBTC / BTC", "BTC / USD"),
    "tBTC": ("TBTC / USD",),
    "TBTC": ("TBTC / USD",),
    "stETH": ("STETH / USD",),
    "STETH": ("STETH / USD",),
    "wstETH": ("wstETH/stETH exchange rate", "STETH / USD"),
    "WSTETH": ("wstETH/stETH exchange rate", "STETH / USD"),
    "weETH": ("weETH / ETH", "ETH / USD"),
    "WEETH": ("weETH / ETH", "ETH / USD"),
    "rETH": ("RETH / ETH", "ETH / USD"),
    "RETH": ("RETH / ETH", "ETH / USD"),
    "cbETH": ("CBETH / ETH", "ETH / USD"),
    "CBETH": ("CBETH / ETH", "ETH / USD"),
    "lsETH": ("LsETH / ETH Exchange Rate", "ETH / USD"),
    "LSETH": ("LsETH / ETH Exchange Rate", "ETH / USD"),
    "XAUt": ("XAU / USD",),
    "USD0pp": ("USD0++ / USD",),
    "crvUSD": ("CRVUSD / USD",),
    "CRVUSD": ("CRVUSD / USD",),
    "frxUSD": ("frxUSD / USD",),
    "FRXUSD": ("frxUSD / USD",),
}

DIRECT_USD_FEED_SYMBOLS = {
    "AAVE",
    "DAI",
    "EIGEN",
    "LINK",
    "MKR",
    "PAXG",
    "PYUSD",
    "RLUSD",
    "SKY",
    "STETH",
    "UNI",
    "USDC",
    "USDe",
    "USDS",
    "USDT",
    "USD0",
}


@dataclass
class MorphoMarketParams:
    market_id: str
    loan_token: str = ""
    collateral_token: str = ""
    loan_symbol: str = "UNKNOWN"
    collateral_symbol: str = "UNKNOWN"
    loan_decimals: int = 18
    collateral_decimals: int = 18
    oracle: str = ""
    irm: str = ""
    lltv: int = 0
    creation_block: int = 0
    creation_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


@dataclass
class MorphoMarketState:
    total_supply_assets: int = 0
    total_supply_shares: int = 0
    total_borrow_assets: int = 0
    total_borrow_shares: int = 0
    collateral_assets: int = 0
    fee_wad: int = 0
    last_borrow_rate_wad: int = 0
    last_event_block: int = 0
    last_event_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


@dataclass
class MorphoUserPosition:
    supply_shares: int = 0
    borrow_shares: int = 0
    collateral_assets: int = 0
    last_event_block: int = 0
    last_event_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


def _word_address(word: str) -> str:
    return "0x" + word[-40:].lower()


def _words(data: str) -> list[str]:
    raw = (data or "").removeprefix("0x")
    if len(raw) % 64 != 0:
        raw = raw[: len(raw) - (len(raw) % 64)]
    return [raw[i : i + 64] for i in range(0, len(raw), 64) if len(raw[i : i + 64]) == 64]



def _topic_address(topics: list[str], idx: int) -> str:
    if idx >= len(topics) or not topics[idx]:
        return ""
    return _word_address(str(topics[idx]).removeprefix("0x").rjust(64, "0"))


def _uint(words: list[str], idx: int) -> int:
    if idx >= len(words):
        return 0
    return int(words[idx], 16)


def _token_meta(address: str) -> tuple[str, int]:
    symbol, decimals = TOKENS.get(address.removeprefix("0x").lower(), (address[:10], 18))
    return symbol, int(decimals)


def _clip_nonnegative(value: int) -> int:
    return max(0, int(value))


def price_feed_requirements(symbol: str, available_feeds: set[str] | None = None) -> tuple[str, ...]:
    """Return the exact feeds the runtime resolver will use for a symbol."""
    available_feeds = available_feeds or set()
    direct = f"{symbol} / USD"
    if direct in available_feeds:
        return (direct,)

    alias = PRICE_FEED_ALIASES.get(symbol)
    if alias:
        return alias

    if symbol in DIRECT_USD_FEED_SYMBOLS or symbol in STABLES:
        return (direct,)

    eth_pair = f"{symbol} / ETH"
    btc_pair = f"{symbol} / BTC"
    if eth_pair in available_feeds:
        return (eth_pair, "ETH / USD")
    if btc_pair in available_feeds:
        return (btc_pair, "BTC / USD")
    return ()


def _ratio(value: float | int | None) -> float | None:
    if value is None:
        return None
    ratio = float(value)
    if ratio > 1_000_000:
        ratio /= 1e18
    return ratio


def resolve_symbol_price(symbol: str, feed_prices: dict[str, float]) -> float | None:
    """Resolve a symbol price through the same feed path used for classification."""
    feeds = price_feed_requirements(symbol, set(feed_prices))
    if not feeds or any(feed not in feed_prices for feed in feeds):
        return None
    if len(feeds) == 1:
        return float(feed_prices[feeds[0]])
    ratio = _ratio(feed_prices.get(feeds[0]))
    base = feed_prices.get(feeds[1])
    if ratio is None or base is None:
        return None
    return float(ratio) * float(base)


def classify_price_support(
    loan_symbol: str,
    collateral_symbol: str,
    available_feeds: set[str],
) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
    loan_feeds = price_feed_requirements(loan_symbol, available_feeds)
    collateral_feeds = price_feed_requirements(collateral_symbol, available_feeds)
    if not loan_feeds or not collateral_feeds:
        return "UNSUPPORTED_ORACLE", loan_feeds, collateral_feeds, "missing Chainlink feed mapping"
    missing = [feed for feed in (*loan_feeds, *collateral_feeds) if feed not in available_feeds]
    if missing:
        return "UNPRICED", loan_feeds, collateral_feeds, "missing Chainlink worker feed: " + ", ".join(sorted(set(missing)))
    probe_prices = {feed: 1.0 for feed in (*loan_feeds, *collateral_feeds)}
    if resolve_symbol_price(loan_symbol, probe_prices) is None or resolve_symbol_price(collateral_symbol, probe_prices) is None:
        return "UNSUPPORTED_ORACLE", loan_feeds, collateral_feeds, "missing resolver path"
    return "CHAINLINK_SUPPORTED", loan_feeds, collateral_feeds, ""


def _borrow_apy_from_rate(rate_wad: int) -> float:
    if rate_wad <= 0:
        return 0.0
    per_second = float(rate_wad) / WAD
    annual = per_second * SECONDS_PER_YEAR
    if annual < 0.50:
        try:
            return min(math.expm1(math.log1p(per_second) * SECONDS_PER_YEAR), 10.0)
        except (OverflowError, ValueError):
            pass
    return min(annual, 10.0)


class MorphoSource(BaseSource):
    name = MORPHO_MARKET
    contracts = [MORPHO_BLUE]
    topics = list(EVENT_MAP.keys())
    raw_table = "morpho_events"
    genesis_block = MORPHO_BLUE_GENESIS_BLOCK

    def __init__(self):
        self._markets: dict[str, MorphoMarketState] = {}
        self._params: dict[str, MorphoMarketParams] = {}
        self._positions: dict[tuple[str, str], MorphoUserPosition] = {}
        self._oracle_support: dict[str, tuple[str, tuple[str, ...], tuple[str, ...], str]] = {}
        self._available_feeds: set[str] = set()
        self._touched_markets: set[str] = set()
        self._touched_positions: set[tuple[str, str]] = set()
        self._event_facts: list[dict] = []
        self._initialized = False

    def get_cursor(self, ch) -> int:
        if not self._initialized:
            self._ensure_tables(ch)
            self._load_available_feeds(ch)
            self._load_params(ch)
            self._load_state(ch)
            self._load_positions(ch)
            self._initialized = True
            log.info(
                "[%s] Initialized %s markets, %s durable states, %s positions, %s Chainlink feeds",
                self.name,
                len(self._params),
                len(self._markets),
                len(self._positions),
                len(self._available_feeds),
            )
        result = ch.command("SELECT max(block_number) FROM morpho_events")
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        topics = log_entry.topics or []
        return EVENT_MAP.get(topics[0], "") if topics else ""

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topics = log_entry.topics or []
        if not topics:
            return None
        event_name = self._event_name(log_entry)
        if not event_name:
            return None
        ts = block_ts_map.get(log_entry.block_number, datetime.datetime.now(datetime.UTC))
        ts = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
        words = _words(log_entry.data or "")

        if event_name == "CreateMarket":
            if len(topics) < 2 or len(words) < 5:
                return None
            market_id = str(topics[1]).lower()
            loan_token = _word_address(words[0])
            collateral_token = _word_address(words[1])
            loan_symbol, loan_decimals = _token_meta(loan_token)
            collateral_symbol, collateral_decimals = _token_meta(collateral_token)
            params = MorphoMarketParams(
                market_id=market_id,
                loan_token=loan_token,
                collateral_token=collateral_token,
                loan_symbol=loan_symbol,
                collateral_symbol=collateral_symbol,
                loan_decimals=loan_decimals,
                collateral_decimals=collateral_decimals,
                oracle=_word_address(words[2]),
                irm=_word_address(words[3]),
                lltv=_uint(words, 4),
                creation_block=int(log_entry.block_number),
                creation_timestamp=ts,
            )
            self._params[market_id] = params
            self._markets.setdefault(market_id, MorphoMarketState())
            self._touched_markets.add(market_id)
            return {"kind": "market_params", "market_id": market_id}

        if len(topics) < 2:
            return None
        market_id = str(topics[1]).lower()
        state = self._markets.setdefault(market_id, MorphoMarketState())

        caller = _topic_address(topics, 2)
        on_behalf = _topic_address(topics, 3)
        receiver = ""
        assets = shares = collateral_assets = 0
        repaid_assets = repaid_shares = seized_assets = bad_debt_assets = bad_debt_shares = 0
        interest = fee_shares = 0
        user = on_behalf

        def position_for(position_user: str) -> MorphoUserPosition | None:
            if not position_user:
                return None
            key = (market_id, position_user.lower())
            position = self._positions.setdefault(key, MorphoUserPosition())
            position.last_event_block = int(log_entry.block_number)
            position.last_event_timestamp = ts
            self._touched_positions.add(key)
            return position

        if event_name == "Supply":
            assets = _uint(words, 0)
            shares = _uint(words, 1)
            state.total_supply_assets += assets
            state.total_supply_shares += shares
            position = position_for(user)
            if position:
                position.supply_shares += shares
        elif event_name == "Withdraw":
            receiver = _word_address(words[0]) if words else ""
            assets = _uint(words, 1)
            shares = _uint(words, 2)
            state.total_supply_assets = _clip_nonnegative(state.total_supply_assets - assets)
            state.total_supply_shares = _clip_nonnegative(state.total_supply_shares - shares)
            position = position_for(user)
            if position:
                position.supply_shares = _clip_nonnegative(position.supply_shares - shares)
        elif event_name == "Borrow":
            receiver = _word_address(words[0]) if words else ""
            assets = _uint(words, 1)
            shares = _uint(words, 2)
            state.total_borrow_assets += assets
            state.total_borrow_shares += shares
            position = position_for(user)
            if position:
                position.borrow_shares += shares
        elif event_name == "Repay":
            assets = _uint(words, 0)
            shares = _uint(words, 1)
            state.total_borrow_assets = _clip_nonnegative(state.total_borrow_assets - assets)
            state.total_borrow_shares = _clip_nonnegative(state.total_borrow_shares - shares)
            position = position_for(user)
            if position:
                position.borrow_shares = _clip_nonnegative(position.borrow_shares - shares)
        elif event_name == "SupplyCollateral":
            collateral_assets = _uint(words, 0)
            state.collateral_assets += collateral_assets
            position = position_for(user)
            if position:
                position.collateral_assets += collateral_assets
        elif event_name == "WithdrawCollateral":
            receiver = _word_address(words[0]) if words else ""
            collateral_assets = _uint(words, 1)
            state.collateral_assets = _clip_nonnegative(state.collateral_assets - collateral_assets)
            position = position_for(user)
            if position:
                position.collateral_assets = _clip_nonnegative(position.collateral_assets - collateral_assets)
        elif event_name == "Liquidate":
            user = on_behalf
            repaid_assets = _uint(words, 0)
            repaid_shares = _uint(words, 1)
            seized_assets = _uint(words, 2)
            bad_debt_assets = _uint(words, 3)
            bad_debt_shares = _uint(words, 4)
            state.total_borrow_assets = _clip_nonnegative(state.total_borrow_assets - repaid_assets - bad_debt_assets)
            state.total_borrow_shares = _clip_nonnegative(state.total_borrow_shares - repaid_shares - bad_debt_shares)
            state.total_supply_assets = _clip_nonnegative(state.total_supply_assets - bad_debt_assets)
            state.collateral_assets = _clip_nonnegative(state.collateral_assets - seized_assets)
            position = position_for(user)
            if position:
                position.borrow_shares = _clip_nonnegative(position.borrow_shares - repaid_shares - bad_debt_shares)
                position.collateral_assets = _clip_nonnegative(position.collateral_assets - seized_assets)
        elif event_name == "AccrueInterest":
            state.last_borrow_rate_wad = _uint(words, 0)
            interest = _uint(words, 1)
            fee_shares = _uint(words, 2)
            state.total_borrow_assets += interest
            state.total_supply_assets += interest
            state.total_supply_shares += fee_shares
        elif event_name == "SetFee":
            state.fee_wad = _uint(words, 0)
        else:
            return None

        state.last_event_block = int(log_entry.block_number)
        state.last_event_timestamp = ts
        self._touched_markets.add(market_id)
        self._event_facts.append(
            {
                "block_number": int(log_entry.block_number),
                "timestamp": ts,
                "tx_hash": str(getattr(log_entry, "transaction_hash", "") or ""),
                "log_index": int(getattr(log_entry, "log_index", 0) or 0),
                "market_id": market_id,
                "event_name": event_name,
                "caller": caller,
                "on_behalf": on_behalf,
                "receiver": receiver,
                "assets": str(assets),
                "shares": str(shares),
                "collateral_assets": str(collateral_assets),
                "repaid_assets": str(repaid_assets),
                "repaid_shares": str(repaid_shares),
                "seized_assets": str(seized_assets),
                "bad_debt_assets": str(bad_debt_assets),
                "bad_debt_shares": str(bad_debt_shares),
                "interest_assets": str(interest),
                "fee_shares": str(fee_shares),
                "fee_wad": str(state.fee_wad) if event_name == "SetFee" else "0",
            }
        )
        return {
            "kind": "snapshot",
            "market_id": market_id,
            "block_number": int(log_entry.block_number),
            "timestamp": ts,
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0

        self._load_available_feeds(ch)
        self._persist_params(ch, [row["market_id"] for row in decoded_rows if row.get("kind") == "market_params"])

        snapshot_rows = [row for row in decoded_rows if row.get("kind") == "snapshot"]
        written = 0
        if snapshot_rows:
            written = self._write_snapshots(ch, snapshot_rows)

        self._persist_event_facts(ch)
        self._persist_state(ch)
        self._persist_positions(ch)
        return written

    def _ensure_tables(self, ch) -> None:
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
                last_event_block UInt64,
                last_event_timestamp DateTime,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY market_id
            """
        )
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
        for alter in (
            "ALTER TABLE morpho_market_positions ADD COLUMN IF NOT EXISTS updated_at DateTime DEFAULT now()",
            "ALTER TABLE morpho_market_events ADD COLUMN IF NOT EXISTS inserted_at DateTime DEFAULT now()",
        ):
            ch.command(alter)
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
        ensure_morpho_oracle_snapshot_tables(ch)
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS morpho_chainlink_timeseries (
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

    def _load_available_feeds(self, ch) -> None:
        try:
            rows = ch.query("SELECT DISTINCT feed FROM chainlink_prices").result_rows
            self._available_feeds = {str(row[0]) for row in rows if row and row[0]}
        except Exception as exc:
            log.warning("[%s] Failed to load Chainlink feeds: %s", self.name, exc)
            self._available_feeds = set()

    def _load_params(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT market_id, loan_token, collateral_token, loan_symbol, collateral_symbol,
                       loan_decimals, collateral_decimals, oracle, irm, toString(lltv),
                       creation_block, creation_timestamp
                FROM morpho_market_params
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            self._params[str(row[0]).lower()] = MorphoMarketParams(
                market_id=str(row[0]).lower(),
                loan_token=str(row[1]).lower(),
                collateral_token=str(row[2]).lower(),
                loan_symbol=str(row[3]),
                collateral_symbol=str(row[4]),
                loan_decimals=int(row[5] or 18),
                collateral_decimals=int(row[6] or 18),
                oracle=str(row[7]).lower(),
                irm=str(row[8]).lower(),
                lltv=int(float(row[9] or 0)),
                creation_block=int(row[10] or 0),
                creation_timestamp=row[11] or datetime.datetime(1970, 1, 1),
            )

    def _load_state(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT market_id, total_supply_assets, total_supply_shares,
                       total_borrow_assets, total_borrow_shares, collateral_assets,
                       fee_wad, last_borrow_rate_wad, last_event_block, last_event_timestamp
                FROM morpho_market_state FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            self._markets[str(row[0]).lower()] = MorphoMarketState(
                total_supply_assets=int(row[1] or 0),
                total_supply_shares=int(row[2] or 0),
                total_borrow_assets=int(row[3] or 0),
                total_borrow_shares=int(row[4] or 0),
                collateral_assets=int(row[5] or 0),
                fee_wad=int(row[6] or 0),
                last_borrow_rate_wad=int(row[7] or 0),
                last_event_block=int(row[8] or 0),
                last_event_timestamp=row[9] or datetime.datetime(1970, 1, 1),
            )

    def _load_positions(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT market_id, user, supply_shares, borrow_shares, collateral_assets,
                       last_event_block, last_event_timestamp
                FROM morpho_market_positions FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            self._positions[(str(row[0]).lower(), str(row[1]).lower())] = MorphoUserPosition(
                supply_shares=int(row[2] or 0),
                borrow_shares=int(row[3] or 0),
                collateral_assets=int(row[4] or 0),
                last_event_block=int(row[5] or 0),
                last_event_timestamp=row[6] or datetime.datetime(1970, 1, 1),
            )

    def _support_for(self, params: MorphoMarketParams) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        loan_feeds = price_feed_requirements(params.loan_symbol, self._available_feeds)
        collateral_feeds = price_feed_requirements(params.collateral_symbol, self._available_feeds)
        if not loan_feeds:
            support = ("UNSUPPORTED_ORACLE", loan_feeds, collateral_feeds, "missing loan feed mapping")
        else:
            missing_loan = [feed for feed in loan_feeds if feed not in self._available_feeds]
            if missing_loan:
                support = (
                    "UNPRICED",
                    loan_feeds,
                    collateral_feeds,
                    "missing loan-side USD conversion feed: " + ", ".join(sorted(set(missing_loan))),
                )
            elif collateral_feeds:
                missing_collateral = [feed for feed in collateral_feeds if feed not in self._available_feeds]
                if missing_collateral:
                    support = (
                        "UNPRICED",
                        loan_feeds,
                        collateral_feeds,
                        "missing collateral USD conversion feed: " + ", ".join(sorted(set(missing_collateral))),
                    )
                else:
                    probe_prices = {feed: 1.0 for feed in (*loan_feeds, *collateral_feeds)}
                    if (
                        resolve_symbol_price(params.loan_symbol, probe_prices) is None
                        or resolve_symbol_price(params.collateral_symbol, probe_prices) is None
                    ):
                        support = ("UNSUPPORTED_ORACLE", loan_feeds, collateral_feeds, "missing resolver path")
                    else:
                        support = ("CHAINLINK_SUPPORTED", loan_feeds, collateral_feeds, "")
            elif params.oracle.lower() != ZERO_ADDRESS:
                support = (
                    "ORACLE_SNAPSHOT_SUPPORTED",
                    loan_feeds,
                    collateral_feeds,
                    "collateral priced by Morpho IOracle.price() snapshots",
                )
            else:
                support = ("UNSUPPORTED_ORACLE", loan_feeds, collateral_feeds, "missing collateral feed mapping")
        self._oracle_support[params.market_id] = support
        return support

    def _persist_params(self, ch, market_ids: list[str]) -> None:
        rows = []
        support_rows = []
        for market_id in sorted(set(market_ids)):
            params = self._params.get(market_id)
            if not params:
                continue
            rows.append(
                [
                    params.market_id,
                    params.loan_token,
                    params.collateral_token,
                    params.loan_symbol,
                    params.collateral_symbol,
                    params.loan_decimals,
                    params.collateral_decimals,
                    params.oracle,
                    params.irm,
                    str(params.lltv),
                    params.creation_block,
                    params.creation_timestamp,
                ]
            )
            oracle_support, loan_feeds, collateral_feeds, reason = self._support_for(params)
            support_rows.append(
                [
                    params.market_id,
                    oracle_support,
                    params.loan_symbol,
                    params.collateral_symbol,
                    list(loan_feeds),
                    list(collateral_feeds),
                    reason,
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "morpho_market_params",
                rows,
                [
                    "market_id",
                    "loan_token",
                    "collateral_token",
                    "loan_symbol",
                    "collateral_symbol",
                    "loan_decimals",
                    "collateral_decimals",
                    "oracle",
                    "irm",
                    "lltv",
                    "creation_block",
                    "creation_timestamp",
                ],
            )
        if support_rows:
            insert_rows_batched(
                ch,
                "morpho_market_oracle_support",
                support_rows,
                [
                    "market_id",
                    "oracle_support",
                    "loan_symbol",
                    "collateral_symbol",
                    "loan_price_feeds",
                    "collateral_price_feeds",
                    "reason",
                ],
            )

    def _persist_state(self, ch) -> None:
        if not self._touched_markets:
            return
        rows = []
        for market_id in sorted(self._touched_markets):
            state = self._markets.get(market_id)
            if not state:
                continue
            rows.append(
                [
                    market_id,
                    str(state.total_supply_assets),
                    str(state.total_supply_shares),
                    str(state.total_borrow_assets),
                    str(state.total_borrow_shares),
                    str(state.collateral_assets),
                    str(state.fee_wad),
                    str(state.last_borrow_rate_wad),
                    state.last_event_block,
                    state.last_event_timestamp,
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "morpho_market_state",
                rows,
                [
                    "market_id",
                    "total_supply_assets",
                    "total_supply_shares",
                    "total_borrow_assets",
                    "total_borrow_shares",
                    "collateral_assets",
                    "fee_wad",
                    "last_borrow_rate_wad",
                    "last_event_block",
                    "last_event_timestamp",
                ],
            )
        self._touched_markets.clear()

    def _persist_positions(self, ch) -> None:
        if not self._touched_positions:
            return
        rows = []
        for key in sorted(self._touched_positions):
            position = self._positions.get(key)
            if not position:
                continue
            market_id, user = key
            rows.append(
                [
                    market_id,
                    user,
                    str(position.supply_shares),
                    str(position.borrow_shares),
                    str(position.collateral_assets),
                    position.last_event_block,
                    position.last_event_timestamp,
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "morpho_market_positions",
                rows,
                [
                    "market_id",
                    "user",
                    "supply_shares",
                    "borrow_shares",
                    "collateral_assets",
                    "last_event_block",
                    "last_event_timestamp",
                ],
            )
        self._touched_positions.clear()

    def _persist_event_facts(self, ch) -> None:
        if not self._event_facts:
            return
        df = pd.DataFrame(self._event_facts)
        insert_df_batched(ch, "morpho_market_events", df)
        self._event_facts.clear()

    def _price_frame(self, ch, min_ts: datetime.datetime, max_ts: datetime.datetime, feeds: set[str]) -> pd.DataFrame:
        if not feeds:
            return pd.DataFrame()
        escaped = ", ".join("'" + feed.replace("'", "''") + "'" for feed in sorted(feeds))
        start = (pd.to_datetime(min_ts) - pd.Timedelta(days=730)).strftime("%Y-%m-%d %H:%M:%S")
        end = pd.to_datetime(max_ts).strftime("%Y-%m-%d %H:%M:%S")
        df = ch.query_df(
            f"""
            SELECT toStartOfHour(timestamp) AS ts, feed, argMax(price, timestamp) AS price
            FROM chainlink_prices
            WHERE feed IN ({escaped})
              AND timestamp >= '{start}'
              AND timestamp <= '{end}'
            GROUP BY ts, feed
            ORDER BY ts, feed
            """
        )
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot_table(index="ts", columns="feed", values="price", aggfunc="last").sort_index()
        return pivot.ffill()

    def _oracle_snapshot_frame(self, ch, min_ts: datetime.datetime, max_ts: datetime.datetime, oracles: set[str]) -> pd.DataFrame:
        if not oracles:
            return pd.DataFrame()
        ensure_morpho_oracle_snapshot_tables(ch)
        escaped = ", ".join("'" + oracle.replace("'", "''") + "'" for oracle in sorted(oracles))
        start = (pd.to_datetime(min_ts) - pd.Timedelta(days=730)).strftime("%Y-%m-%d %H:%M:%S")
        end = pd.to_datetime(max_ts).strftime("%Y-%m-%d %H:%M:%S")
        df = ch.query_df(
            f"""
            SELECT toStartOfHour(timestamp) AS ts,
                   oracle,
                   argMax(toFloat64OrZero(price_raw), inserted_at) AS price_raw
            FROM morpho_oracle_snapshots
            WHERE oracle IN ({escaped})
              AND status = 'OK'
              AND timestamp >= '{start}'
              AND timestamp <= '{end}'
            GROUP BY ts, oracle
            ORDER BY ts, oracle
            """
        )
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot_table(index="ts", columns="oracle", values="price_raw", aggfunc="last").sort_index()
        return pivot.ffill()

    def _write_snapshots(self, ch, snapshot_rows: list[dict]) -> int:
        df = pd.DataFrame(snapshot_rows)
        if df.empty:
            return 0
        df["ts"] = pd.to_datetime(df["timestamp"]).dt.floor("h")
        df.sort_values("block_number", inplace=True)
        hourly = df.groupby(["ts", "market_id"]).last().reset_index()

        required_feeds: set[str] = set()
        supported_market_ids: set[str] = set()
        oracle_priced_market_ids: set[str] = set()
        required_oracles: set[str] = set()
        for market_id in hourly["market_id"].unique():
            params = self._params.get(str(market_id))
            if not params:
                continue
            support, loan_feeds, collateral_feeds, _reason = self._support_for(params)
            if support == "CHAINLINK_SUPPORTED":
                supported_market_ids.add(str(market_id))
                required_feeds.update(loan_feeds)
                required_feeds.update(collateral_feeds)
            elif support == "ORACLE_SNAPSHOT_SUPPORTED":
                supported_market_ids.add(str(market_id))
                oracle_priced_market_ids.add(str(market_id))
                required_feeds.update(loan_feeds)
                required_oracles.add(params.oracle.lower())

        if not supported_market_ids:
            return 0

        prices = self._price_frame(ch, hourly["ts"].min(), hourly["ts"].max(), required_feeds)
        if prices.empty:
            return 0
        oracle_prices = self._oracle_snapshot_frame(ch, hourly["ts"].min(), hourly["ts"].max(), required_oracles)
        if oracle_priced_market_ids and oracle_prices.empty:
            log.info("[%s] Oracle-priced markets are waiting for morpho_oracle_snapshots", self.name)

        metrics = []
        for row in hourly.itertuples(index=False):
            market_id = str(row.market_id)
            if market_id not in supported_market_ids:
                continue
            params = self._params.get(market_id)
            state = self._markets.get(market_id)
            if not params or not state:
                continue
            ts = pd.to_datetime(row.ts)
            price_row = prices.loc[prices.index <= ts]
            if price_row.empty:
                continue
            feed_prices = {
                str(feed): float(value)
                for feed, value in price_row.iloc[-1].dropna().items()
            }
            loan_price = resolve_symbol_price(params.loan_symbol, feed_prices)
            if loan_price is None:
                continue

            supply_tokens = state.total_supply_assets / (10 ** params.loan_decimals)
            borrow_tokens = state.total_borrow_assets / (10 ** params.loan_decimals)
            collateral_tokens = state.collateral_assets / (10 ** params.collateral_decimals)
            supply_usd = supply_tokens * loan_price
            borrow_usd = borrow_tokens * loan_price
            oracle_support_label = "CHAINLINK_SUPPORTED"
            if market_id in oracle_priced_market_ids:
                oracle_price_rows = oracle_prices.loc[oracle_prices.index <= ts] if not oracle_prices.empty else pd.DataFrame()
                if oracle_price_rows.empty or params.oracle.lower() not in oracle_price_rows.columns:
                    continue
                oracle_price_raw = oracle_price_rows.iloc[-1].get(params.oracle.lower())
                collateral_usd = collateral_value_usd_from_oracle(
                    state.collateral_assets,
                    oracle_price_raw,
                    params.loan_decimals,
                    loan_price,
                )
                if collateral_usd is None:
                    continue
                collateral_price = collateral_usd / collateral_tokens if collateral_tokens > 0 else 0.0
                oracle_support_label = "ORACLE_SNAPSHOT_SUPPORTED"
            else:
                collateral_price = resolve_symbol_price(params.collateral_symbol, feed_prices)
                if collateral_price is None:
                    continue
                collateral_usd = collateral_tokens * collateral_price
            utilization = min(max(borrow_tokens / supply_tokens, 0.0), 1.0) if supply_tokens > 0 else 0.0
            borrow_apy = _borrow_apy_from_rate(state.last_borrow_rate_wad)
            fee = max(0.0, min(float(state.fee_wad) / WAD, 1.0))
            supply_apy = max(0.0, borrow_apy * utilization * (1.0 - fee))
            lltv = float(params.lltv) / WAD if params.lltv else 0.0
            metrics.append(
                {
                    "timestamp": ts.to_pydatetime(),
                    "market_id": market_id,
                    "entity_id": market_id,
                    "loan_symbol": params.loan_symbol,
                    "collateral_symbol": params.collateral_symbol,
                    "supply_usd": float(supply_usd),
                    "borrow_usd": float(borrow_usd),
                    "collateral_usd": float(collateral_usd),
                    "supply_apy": float(supply_apy),
                    "borrow_apy": float(borrow_apy),
                    "utilization": float(utilization),
                    "loan_price_usd": float(loan_price),
                    "collateral_price_usd": float(collateral_price),
                    "lltv": float(lltv),
                    "oracle": params.oracle,
                    "oracle_support": oracle_support_label,
                }
            )

        if not metrics:
            return 0

        metrics_df = pd.DataFrame(metrics)
        insert_df_batched(ch, "morpho_market_metrics", metrics_df)

        final = pd.DataFrame(
            {
                "timestamp": metrics_df["timestamp"],
                "protocol": MORPHO_MARKET,
                "symbol": metrics_df["loan_symbol"],
                "entity_id": metrics_df["entity_id"],
                "target_id": metrics_df["collateral_symbol"],
                "supply_usd": metrics_df["supply_usd"],
                "borrow_usd": metrics_df["borrow_usd"],
                "supply_apy": metrics_df["supply_apy"],
                "borrow_apy": metrics_df["borrow_apy"],
                "utilization": metrics_df["utilization"],
                "price_usd": metrics_df["loan_price_usd"],
            }
        )
        final = forward_fill_hourly(final, ch, MORPHO_MARKET, compound=False)
        if final.empty:
            return 0

        min_ts_dt = final["timestamp"].min()
        max_ts_dt = final["timestamp"].max()
        min_ts = pd.to_datetime(min_ts_dt).strftime("%Y-%m-%d %H:%M:%S")
        max_ts = pd.to_datetime(max_ts_dt).strftime("%Y-%m-%d %H:%M:%S")
        rewrite_protocol_window_if_enabled(ch, self.output_table, MORPHO_MARKET, min_ts, max_ts)
        insert_df_batched(ch, self.output_table, final)
        upsert_market_timeseries(ch, final)
        upsert_api_market_latest(ch, final)
        refresh_api_protocol_tvl_weekly(ch, min_ts_dt, max_ts_dt)
        return len(final)
