"""Euler V2 EVK verified vault analytics source.

The collector stores raw EVault logs for factory-created vaults while serving
only vaults verified by Euler's governedPerspective. VaultStatus is treated as
the authoritative event-sourced state for balances and rates.
"""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass
from typing import Optional

import hypersync
import pandas as pd
from eth_utils import keccak

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
from ..euler_schema import ensure_euler_tables
from ..protocols import EULER_MARKET
from ..tokens import TOKENS
from .morpho import price_feed_requirements, resolve_symbol_price

log = logging.getLogger("indexer.euler")

ETHEREUM_CHAIN_ID = 1
EULER_EVAULT_FACTORY = "0x29a56a1b8214d9cf7c5561811750d5cbdb45cc8e"
EULER_GOVERNED_PERSPECTIVE = "0xc0121817ff224a018840e4d15a864747d36e6eb2"
EULER_GENESIS_BLOCK = 20_000_000
SECONDS_PER_YEAR = 365.2425 * 24 * 60 * 60
RAY = 10**27


def _topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


TOPIC_EVAULT_CREATED = _topic("EVaultCreated(address,address,address)")
TOPIC_DEPOSIT = _topic("Deposit(address,address,uint256,uint256)")
TOPIC_WITHDRAW = _topic("Withdraw(address,address,address,uint256,uint256)")
TOPIC_BORROW = _topic("Borrow(address,uint256)")
TOPIC_REPAY = _topic("Repay(address,uint256)")
TOPIC_INTEREST_ACCRUED = _topic("InterestAccrued(address,uint256)")
TOPIC_LIQUIDATE = _topic("Liquidate(address,address,address,uint256,uint256)")
TOPIC_VAULT_STATUS = _topic("VaultStatus(uint256,uint256,uint256,uint256,uint256,uint256,uint256)")
TOPIC_GOV_SET_INTEREST_FEE = _topic("GovSetInterestFee(uint16)")
TOPIC_GOV_SET_INTEREST_RATE_MODEL = _topic("GovSetInterestRateModel(address)")
TOPIC_GOV_SET_CAPS = _topic("GovSetCaps(uint16,uint16)")
TOPIC_GOV_SET_CONFIG_FLAGS = _topic("GovSetConfigFlags(uint32)")

EVENT_MAP = {
    TOPIC_EVAULT_CREATED: "EVaultCreated",
    TOPIC_DEPOSIT: "Deposit",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_BORROW: "Borrow",
    TOPIC_REPAY: "Repay",
    TOPIC_INTEREST_ACCRUED: "InterestAccrued",
    TOPIC_LIQUIDATE: "Liquidate",
    TOPIC_VAULT_STATUS: "VaultStatus",
    TOPIC_GOV_SET_INTEREST_FEE: "GovSetInterestFee",
    TOPIC_GOV_SET_INTEREST_RATE_MODEL: "GovSetInterestRateModel",
    TOPIC_GOV_SET_CAPS: "GovSetCaps",
    TOPIC_GOV_SET_CONFIG_FLAGS: "GovSetConfigFlags",
}
EULER_VAULT_TOPICS = tuple(EVENT_MAP.keys())
EULER_STATE_TOPICS = (TOPIC_VAULT_STATUS, TOPIC_GOV_SET_INTEREST_FEE)
ZERO_ADDRESS = "0x" + "0" * 40


@dataclass
class EulerVaultMetadata:
    vault_address: str
    asset_address: str = ""
    asset_symbol: str = ""
    asset_decimals: int = 18
    verified: bool = False
    source: str = "event"
    created_block: int = 0
    created_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)
    name: str = ""


@dataclass
class EulerVaultState:
    vault_address: str
    total_shares: int = 0
    total_borrows: int = 0
    accumulated_fees: int = 0
    cash: int = 0
    interest_accumulator: int = 0
    interest_rate: int = 0
    interest_fee: int = 1000
    last_event_block: int = 0
    last_event_timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)


