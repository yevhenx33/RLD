"""
FluidSource - Fluid Liquidity Layer reserve analytics.

Decodes Fluid Liquidity Layer reserve snapshots from LogOperate and
LogUpdateExchangePrices, prices only strictly supported assets, and routes
priced reserve rows through the canonical market serving tables.
"""

from __future__ import annotations

import datetime
import logging
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
from ..fluid_full_coverage import ETHEREUM_CHAIN_ID, ensure_fluid_full_coverage_tables, needs_explicit_snapshot, support_hint
from ..protocols import FLUID_MARKET
from ..tokens import TOKENS
from .morpho import price_feed_requirements, resolve_symbol_price

log = logging.getLogger("indexer.fluid")

FLUID_LIQUIDITY = "0x52aa899454998be5b000ad077a46bbe360f4e497"
FLUID_GENESIS_BLOCK = 19_258_464

TOPIC_LOG_OPERATE = "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15"
TOPIC_LOG_UPDATE_EXCHANGE_PRICES = "0x96c40bed7fc8d0ac41633a3bd47f254f0b0076e5df70975c51d23514bc49d3b8"

EVENT_MAP = {TOPIC_LOG_OPERATE: "LogOperate", TOPIC_LOG_UPDATE_EXCHANGE_PRICES: "LogUpdateExchangePrices"}
MASK_64 = (1 << 64) - 1


@dataclass
class FluidReserveState:
    token: str
    symbol: str
    decimals: int
    total_supply_tokens: float = 0.0
    total_borrow_tokens: float = 0.0
    utilization: float = 0.0
    borrow_apy: float = 0.0
    supply_apy: float = 0.0
    fee: float = 0.0
    supply_exchange_price: str = "0"
    borrow_exchange_price: str = "0"
    last_event_block: int = 0
    last_event_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


def bigmath(packed: int) -> int:
    """Fluid BigMath: 56-bit coefficient + 8-bit exponent."""
    return (packed >> 8) << (packed & 0xFF)


def _topic_address(topic: str | None) -> str:
    raw = str(topic or "").removeprefix("0x")
    return "0x" + raw[-40:].lower()


def _words(data: str | None) -> list[int]:
    raw = str(data or "").removeprefix("0x")
    if len(raw) % 64 != 0:
        raw = raw[: len(raw) - (len(raw) % 64)]
    return [int(raw[i : i + 64], 16) for i in range(0, len(raw), 64) if len(raw[i : i + 64]) == 64]


def _token_meta(address: str) -> tuple[str, int] | None:
    meta = TOKENS.get(address.removeprefix("0x").lower())
    if not meta:
        return None
    return str(meta[0]), int(meta[1])


def _clip(value: float, low: float = 0.0, high: float = 10.0) -> float:
    if value != value:
        return low
    return max(low, min(float(value), high))


def _ratio_or_bps(raw: int, high: float = 10.0) -> float:
    if raw <= 0:
        return 0.0
    if raw <= 10_000:
        return _clip(raw / 10_000.0, 0.0, high)
    return _clip(raw / 1e18, 0.0, high)


def _fluid_symbol_price(symbol: str, feed_prices: dict[str, float]) -> float | None:
    # No Fluid-specific fallback: BTC derivatives and wrappers must have an
    # explicit resolver/snapshot path before they are served in USD.
    return resolve_symbol_price(symbol, feed_prices)