def normalize_address(value: str | None) -> str:
    raw = str(value or "").lower().removeprefix("0x")
    if len(raw) < 40:
        raw = raw.rjust(40, "0")
    return "0x" + raw[-40:]


def _word_address(word: str | int | None) -> str:
    if isinstance(word, int):
        word = f"{word:064x}"
    raw = str(word or "").removeprefix("0x").rjust(64, "0")
    return "0x" + raw[-40:].lower()


def _topic_address(topics: list[str], idx: int) -> str:
    if idx >= len(topics) or not topics[idx]:
        return ""
    return _word_address(topics[idx])


def _words(data: str | None) -> list[str]:
    raw = str(data or "").removeprefix("0x")
    if len(raw) % 64 != 0:
        raw = raw[: len(raw) - (len(raw) % 64)]
    return [raw[i : i + 64] for i in range(0, len(raw), 64) if len(raw[i : i + 64]) == 64]


def _uint(words: list[str], idx: int) -> int:
    if idx >= len(words):
        return 0
    return int(words[idx], 16)


def _token_meta(address: str) -> tuple[str, int]:
    symbol, decimals = TOKENS.get(address.removeprefix("0x").lower(), (address[:10], 18))
    return str(symbol), int(decimals)


def _block_ts(block_number: int, block_ts_map) -> datetime.datetime:
    ts = block_ts_map.get(block_number, datetime.datetime.now(datetime.UTC))
    return ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts


def spy_to_apy(spy: int) -> float:
    """Convert Euler seconds-per-year rate to annual percentage yield."""
    if spy <= 0:
        return 0.0
    per_second = float(spy) / RAY
    annual = per_second * SECONDS_PER_YEAR
    if annual < 0.50:
        try:
            return min(math.expm1(math.log1p(per_second) * SECONDS_PER_YEAR), 10.0)
        except (OverflowError, ValueError):
            pass
    return min(max(annual, 0.0), 10.0)


def interest_fee_ratio(raw_fee: int) -> float:
    if raw_fee <= 0:
        return 0.0
    if raw_fee <= 10_000:
        return max(0.0, min(float(raw_fee) / 10_000.0, 1.0))
    return max(0.0, min(float(raw_fee) / 1e18, 1.0))


class EulerSource(BaseSource):
    name = EULER_MARKET
    contracts: list[str] = []
    topics = list(EULER_VAULT_TOPICS)
    raw_table = "euler_events"
    genesis_block = EULER_GENESIS_BLOCK

    def __init__(self):
        self._registry: dict[str, EulerVaultMetadata] = {}
        self._states: dict[str, EulerVaultState] = {}
        self._available_feeds: set[str] = set()
        self._touched_vaults: set[str] = set()
        self._registry_updates: dict[str, EulerVaultMetadata] = {}
        self._initialized = False

    def log_selection(self) -> hypersync.LogSelection:
        if self._registry:
            return hypersync.LogSelection(address=sorted(self._registry), topics=[list(EULER_VAULT_TOPICS)])
        return hypersync.LogSelection(topics=[[TOPIC_EVAULT_CREATED]])

    def route(self, log_entry) -> bool:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        if not topics:
            return False
        topic0 = topics[0]
        if topic0 == TOPIC_EVAULT_CREATED:
            return True
        return topic0 in EVENT_MAP and normalize_address(log_entry.address) in self._registry

    def get_cursor(self, ch) -> int:
        self._ensure_tables(ch)
        self._load_available_feeds(ch)
        self._load_registry(ch)
        if not self._initialized:
            self._load_state(ch)
            self._initialized = True
        result = ch.command("SELECT max(block_number) FROM euler_events")
        return int(result) if result else 0

    def _ensure_tables(self, ch) -> None:
        ensure_euler_tables(ch)

    def _event_name(self, log_entry) -> str:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        return EVENT_MAP.get(topics[0], "") if topics else ""

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topics = [str(t).lower() for t in (log_entry.topics or [])]
        if not topics:
            return None
        event_name = EVENT_MAP.get(topics[0])
        if not event_name:
            return None
        if event_name == "EVaultCreated":
            return self._decode_evault_created(log_entry, topics, block_ts_map)
        vault_address = normalize_address(log_entry.address)
        self._ensure_registry_placeholder(vault_address)
        if event_name == "VaultStatus":
            return self._decode_vault_status(log_entry, topics, block_ts_map)
        if event_name == "GovSetInterestFee":
            return self._decode_interest_fee(log_entry, topics, block_ts_map)
        return self._decode_fact(log_entry, topics, block_ts_map, event_name)

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0
        self._load_available_feeds(ch)
        registry_rows = [row for row in decoded_rows if row.get("kind") == "registry"]
        if registry_rows:
            self._persist_registry_rows(ch, registry_rows)
        facts = [row for row in decoded_rows if row.get("kind") in {"event", "snapshot", "config"}]
        if facts:
            self._persist_events(ch, facts)
        self._persist_state(ch)
        self._persist_oracle_support(ch)
        snapshots = [row for row in decoded_rows if row.get("kind") in {"snapshot", "config"}]
        written = self._write_snapshots(ch, snapshots)
        self._registry_updates.clear()
        self._touched_vaults.clear()
        return written

    def _decode_evault_created(self, log_entry, topics: list[str], block_ts_map) -> dict:
        ts = _block_ts(log_entry.block_number, block_ts_map)
        words = _words(log_entry.data)
        creator = _topic_address(topics, 1)
        asset = _topic_address(topics, 2)
        emitted_from = normalize_address(log_entry.address)
        d_token = _word_address(words[0]) if words else ""
        vault = emitted_from if emitted_from != EULER_EVAULT_FACTORY else d_token
        symbol, decimals = _token_meta(asset)
        meta = EulerVaultMetadata(
            vault_address=vault,
            asset_address=asset,
            asset_symbol=symbol,
            asset_decimals=decimals,
            verified=self._registry.get(vault, EulerVaultMetadata(vault)).verified,
            source="factory",
            created_block=int(log_entry.block_number),
            created_timestamp=ts,
            name="",
        )
        self._registry[vault] = meta
        self._registry_updates[vault] = meta
        return {
            "kind": "registry",
            "vault_address": vault,
            "asset_address": asset,
            "asset_symbol": symbol,
            "asset_decimals": decimals,
            "verified": int(meta.verified),
            "source": "factory",
            "created_block": int(log_entry.block_number),
            "created_timestamp": ts,
            "name": "",
            "creator": creator,
            "d_token": d_token,
        }

    def _decode_vault_status(self, log_entry, topics: list[str], block_ts_map) -> Optional[dict]:
        words = _words(log_entry.data)
        if len(words) < 7:
            return None
        vault = normalize_address(log_entry.address)
        ts = _block_ts(log_entry.block_number, block_ts_map)
        previous = self._states.get(vault) or EulerVaultState(vault_address=vault)
        state = EulerVaultState(
            vault_address=vault,
            total_shares=_uint(words, 0),
            total_borrows=_uint(words, 1),
            accumulated_fees=_uint(words, 2),
            cash=_uint(words, 3),
            interest_accumulator=_uint(words, 4),
            interest_rate=_uint(words, 5),
            interest_fee=previous.interest_fee,
            last_event_block=int(log_entry.block_number),
            last_event_timestamp=ts,
        )
        self._states[vault] = state
        self._touched_vaults.add(vault)
        row = self._event_fact(log_entry, topics, ts, "VaultStatus")
        row.update(
            {
                "kind": "snapshot",
                "timestamp": ts,
                "vault_address": vault,
                "total_shares": str(state.total_shares),
                "total_borrows": str(state.total_borrows),
                "accumulated_fees": str(state.accumulated_fees),
                "cash": str(state.cash),
                "interest_accumulator": str(state.interest_accumulator),
                "interest_rate": str(state.interest_rate),
            }
        )
        return row

    def _decode_interest_fee(self, log_entry, topics: list[str], block_ts_map) -> dict:
        vault = normalize_address(log_entry.address)
        ts = _block_ts(log_entry.block_number, block_ts_map)
        words = _words(log_entry.data)
        previous = self._states.get(vault) or EulerVaultState(vault_address=vault)
        previous.interest_fee = _uint(words, 0)
        if previous.interest_fee == 0 and len(topics) > 1:
            previous.interest_fee = int(str(topics[1]).removeprefix("0x") or "0", 16)
        previous.last_event_block = int(log_entry.block_number)
        previous.last_event_timestamp = ts
        self._states[vault] = previous
        self._touched_vaults.add(vault)
        row = self._event_fact(log_entry, topics, ts, "GovSetInterestFee")
        row.update({"kind": "config", "interest_fee": str(previous.interest_fee)})
        return row

    def _decode_fact(self, log_entry, topics: list[str], block_ts_map, event_name: str) -> dict:
        ts = _block_ts(log_entry.block_number, block_ts_map)
        row = self._event_fact(log_entry, topics, ts, event_name)
        words = _words(log_entry.data)
        if event_name == "Deposit":
            row.update({"sender": _topic_address(topics, 1), "owner": _topic_address(topics, 2), "assets": str(_uint(words, 0)), "shares": str(_uint(words, 1))})
        elif event_name == "Withdraw":
            row.update({"sender": _topic_address(topics, 1), "receiver": _topic_address(topics, 2), "owner": _topic_address(topics, 3), "assets": str(_uint(words, 0)), "shares": str(_uint(words, 1))})
        elif event_name in {"Borrow", "Repay", "InterestAccrued"}:
            row.update({"account": _topic_address(topics, 1), "assets": str(_uint(words, 0))})
        elif event_name == "Liquidate":
            row.update({"account": _topic_address(topics, 2), "sender": _topic_address(topics, 1), "collateral": _word_address(words[0]) if words else "", "repay_assets": str(_uint(words, 1)), "yield_balance": str(_uint(words, 2))})
        elif event_name == "GovSetInterestRateModel":
            row.update({"receiver": _word_address(words[0]) if words else ""})
        elif event_name == "GovSetCaps":
            row.update({"assets": str(_uint(words, 0)), "shares": str(_uint(words, 1))})
        elif event_name == "GovSetConfigFlags":
            row.update({"assets": str(_uint(words, 0))})
        return row

    def _event_fact(self, log_entry, topics: list[str], ts: datetime.datetime, event_name: str) -> dict:
        return {
            "kind": "event",
            "block_number": int(log_entry.block_number),
            "timestamp": ts,
            "tx_hash": str(getattr(log_entry, "transaction_hash", "") or ""),
            "log_index": int(getattr(log_entry, "log_index", 0) or 0),
            "vault_address": normalize_address(getattr(log_entry, "address", "")),
            "event_name": event_name,
            "account": "",
            "sender": "",
            "receiver": "",
            "owner": "",
            "collateral": "",
            "assets": "0",
            "shares": "0",
            "repay_assets": "0",
            "yield_balance": "0",
            "raw_data": str(getattr(log_entry, "data", "") or ""),
        }

    def _ensure_registry_placeholder(self, vault: str) -> None:
        if vault in self._registry:
            return
        meta = EulerVaultMetadata(vault_address=vault, source="event")
        self._registry[vault] = meta
        self._registry_updates[vault] = meta

    def _load_available_feeds(self, ch) -> None:
        try:
            rows = ch.query("SELECT DISTINCT feed FROM chainlink_prices").result_rows
            self._available_feeds = {str(row[0]) for row in rows if row and row[0]}
        except Exception as exc:
            log.warning("[%s] Failed to load Chainlink feeds: %s", self.name, exc)
            self._available_feeds = set()

    def _load_registry(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT
                    vault_address,
                    argMax(asset_address, updated_at),
                    argMax(asset_symbol, updated_at),
                    argMax(asset_decimals, updated_at),
                    argMax(verified, updated_at),
                    argMax(source, updated_at),
                    min(created_block),
                    min(created_timestamp),
                    argMax(name, updated_at)
                FROM euler_vault_registry
                GROUP BY vault_address
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            vault = normalize_address(row[0])
            self._registry[vault] = EulerVaultMetadata(
                vault_address=vault,
                asset_address=normalize_address(row[1]) if row[1] else "",
                asset_symbol=str(row[2] or ""),
                asset_decimals=int(row[3] or 18),
                verified=bool(row[4]),
                source=str(row[5] or ""),
                created_block=int(row[6] or 0),
                created_timestamp=row[7] or datetime.datetime(1970, 1, 1),
                name=str(row[8] or ""),
            )

    def _load_state(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT vault_address, total_shares, total_borrows, accumulated_fees, cash,
                       interest_accumulator, interest_rate, interest_fee,
                       last_event_block, last_event_timestamp
                FROM euler_vault_state FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            vault = normalize_address(row[0])
            self._states[vault] = EulerVaultState(
                vault_address=vault,
                total_shares=int(row[1] or 0),
                total_borrows=int(row[2] or 0),
                accumulated_fees=int(row[3] or 0),
                cash=int(row[4] or 0),
                interest_accumulator=int(row[5] or 0),
                interest_rate=int(row[6] or 0),
                interest_fee=int(row[7] or 0),
                last_event_block=int(row[8] or 0),
                last_event_timestamp=row[9] or datetime.datetime(1970, 1, 1),
            )

    def _support_for(self, meta: EulerVaultMetadata) -> tuple[str, tuple[str, ...], str]:
        if not meta.verified:
            return "UNVERIFIED", (), "vault not present in governedPerspective verified set"
        if not meta.asset_symbol or not meta.asset_address:
            return "MISSING_METADATA", (), "missing EVault asset metadata"
        feeds = price_feed_requirements(meta.asset_symbol, self._available_feeds)
        if not feeds:
            return "UNSUPPORTED_ORACLE", feeds, "missing Chainlink feed mapping"
        missing = [feed for feed in feeds if feed not in self._available_feeds]
        if missing:
            return "UNPRICED", feeds, "missing Chainlink worker feed: " + ", ".join(sorted(set(missing)))
        probe_prices = {feed: 1.0 for feed in feeds}
        if resolve_symbol_price(meta.asset_symbol, probe_prices) is None:
            return "UNSUPPORTED_ORACLE", feeds, "price resolver cannot compose symbol from available feeds"
        return "CHAINLINK_SUPPORTED", feeds, ""

    def _persist_registry_rows(self, ch, decoded_rows: list[dict]) -> None:
        rows = []
        for row in decoded_rows:
            vault = normalize_address(row.get("vault_address"))
            if not vault:
                continue
            meta = self._registry.get(vault) or EulerVaultMetadata(vault_address=vault)
            rows.append(
                [
                    vault,
                    normalize_address(row.get("asset_address")) if row.get("asset_address") else meta.asset_address,
                    str(row.get("asset_symbol") or meta.asset_symbol or ""),
                    int(row.get("asset_decimals") or meta.asset_decimals or 18),
                    int(row.get("verified", int(meta.verified)) or 0),
                    str(row.get("source") or meta.source or "event"),
                    int(row.get("created_block") or meta.created_block or 0),
                    row.get("created_timestamp") or meta.created_timestamp,
                    str(row.get("name") or meta.name or ""),
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "euler_vault_registry",
                rows,
                [
                    "vault_address",
                    "asset_address",
                    "asset_symbol",
                    "asset_decimals",
                    "verified",
                    "source",
                    "created_block",
                    "created_timestamp",
                    "name",
                ],
            )

    def _persist_state(self, ch) -> None:
        if not self._touched_vaults:
            return
        rows = []
        for vault in sorted(self._touched_vaults):
            state = self._states.get(vault)
            if not state:
                continue
            rows.append(
                [
                    state.vault_address,
                    str(state.total_shares),
                    str(state.total_borrows),
                    str(state.accumulated_fees),
                    str(state.cash),
                    str(state.interest_accumulator),
                    str(state.interest_rate),
                    str(state.interest_fee),
                    int(state.last_event_block),
                    state.last_event_timestamp,
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "euler_vault_state",
                rows,
                [
                    "vault_address",
                    "total_shares",
                    "total_borrows",
                    "accumulated_fees",
                    "cash",
                    "interest_accumulator",
                    "interest_rate",
                    "interest_fee",
                    "last_event_block",
                    "last_event_timestamp",
                ],
            )

    def _persist_events(self, ch, decoded_rows: list[dict]) -> None:
        rows = []
        for row in decoded_rows:
            rows.append(
                [
                    int(row.get("block_number") or 0),
                    row.get("timestamp") or datetime.datetime(1970, 1, 1),
                    str(row.get("tx_hash") or ""),
                    int(row.get("log_index") or 0),
                    normalize_address(row.get("vault_address")),
                    str(row.get("event_name") or ""),
                    str(row.get("account") or ""),
                    str(row.get("sender") or ""),
                    str(row.get("receiver") or ""),
                    str(row.get("owner") or ""),
                    str(row.get("collateral") or ""),
                    str(row.get("assets") or "0"),
                    str(row.get("shares") or "0"),
                    str(row.get("repay_assets") or "0"),
                    str(row.get("yield_balance") or "0"),
                    str(row.get("raw_data") or ""),
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "euler_vault_events",
                rows,
                [
                    "block_number",
                    "timestamp",
                    "tx_hash",
                    "log_index",
                    "vault_address",
                    "event_name",
                    "account",
                    "sender",
                    "receiver",
                    "owner",
                    "collateral",
                    "assets",
                    "shares",
                    "repay_assets",
                    "yield_balance",
                    "raw_data",
                ],
            )

    def _persist_oracle_support(self, ch) -> None:
        rows = []
        for vault, meta in sorted(self._registry.items()):
            support, feeds, reason = self._support_for(meta)
            rows.append([vault, meta.asset_address, meta.asset_symbol, int(meta.verified), support, list(feeds), reason])
        if rows:
            insert_rows_batched(
                ch,
                "euler_vault_oracle_support",
                rows,
                ["vault_address", "asset_address", "asset_symbol", "verified", "oracle_support", "price_feeds", "reason"],
            )

    def _price_frame(self, ch, min_ts: datetime.datetime, max_ts: datetime.datetime, feeds: set[str]) -> pd.DataFrame:
        if not feeds:
            return pd.DataFrame()
        escaped = ", ".join("'" + feed.replace("'", "''") + "'" for feed in sorted(feeds))
        start = (pd.to_datetime(min_ts) - pd.Timedelta(days=1095)).strftime("%Y-%m-%d %H:%M:%S")
        end = pd.to_datetime(max_ts).strftime("%Y-%m-%d %H:%M:%S")
        df = ch.query_df(
            f"""
            SELECT toStartOfHour(timestamp) AS ts, feed, argMax(price, timestamp) AS price
            FROM chainlink_prices
            WHERE feed IN ({escaped}) AND timestamp >= '{start}' AND timestamp <= '{end}'
            GROUP BY ts, feed ORDER BY ts, feed
            """
        )
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot_table(index="ts", columns="feed", values="price", aggfunc="last").sort_index()
        return pivot.ffill()

    def _write_snapshots(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0
        df = pd.DataFrame(decoded_rows)
        if df.empty or "timestamp" not in df or "vault_address" not in df:
            return 0
        df["ts"] = pd.to_datetime(df["timestamp"]).dt.floor("h")
        df.sort_values(["block_number", "log_index", "vault_address"], inplace=True)
        hourly = df.groupby(["ts", "vault_address"], as_index=False).last()
        supported_vaults: set[str] = set()
        required_feeds: set[str] = set()
        for vault in hourly["vault_address"].unique():
            meta = self._registry.get(str(vault).lower())
            state = self._states.get(str(vault).lower())
            if not meta or not state:
                continue
            support, feeds, _reason = self._support_for(meta)
            if support == "CHAINLINK_SUPPORTED":
                supported_vaults.add(str(vault).lower())
                required_feeds.update(feeds)
        if not supported_vaults:
            return 0
        prices = self._price_frame(ch, hourly["ts"].min(), hourly["ts"].max(), required_feeds)
        if prices.empty:
            return 0
        metrics = []
        for row in hourly.itertuples(index=False):
            vault = str(row.vault_address).lower()
            if vault not in supported_vaults:
                continue
            meta = self._registry.get(vault)
            state = self._states.get(vault)
            if not meta or not state:
                continue
            ts = pd.to_datetime(row.ts)
            price_rows = prices.loc[prices.index <= ts]
            if price_rows.empty:
                continue
            feed_prices = {str(feed): float(value) for feed, value in price_rows.iloc[-1].dropna().items()}
            price = resolve_symbol_price(meta.asset_symbol, feed_prices)
            if price is None or price <= 0:
                continue
            total_assets_raw = max(0, int(state.cash) + int(state.total_borrows))
            total_borrows_raw = max(0, int(state.total_borrows))
            scale = float(10 ** int(meta.asset_decimals or 18))
            supply_tokens = total_assets_raw / scale
            borrow_tokens = total_borrows_raw / scale
            utilization = min(max(total_borrows_raw / total_assets_raw, 0.0), 1.0) if total_assets_raw > 0 else 0.0
            borrow_apy = spy_to_apy(state.interest_rate)
            fee = interest_fee_ratio(state.interest_fee)
            supply_apy = max(0.0, borrow_apy * utilization * (1.0 - fee))
            metrics.append(
                {
                    "timestamp": ts.to_pydatetime(),
                    "vault_address": vault,
                    "entity_id": vault,
                    "asset_symbol": meta.asset_symbol,
                    "asset_address": meta.asset_address,
                    "total_assets_raw": str(total_assets_raw),
                    "total_borrows_raw": str(total_borrows_raw),
                    "supply_usd": float(supply_tokens * price),
                    "borrow_usd": float(borrow_tokens * price),
                    "supply_apy": float(supply_apy),
                    "borrow_apy": float(borrow_apy),
                    "utilization": float(utilization),
                    "price_usd": float(price),
                    "interest_fee": float(fee),
                    "oracle_support": "CHAINLINK_SUPPORTED",
                }
            )
        if not metrics:
            return 0
        metrics_df = pd.DataFrame(metrics)
        insert_df_batched(ch, "euler_vault_metrics", metrics_df)
        final = pd.DataFrame(
            {
                "timestamp": metrics_df["timestamp"],
                "protocol": EULER_MARKET,
                "symbol": metrics_df["asset_symbol"],
                "entity_id": metrics_df["entity_id"],
                "target_id": metrics_df["asset_address"],
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
        final = forward_fill_hourly(final, ch, EULER_MARKET, compound=False)
        if final.empty:
            return 0
        min_ts_dt = pd.to_datetime(final["timestamp"].min())
        max_ts_dt = pd.to_datetime(final["timestamp"].max())
        min_ts = min_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        max_ts = max_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
        rewrite_protocol_window_if_enabled(ch, self.output_table, EULER_MARKET, min_ts, max_ts)
        insert_df_batched(ch, self.output_table, final)
        upsert_market_timeseries(ch, final)
        upsert_api_market_latest(ch, final)
        refresh_api_protocol_tvl_weekly(ch, min_ts_dt, max_ts_dt)
        return len(final)