class FluidSource(BaseSource):
    name = FLUID_MARKET
    contracts = [FLUID_LIQUIDITY]
    topics = [TOPIC_LOG_OPERATE, TOPIC_LOG_UPDATE_EXCHANGE_PRICES]
    raw_table = "fluid_events"
    genesis_block = FLUID_GENESIS_BLOCK

    def __init__(self):
        self._states: dict[str, FluidReserveState] = {}
        self._available_feeds: set[str] = set()
        self._touched_tokens: set[str] = set()
        self._initialized = False

    def get_cursor(self, ch) -> int:
        if not self._initialized:
            self._ensure_tables(ch)
            self._load_available_feeds(ch)
            self._load_state(ch)
            self._initialized = True
            log.info("[%s] Initialized %s durable reserve states, %s Chainlink feeds", self.name, len(self._states), len(self._available_feeds))
        result = ch.command("SELECT max(block_number) FROM fluid_events")
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        topics = log_entry.topics or []
        return EVENT_MAP.get(str(topics[0]).lower(), "") if topics else ""

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        if not topics:
            return None
        event_name = self._event_name(log_entry)
        if event_name == "LogOperate":
            return self._decode_operate(log_entry, topics, block_ts_map)
        if event_name == "LogUpdateExchangePrices":
            return self._decode_exchange_prices(log_entry, topics, block_ts_map)
        return None

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0
        self._load_available_feeds(ch)
        snapshot_rows = [row for row in decoded_rows if row.get("kind") == "reserve_snapshot"]
        self._persist_state(ch)
        self._persist_oracle_support(ch)
        if not snapshot_rows:
            self._touched_tokens.clear()
            return 0
        written = self._write_snapshots(ch, snapshot_rows)
        self._touched_tokens.clear()
        return written

    def _decode_operate(self, log_entry, topics: list[str], block_ts_map) -> Optional[dict]:
        if len(topics) < 3:
            return None
        token = _topic_address(topics[2])
        meta = _token_meta(token)
        if not meta:
            return None
        words = _words(log_entry.data)
        if len(words) < 6:
            return None
        symbol, decimals = meta
        w4 = words[4]
        w5 = words[5]
        sup_int = bigmath(w4 & MASK_64)
        sup_free = bigmath((w4 >> 64) & MASK_64)
        bor_int = bigmath((w4 >> 128) & MASK_64)
        bor_free = bigmath((w4 >> 192) & MASK_64)
        sup_ep = (w5 >> 91) & MASK_64 or int(1e12)
        bor_ep = (w5 >> 155) & MASK_64 or int(1e12)
        util_raw = (w5 >> 30) & 0x3FFF
        rate_raw = w5 & 0xFFFF
        fee_raw = (w5 >> 16) & 0x3FFF
        scale = float(10 ** decimals)
        total_supply_tokens = (sup_int * sup_ep / 1e12 + sup_free) / scale
        total_borrow_tokens = (bor_int * bor_ep / 1e12 + bor_free) / scale
        utilization = _clip(util_raw / 10_000.0, 0.0, 1.0)
        borrow_apy = _clip(rate_raw / 10_000.0, 0.0, 10.0)
        fee = _clip(fee_raw / 10_000.0, 0.0, 1.0)
        supply_apy = max(0.0, borrow_apy * utilization * (1.0 - fee))
        ts = self._block_ts(log_entry.block_number, block_ts_map)
        state = FluidReserveState(token=token, symbol=symbol, decimals=decimals, total_supply_tokens=float(total_supply_tokens), total_borrow_tokens=float(total_borrow_tokens), utilization=float(utilization), borrow_apy=float(borrow_apy), supply_apy=float(supply_apy), fee=float(fee), supply_exchange_price=str(sup_ep), borrow_exchange_price=str(bor_ep), last_event_block=int(log_entry.block_number), last_event_timestamp=ts)
        self._states[token] = state
        self._touched_tokens.add(token)
        return self._snapshot_row(state, log_entry.block_number, ts, "LogOperate")

    def _decode_exchange_prices(self, log_entry, topics: list[str], block_ts_map) -> Optional[dict]:
        if len(topics) < 4:
            return None
        token = _topic_address(topics[1])
        meta = _token_meta(token)
        if not meta:
            return None
        words = _words(log_entry.data)
        if len(words) < 2:
            return None
        symbol, decimals = meta
        ts = self._block_ts(log_entry.block_number, block_ts_map)
        previous = self._states.get(token) or FluidReserveState(token=token, symbol=symbol, decimals=decimals)
        borrow_apy = _ratio_or_bps(words[0], high=10.0)
        utilization = _ratio_or_bps(words[1], high=1.0)
        supply_ep = int(str(topics[2]).removeprefix("0x"), 16)
        borrow_ep = int(str(topics[3]).removeprefix("0x"), 16)
        fee = _clip(previous.fee, 0.0, 1.0)
        supply_apy = max(0.0, borrow_apy * utilization * (1.0 - fee))
        state = FluidReserveState(token=token, symbol=symbol, decimals=decimals, total_supply_tokens=previous.total_supply_tokens, total_borrow_tokens=previous.total_borrow_tokens, utilization=float(utilization), borrow_apy=float(borrow_apy), supply_apy=float(supply_apy), fee=float(fee), supply_exchange_price=str(supply_ep), borrow_exchange_price=str(borrow_ep), last_event_block=int(log_entry.block_number), last_event_timestamp=ts)
        self._states[token] = state
        self._touched_tokens.add(token)
        return self._snapshot_row(state, log_entry.block_number, ts, "LogUpdateExchangePrices")

    def _snapshot_row(self, state: FluidReserveState, block_number: int, ts: datetime.datetime, event_name: str) -> dict:
        return {
            "kind": "reserve_snapshot",
            "event_name": event_name,
            "token": state.token,
            "symbol": state.symbol,
            "total_supply_tokens": float(state.total_supply_tokens),
            "total_borrow_tokens": float(state.total_borrow_tokens),
            "supply_apy": float(state.supply_apy),
            "borrow_apy": float(state.borrow_apy),
            "utilization": float(state.utilization),
            "block_number": int(block_number),
            "timestamp": ts,
        }

    def _block_ts(self, block_number: int, block_ts_map) -> datetime.datetime:
        ts = block_ts_map.get(block_number, datetime.datetime.now(datetime.UTC))
        return ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts

    def _ensure_tables(self, ch) -> None:
        ensure_fluid_full_coverage_tables(ch)
        ch.command("""
            CREATE TABLE IF NOT EXISTS fluid_events (
                block_number UInt64, block_timestamp DateTime, tx_hash String, log_index UInt32,
                contract String, event_name LowCardinality(String), topic0 String,
                topic1 Nullable(String), topic2 Nullable(String), topic3 Nullable(String), data String,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(block_timestamp)
            ORDER BY (block_number, log_index, contract, topic0)
            """)
        self._ensure_derived_tables(ch)

    def _ensure_derived_tables(self, ch) -> None:
        ch.command("""
            CREATE TABLE IF NOT EXISTS fluid_reserve_state (
                token String, symbol LowCardinality(String), decimals UInt8,
                total_supply_tokens Float64, total_borrow_tokens Float64,
                utilization Float64, borrow_apy Float64, supply_apy Float64, fee Float64,
                supply_exchange_price String, borrow_exchange_price String,
                last_event_block UInt64, last_event_timestamp DateTime,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY token
            """)
        ch.command("""
            CREATE TABLE IF NOT EXISTS fluid_reserve_oracle_support (
                token String, symbol LowCardinality(String), oracle_support LowCardinality(String),
                price_feeds Array(String), reason String, updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at) ORDER BY token
            """)
        ch.command("""
            CREATE TABLE IF NOT EXISTS fluid_reserve_metrics (
                timestamp DateTime, token String, entity_id String, symbol LowCardinality(String),
                total_supply_tokens Float64, total_borrow_tokens Float64,
                supply_usd Float64, borrow_usd Float64, supply_apy Float64, borrow_apy Float64,
                utilization Float64, price_usd Float64, oracle_support LowCardinality(String),
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (token, timestamp)
            TTL timestamp + INTERVAL 36 MONTH DELETE
            """)
        ch.command("""
            CREATE TABLE IF NOT EXISTS fluid_timeseries (
                timestamp DateTime, protocol LowCardinality(String), symbol LowCardinality(String),
                entity_id String, target_id String, supply_usd Float64, borrow_usd Float64,
                supply_apy Float64, borrow_apy Float64, utilization Float64, price_usd Float64,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (protocol, entity_id, timestamp)
            TTL timestamp + INTERVAL 36 MONTH DELETE
            """)

    def _load_available_feeds(self, ch) -> None:
        try:
            rows = ch.query("SELECT DISTINCT feed FROM chainlink_prices").result_rows
            self._available_feeds = {str(row[0]) for row in rows if row and row[0]}
        except Exception as exc:
            log.warning("[%s] Failed to load Chainlink feeds: %s", self.name, exc)
            self._available_feeds = set()

    def _load_state(self, ch) -> None:
        try:
            rows = ch.query("""
                SELECT token, symbol, decimals, total_supply_tokens, total_borrow_tokens,
                       utilization, borrow_apy, supply_apy, fee, supply_exchange_price,
                       borrow_exchange_price, last_event_block, last_event_timestamp
                FROM fluid_reserve_state FINAL
                """).result_rows
        except Exception:
            rows = []
        for row in rows:
            token = str(row[0]).lower()
            self._states[token] = FluidReserveState(token=token, symbol=str(row[1]), decimals=int(row[2] or 18), total_supply_tokens=float(row[3] or 0.0), total_borrow_tokens=float(row[4] or 0.0), utilization=float(row[5] or 0.0), borrow_apy=float(row[6] or 0.0), supply_apy=float(row[7] or 0.0), fee=float(row[8] or 0.0), supply_exchange_price=str(row[9] or "0"), borrow_exchange_price=str(row[10] or "0"), last_event_block=int(row[11] or 0), last_event_timestamp=row[12] or datetime.datetime(1970, 1, 1))

    def _support_for_symbol(self, symbol: str) -> tuple[str, tuple[str, ...], str]:
        feeds = price_feed_requirements(symbol, self._available_feeds)
        if needs_explicit_snapshot(symbol):
            hint = support_hint(symbol) or "explicit BTC derivative validation snapshot required"
            return "ORACLE_SNAPSHOT_REQUIRED", feeds, hint
        if not feeds:
            return "UNSUPPORTED_ORACLE", feeds, "missing Chainlink feed mapping"
        missing = [feed for feed in feeds if feed not in self._available_feeds]
        if missing:
            return "UNPRICED", feeds, "missing Chainlink worker feed: " + ", ".join(sorted(set(missing)))
        probe_prices = {feed: 1.0 for feed in feeds}
        probe_prices.setdefault("BTC / USD", 1.0)
        probe_prices.setdefault("ETH / USD", 1.0)
        if _fluid_symbol_price(symbol, probe_prices) is None:
            return "UNSUPPORTED_ORACLE", feeds, "price resolver cannot compose symbol from available feeds"
        return "CHAINLINK_SUPPORTED", feeds, ""

    def _persist_state(self, ch) -> None:
        if not self._touched_tokens:
            return
        rows = []
        for token in sorted(self._touched_tokens):
            state = self._states.get(token)
            if not state:
                continue
            rows.append([state.token, state.symbol, state.decimals, state.total_supply_tokens, state.total_borrow_tokens, state.utilization, state.borrow_apy, state.supply_apy, state.fee, state.supply_exchange_price, state.borrow_exchange_price, state.last_event_block, state.last_event_timestamp])
        if rows:
            insert_rows_batched(ch, "fluid_reserve_state", rows, ["token", "symbol", "decimals", "total_supply_tokens", "total_borrow_tokens", "utilization", "borrow_apy", "supply_apy", "fee", "supply_exchange_price", "borrow_exchange_price", "last_event_block", "last_event_timestamp"])

    def _persist_oracle_support(self, ch) -> None:
        tokens = set(self._states.keys()) | set(self._touched_tokens)
        rows = []
        asset_rows = []
        for token in sorted(tokens):
            state = self._states.get(token)
            if not state:
                continue
            support, feeds, reason = self._support_for_symbol(state.symbol)
            rows.append([state.token, state.symbol, support, list(feeds), reason])
            oracle_type = "CHAINLINK" if support == "CHAINLINK_SUPPORTED" else ("SNAPSHOT" if support == "ORACLE_SNAPSHOT_REQUIRED" else "UNKNOWN")
            snapshot_subjects = [state.token] if support == "ORACLE_SNAPSHOT_REQUIRED" else []
            asset_rows.append([ETHEREUM_CHAIN_ID, state.token, state.symbol, support, oracle_type, list(feeds), snapshot_subjects, reason])
        if rows:
            insert_rows_batched(ch, "fluid_reserve_oracle_support", rows, ["token", "symbol", "oracle_support", "price_feeds", "reason"])
        if asset_rows:
            insert_rows_batched(ch, "fluid_asset_oracle_support", asset_rows, ["chain_id", "asset", "symbol", "oracle_support", "oracle_type", "price_feeds", "snapshot_subjects", "reason"])

    def _price_frame(self, ch, min_ts: datetime.datetime, max_ts: datetime.datetime, feeds: set[str]) -> pd.DataFrame:
        if not feeds:
            return pd.DataFrame()
        escaped = ", ".join("'" + feed.replace("'", "''") + "'" for feed in sorted(feeds))
        start = (pd.to_datetime(min_ts) - pd.Timedelta(days=1095)).strftime("%Y-%m-%d %H:%M:%S")
        end = pd.to_datetime(max_ts).strftime("%Y-%m-%d %H:%M:%S")
        df = ch.query_df(f"""
            SELECT toStartOfHour(timestamp) AS ts, feed, argMax(price, timestamp) AS price
            FROM chainlink_prices
            WHERE feed IN ({escaped}) AND timestamp >= '{start}' AND timestamp <= '{end}'
            GROUP BY ts, feed ORDER BY ts, feed
            """)
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot_table(index="ts", columns="feed", values="price", aggfunc="last").sort_index()
        return pivot.ffill()

    def _write_snapshots(self, ch, decoded_rows: list[dict]) -> int:
        df = pd.DataFrame(decoded_rows)
        if df.empty:
            return 0
        df["ts"] = pd.to_datetime(df["timestamp"]).dt.floor("h")
        df.sort_values(["block_number", "token"], inplace=True)
        hourly = df.groupby(["ts", "token"], as_index=False).last()
        required_feeds: set[str] = set()
        supported_tokens: set[str] = set()
        snapshot_prices: dict[str, float] = {}
        try:
            snapshot_rows = ch.query("""
                SELECT subject, argMax(price_usd, tuple(timestamp, block_number)) AS price
                FROM oracle_snapshots FINAL
                WHERE source = 'FLUID' AND status = 'OK'
                GROUP BY subject
                """).result_rows
            snapshot_prices = {str(subject).lower(): float(price or 0.0) for subject, price in snapshot_rows if price}
        except Exception:
            snapshot_prices = {}
        for token in hourly["token"].unique():
            token_rows = hourly[hourly["token"] == str(token)]
            if token_rows.empty:
                continue
            symbol = str(token_rows["symbol"].iloc[-1])
            support, feeds, _reason = self._support_for_symbol(symbol)
            if support == "CHAINLINK_SUPPORTED":
                supported_tokens.add(str(token))
                required_feeds.update(feeds)
            elif support == "ORACLE_SNAPSHOT_REQUIRED" and str(token).lower() in snapshot_prices:
                supported_tokens.add(str(token))
        if not supported_tokens:
            return 0
        prices = self._price_frame(ch, hourly["ts"].min(), hourly["ts"].max(), required_feeds) if required_feeds else pd.DataFrame()
        if prices.empty and not snapshot_prices:
            return 0
        metrics = []
        for row in hourly.itertuples(index=False):
            token = str(row.token)
            if token not in supported_tokens:
                continue
            ts = pd.to_datetime(row.ts)
            support, feeds, _reason = self._support_for_symbol(str(row.symbol))
            price = None
            oracle_support = support
            if support == "CHAINLINK_SUPPORTED":
                if prices.empty:
                    continue
                price_rows = prices.loc[prices.index <= ts]
                if price_rows.empty:
                    continue
                feed_prices = {str(feed): float(value) for feed, value in price_rows.iloc[-1].dropna().items()}
                price = _fluid_symbol_price(str(row.symbol), feed_prices)
            elif support == "ORACLE_SNAPSHOT_REQUIRED":
                price = snapshot_prices.get(token.lower())
                oracle_support = "ORACLE_SNAPSHOT_SUPPORTED" if price else support
            if price is None or price <= 0:
                continue
            metrics.append({"timestamp": ts.to_pydatetime(), "token": token, "entity_id": token, "symbol": str(row.symbol), "total_supply_tokens": float(row.total_supply_tokens), "total_borrow_tokens": float(row.total_borrow_tokens), "supply_usd": float(row.total_supply_tokens * price), "borrow_usd": float(row.total_borrow_tokens * price), "supply_apy": float(row.supply_apy), "borrow_apy": float(row.borrow_apy), "utilization": float(row.utilization), "price_usd": float(price), "oracle_support": oracle_support})
        if not metrics:
            return 0
        metrics_df = pd.DataFrame(metrics)
        insert_df_batched(ch, "fluid_reserve_metrics", metrics_df)
        final = pd.DataFrame({"timestamp": metrics_df["timestamp"], "protocol": FLUID_MARKET, "symbol": metrics_df["symbol"], "entity_id": metrics_df["entity_id"], "target_id": "", "supply_usd": metrics_df["supply_usd"], "borrow_usd": metrics_df["borrow_usd"], "supply_apy": metrics_df["supply_apy"], "borrow_apy": metrics_df["borrow_apy"], "utilization": metrics_df["utilization"], "price_usd": metrics_df["price_usd"]})
        final = final[(final["supply_usd"] > 0) | (final["borrow_usd"] > 0)]
        if final.empty:
            return 0
        final = forward_fill_hourly(final, ch, FLUID_MARKET, compound=False)
        if final.empty:
            return 0
        min_ts_dt = pd.to_datetime(final["timestamp"].min())
        max_ts_dt = pd.to_datetime(final["timestamp"].max())
        min_ts = min_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        max_ts = max_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        rewrite_protocol_window_if_enabled(ch, self.output_table, FLUID_MARKET, min_ts, max_ts)
        insert_df_batched(ch, self.output_table, final)
        upsert_market_timeseries(ch, final)
        upsert_api_market_latest(ch, final)
        refresh_api_protocol_tvl_weekly(ch, min_ts_dt, max_ts_dt)
        return len(final)
