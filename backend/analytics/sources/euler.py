"""Euler V2 EVK verified vault analytics source.

The collector stores raw EVault logs for factory-created vaults while serving
only vaults verified by Euler's governedPerspective. VaultStatus is treated as
the authoritative event-sourced state for balances and rates.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Optional
from urllib import error, request

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
try:
    EULER_DIRECT_ORACLE_MIN_TVL_USD = float(os.getenv("EULER_DIRECT_ORACLE_MIN_TVL_USD", "100000") or "100000")
except ValueError:
    EULER_DIRECT_ORACLE_MIN_TVL_USD = 100000.0
EULER_USD_UNIT_OF_ACCOUNT = "0x0000000000000000000000000000000000000348"
EULER_ORACLE_UNIT_FEEDS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC / USD", 6),
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("ETH / USD", 18),
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("BTC / USD", 8),
}


def _topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


def _selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


TOPIC_EVAULT_CREATED = _topic("EVaultCreated(address,address,address)")
TOPIC_DEPOSIT = _topic("Deposit(address,address,uint256,uint256)")
TOPIC_WITHDRAW = _topic("Withdraw(address,address,address,uint256,uint256)")
TOPIC_BORROW = _topic("Borrow(address,uint256)")
TOPIC_REPAY = _topic("Repay(address,uint256)")
TOPIC_INTEREST_ACCRUED = _topic("InterestAccrued(address,uint256)")
TOPIC_LIQUIDATE = _topic("Liquidate(address,address,address,uint256,uint256)")
TOPIC_VAULT_STATUS = _topic("VaultStatus(uint256,uint256,uint256,uint256,uint256,uint256,uint256)")
TOPIC_GOV_SET_LTV = _topic("GovSetLTV(address,uint16,uint16,uint16,uint48,uint32)")
TOPIC_GOV_SET_INTEREST_FEE = _topic("GovSetInterestFee(uint16)")
TOPIC_GOV_SET_INTEREST_RATE_MODEL = _topic("GovSetInterestRateModel(address)")
TOPIC_GOV_SET_MAX_LIQUIDATION_DISCOUNT = _topic("GovSetMaxLiquidationDiscount(uint16)")
TOPIC_GOV_SET_LIQUIDATION_COOL_OFF_TIME = _topic("GovSetLiquidationCoolOffTime(uint16)")
TOPIC_GOV_SET_HOOK_CONFIG = _topic("GovSetHookConfig(address,uint32)")
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
    TOPIC_GOV_SET_LTV: "GovSetLTV",
    TOPIC_GOV_SET_INTEREST_FEE: "GovSetInterestFee",
    TOPIC_GOV_SET_INTEREST_RATE_MODEL: "GovSetInterestRateModel",
    TOPIC_GOV_SET_MAX_LIQUIDATION_DISCOUNT: "GovSetMaxLiquidationDiscount",
    TOPIC_GOV_SET_LIQUIDATION_COOL_OFF_TIME: "GovSetLiquidationCoolOffTime",
    TOPIC_GOV_SET_HOOK_CONFIG: "GovSetHookConfig",
    TOPIC_GOV_SET_CAPS: "GovSetCaps",
    TOPIC_GOV_SET_CONFIG_FLAGS: "GovSetConfigFlags",
}
EULER_VAULT_TOPICS = tuple(EVENT_MAP.keys())
EULER_STATE_TOPICS = (
    TOPIC_VAULT_STATUS,
    TOPIC_GOV_SET_LTV,
    TOPIC_GOV_SET_INTEREST_FEE,
    TOPIC_GOV_SET_INTEREST_RATE_MODEL,
    TOPIC_GOV_SET_MAX_LIQUIDATION_DISCOUNT,
    TOPIC_GOV_SET_LIQUIDATION_COOL_OFF_TIME,
    TOPIC_GOV_SET_HOOK_CONFIG,
    TOPIC_GOV_SET_CAPS,
    TOPIC_GOV_SET_CONFIG_FLAGS,
)
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


@dataclass
class EulerVaultMarketParams:
    vault_address: str
    block_number: int = 0
    timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)
    tx_hash: str = ""
    log_index: int = 0
    interest_rate_model: str = ""
    supply_cap_raw: int = 0
    borrow_cap_raw: int = 0
    config_flags: int = 0
    interest_fee: int = 1000
    max_liquidation_discount: int = 0
    liquidation_cool_off_time: int = 0
    hook_target: str = ""
    hooked_ops: int = 0
    oracle: str = ""
    unit_of_account: str = ""


@dataclass
class EulerVaultLTVConfig:
    vault_address: str
    collateral_address: str
    block_number: int = 0
    timestamp: datetime.datetime = datetime.datetime(1970, 1, 1)
    tx_hash: str = ""
    log_index: int = 0
    borrow_ltv: int = 0
    liquidation_ltv: int = 0
    initial_liquidation_ltv: int = 0
    target_timestamp: int = 0
    ramp_duration: int = 0


@dataclass
class EulerVaultPricePlan:
    source: str
    oracle_support: str
    feeds: tuple[str, ...] = ()
    direct_fallback: bool = False


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


def _encode_uint(value: int) -> str:
    return f"{int(value):064x}"


def _encode_address(value: str) -> str:
    return normalize_address(value).removeprefix("0x").rjust(64, "0")


def _decode_uint(data: str | None) -> int:
    raw = str(data or "").removeprefix("0x")
    if not raw:
        return 0
    return int(raw[:64], 16)


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
        self._params: dict[str, EulerVaultMarketParams] = {}
        self._ltv_configs: dict[tuple[str, str], EulerVaultLTVConfig] = {}
        self._available_feeds: set[str] = set()
        self._touched_vaults: set[str] = set()
        self._registry_updates: dict[str, EulerVaultMetadata] = {}
        self._param_updates: set[str] = set()
        self._ltv_updates: set[tuple[str, str]] = set()
        self._oracle_cache: dict[tuple[str, int], tuple[str, str]] = {}
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
            self._load_market_params(ch)
            self._load_ltv_configs(ch)
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
        facts = [row for row in decoded_rows if row.get("kind") in {"event", "snapshot", "config", "ltv"}]
        if facts:
            self._persist_events(ch, facts)
        self._persist_state(ch)
        self._persist_market_params(ch)
        self._persist_ltv_configs(ch)
        self._persist_oracle_support(ch)
        snapshots = [row for row in decoded_rows if row.get("kind") in {"snapshot", "config"}]
        written = self._write_snapshots(ch, snapshots)
        self._registry_updates.clear()
        self._touched_vaults.clear()
        self._param_updates.clear()
        self._ltv_updates.clear()
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
                "interest_fee": str(state.interest_fee),
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
        params = self._params_for(vault)
        self._stamp_params(params, log_entry, ts)
        params.interest_fee = previous.interest_fee
        self._param_updates.add(vault)
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
        elif event_name == "GovSetLTV":
            vault = normalize_address(log_entry.address)
            collateral = _topic_address(topics, 1)
            ltv = EulerVaultLTVConfig(
                vault_address=vault,
                collateral_address=collateral,
                block_number=int(log_entry.block_number),
                timestamp=ts,
                tx_hash=str(getattr(log_entry, "transaction_hash", "") or ""),
                log_index=int(getattr(log_entry, "log_index", 0) or 0),
                borrow_ltv=_uint(words, 0),
                liquidation_ltv=_uint(words, 1),
                initial_liquidation_ltv=_uint(words, 2),
                target_timestamp=_uint(words, 3),
                ramp_duration=_uint(words, 4),
            )
            self._ltv_configs[(vault, collateral)] = ltv
            self._ltv_updates.add((vault, collateral))
            row.update(
                {
                    "kind": "ltv",
                    "collateral": collateral,
                    "assets": str(ltv.borrow_ltv),
                    "shares": str(ltv.liquidation_ltv),
                    "repay_assets": str(ltv.initial_liquidation_ltv),
                    "yield_balance": str(ltv.target_timestamp),
                }
            )
        elif event_name == "GovSetInterestRateModel":
            model = _word_address(words[0]) if words else ""
            params = self._params_for(normalize_address(log_entry.address))
            self._stamp_params(params, log_entry, ts)
            params.interest_rate_model = model
            self._param_updates.add(params.vault_address)
            row.update({"receiver": model, "kind": "config"})
        elif event_name == "GovSetMaxLiquidationDiscount":
            params = self._params_for(normalize_address(log_entry.address))
            self._stamp_params(params, log_entry, ts)
            params.max_liquidation_discount = _uint(words, 0)
            self._param_updates.add(params.vault_address)
            row.update({"assets": str(params.max_liquidation_discount), "kind": "config"})
        elif event_name == "GovSetLiquidationCoolOffTime":
            params = self._params_for(normalize_address(log_entry.address))
            self._stamp_params(params, log_entry, ts)
            params.liquidation_cool_off_time = _uint(words, 0)
            self._param_updates.add(params.vault_address)
            row.update({"assets": str(params.liquidation_cool_off_time), "kind": "config"})
        elif event_name == "GovSetHookConfig":
            params = self._params_for(normalize_address(log_entry.address))
            self._stamp_params(params, log_entry, ts)
            params.hook_target = _topic_address(topics, 1)
            params.hooked_ops = _uint(words, 0)
            self._param_updates.add(params.vault_address)
            row.update({"receiver": params.hook_target, "assets": str(params.hooked_ops), "kind": "config"})
        elif event_name == "GovSetCaps":
            params = self._params_for(normalize_address(log_entry.address))
            self._stamp_params(params, log_entry, ts)
            params.supply_cap_raw = _uint(words, 0)
            params.borrow_cap_raw = _uint(words, 1)
            self._param_updates.add(params.vault_address)
            row.update({"assets": str(params.supply_cap_raw), "shares": str(params.borrow_cap_raw), "kind": "config"})
        elif event_name == "GovSetConfigFlags":
            params = self._params_for(normalize_address(log_entry.address))
            self._stamp_params(params, log_entry, ts)
            params.config_flags = _uint(words, 0)
            self._param_updates.add(params.vault_address)
            row.update({"assets": str(params.config_flags), "kind": "config"})
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

    def _params_for(self, vault: str) -> EulerVaultMarketParams:
        normalized = normalize_address(vault)
        params = self._params.get(normalized)
        if not params:
            params = EulerVaultMarketParams(vault_address=normalized)
            self._params[normalized] = params
        return params

    def _stamp_params(self, params: EulerVaultMarketParams, log_entry, ts: datetime.datetime) -> None:
        params.block_number = int(getattr(log_entry, "block_number", 0) or 0)
        params.timestamp = ts
        params.tx_hash = str(getattr(log_entry, "transaction_hash", "") or "")
        params.log_index = int(getattr(log_entry, "log_index", 0) or 0)

    def _load_market_params(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT vault_address, block_number, timestamp, tx_hash, log_index,
                       interest_rate_model, supply_cap_raw, borrow_cap_raw,
                       config_flags, interest_fee, max_liquidation_discount,
                       liquidation_cool_off_time, hook_target, hooked_ops,
                       oracle, unit_of_account
                FROM euler_vault_market_params FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            vault = normalize_address(row[0])
            self._params[vault] = EulerVaultMarketParams(
                vault_address=vault,
                block_number=int(row[1] or 0),
                timestamp=row[2] or datetime.datetime(1970, 1, 1),
                tx_hash=str(row[3] or ""),
                log_index=int(row[4] or 0),
                interest_rate_model=normalize_address(row[5]) if row[5] else "",
                supply_cap_raw=int(row[6] or 0),
                borrow_cap_raw=int(row[7] or 0),
                config_flags=int(row[8] or 0),
                interest_fee=int(row[9] or 0),
                max_liquidation_discount=int(row[10] or 0),
                liquidation_cool_off_time=int(row[11] or 0),
                hook_target=normalize_address(row[12]) if row[12] else "",
                hooked_ops=int(row[13] or 0),
                oracle=normalize_address(row[14]) if row[14] else "",
                unit_of_account=normalize_address(row[15]) if row[15] else "",
            )

    def _load_ltv_configs(self, ch) -> None:
        try:
            rows = ch.query(
                """
                SELECT vault_address, collateral_address, block_number, timestamp, tx_hash,
                       log_index, borrow_ltv, liquidation_ltv, initial_liquidation_ltv,
                       target_timestamp, ramp_duration
                FROM euler_vault_ltv_config FINAL
                """
            ).result_rows
        except Exception:
            rows = []
        for row in rows:
            vault = normalize_address(row[0])
            collateral = normalize_address(row[1])
            self._ltv_configs[(vault, collateral)] = EulerVaultLTVConfig(
                vault_address=vault,
                collateral_address=collateral,
                block_number=int(row[2] or 0),
                timestamp=row[3] or datetime.datetime(1970, 1, 1),
                tx_hash=str(row[4] or ""),
                log_index=int(row[5] or 0),
                borrow_ltv=int(row[6] or 0),
                liquidation_ltv=int(row[7] or 0),
                initial_liquidation_ltv=int(row[8] or 0),
                target_timestamp=int(row[9] or 0),
                ramp_duration=int(row[10] or 0),
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

    def _persist_market_params(self, ch) -> None:
        if not self._param_updates:
            return
        rows = []
        for vault in sorted(self._param_updates):
            params = self._params.get(vault)
            if not params:
                continue
            rows.append(
                [
                    params.vault_address,
                    int(params.block_number),
                    params.timestamp,
                    params.tx_hash,
                    int(params.log_index),
                    params.interest_rate_model,
                    str(params.supply_cap_raw),
                    str(params.borrow_cap_raw),
                    int(params.config_flags),
                    int(params.interest_fee),
                    int(params.max_liquidation_discount),
                    int(params.liquidation_cool_off_time),
                    params.hook_target,
                    int(params.hooked_ops),
                    params.oracle,
                    params.unit_of_account,
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "euler_vault_market_params",
                rows,
                [
                    "vault_address",
                    "block_number",
                    "timestamp",
                    "tx_hash",
                    "log_index",
                    "interest_rate_model",
                    "supply_cap_raw",
                    "borrow_cap_raw",
                    "config_flags",
                    "interest_fee",
                    "max_liquidation_discount",
                    "liquidation_cool_off_time",
                    "hook_target",
                    "hooked_ops",
                    "oracle",
                    "unit_of_account",
                ],
            )

    def _persist_ltv_configs(self, ch) -> None:
        if not self._ltv_updates:
            return
        rows = []
        for key in sorted(self._ltv_updates):
            ltv = self._ltv_configs.get(key)
            if not ltv:
                continue
            rows.append(
                [
                    ltv.vault_address,
                    ltv.collateral_address,
                    int(ltv.block_number),
                    ltv.timestamp,
                    ltv.tx_hash,
                    int(ltv.log_index),
                    int(ltv.borrow_ltv),
                    int(ltv.liquidation_ltv),
                    int(ltv.initial_liquidation_ltv),
                    int(ltv.target_timestamp),
                    int(ltv.ramp_duration),
                ]
            )
        if rows:
            insert_rows_batched(
                ch,
                "euler_vault_ltv_config",
                rows,
                [
                    "vault_address",
                    "collateral_address",
                    "block_number",
                    "timestamp",
                    "tx_hash",
                    "log_index",
                    "borrow_ltv",
                    "liquidation_ltv",
                    "initial_liquidation_ltv",
                    "target_timestamp",
                    "ramp_duration",
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

    def _asset_history_frame(
        self,
        ch,
        min_ts: datetime.datetime,
        max_ts: datetime.datetime,
        table: str,
        address_column: str,
        price_expression: str,
        where_expression: str,
        assets: set[str],
    ) -> pd.DataFrame:
        if not assets:
            return pd.DataFrame()
        escaped = ", ".join("'" + asset.replace("'", "''") + "'" for asset in sorted(assets))
        start = (pd.to_datetime(min_ts) - pd.Timedelta(days=1095)).strftime("%Y-%m-%d %H:%M:%S")
        end = pd.to_datetime(max_ts).strftime("%Y-%m-%d %H:%M:%S")
        try:
            df = ch.query_df(
                f"""
                SELECT toStartOfHour(timestamp) AS ts,
                       lower({address_column}) AS asset_address,
                       argMax({price_expression}, timestamp) AS price
                FROM {table}
                WHERE lower({address_column}) IN ({escaped})
                  AND timestamp >= '{start}' AND timestamp <= '{end}'
                  AND {where_expression}
                GROUP BY ts, asset_address ORDER BY ts, asset_address
                """
            )
        except Exception as exc:
            log.debug("Euler %s price history lookup failed: %s", table, exc)
            return pd.DataFrame()
        if df.empty:
            return pd.DataFrame()
        pivot = df.pivot_table(index="ts", columns="asset_address", values="price", aggfunc="last").sort_index()
        return pivot.ffill()

    def _history_source_assets(self, ch, assets: set[str]) -> dict[str, str]:
        if not assets:
            return {}
        escaped = ", ".join("'" + asset.replace("'", "''") + "'" for asset in sorted(assets))
        sources: dict[str, str] = {}
        for source, table, address_column, where_expression in (
            ("PENDLE_OHLCV", "pendle_eth_price_ohlcv", "asset_address", "close > 0"),
            ("FLUID_COMPONENT", "fluid_product_components", "token", "price_usd > 0"),
        ):
            try:
                rows = ch.query(
                    f"""
                    SELECT lower({address_column})
                    FROM {table}
                    WHERE lower({address_column}) IN ({escaped}) AND {where_expression}
                    GROUP BY lower({address_column})
                    """
                ).result_rows
            except Exception as exc:
                log.debug("Euler %s source lookup failed: %s", table, exc)
                continue
            for row in rows:
                sources.setdefault(str(row[0]).lower(), source)
        return sources

    def _price_at(self, frame: pd.DataFrame, key: str, ts: pd.Timestamp) -> Optional[float]:
        if frame.empty or key not in frame.columns:
            return None
        rows = frame.loc[frame.index <= ts]
        if rows.empty:
            return None
        value = rows.iloc[-1][key]
        if pd.isna(value):
            return None
        price = float(value)
        return price if price > 0 else None

    def _unit_feed_prices_at(self, unit_prices: pd.DataFrame, ts: pd.Timestamp) -> dict[str, float]:
        prices = {}
        if unit_prices.empty:
            return prices
        rows = unit_prices.loc[unit_prices.index <= ts]
        if rows.empty:
            return prices
        for feed, value in rows.iloc[-1].dropna().items():
            prices[str(feed)] = float(value)
        return prices

    def _rpc_url(self) -> str:
        return os.getenv("MAINNET_RPC_URL") or os.getenv("ETHEREUM_RPC_URL") or ""

    def _eth_call(self, to_address: str, data: str, block_number: int = 0) -> str:
        rpc_url = self._rpc_url()
        if not rpc_url:
            return ""
        block_tag = hex(int(block_number)) if block_number and int(block_number) > 0 else "latest"
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": normalize_address(to_address), "data": data}, block_tag]}
        ).encode("utf-8")
        req = request.Request(rpc_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=20) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (OSError, error.URLError, json.JSONDecodeError) as exc:
            log.debug("Euler oracle RPC call failed: %s", exc)
            return ""
        if body.get("error"):
            return ""
        return str(body.get("result") or "")

    def _oracle_unit_for(self, vault: str, block_number: int = 0) -> tuple[str, str]:
        key = (normalize_address(vault), int(block_number or 0))
        cached = self._oracle_cache.get(key)
        if cached is not None:
            return cached
        oracle_data = _selector("oracle()")
        unit_data = _selector("unitOfAccount()")
        oracle = _word_address(self._eth_call(vault, oracle_data, block_number))
        unit = _word_address(self._eth_call(vault, unit_data, block_number))
        if oracle == ZERO_ADDRESS:
            oracle = ""
        if unit == ZERO_ADDRESS:
            unit = ""
        self._oracle_cache[key] = (oracle, unit)
        return oracle, unit

    def _quote_to_usd(self, quote_raw: int, unit: str, unit_feed_prices: dict[str, float]) -> Optional[float]:
        unit = normalize_address(unit)
        if quote_raw <= 0:
            return None
        if unit == EULER_USD_UNIT_OF_ACCOUNT:
            return float(quote_raw / 1e18)
        unit_feed = EULER_ORACLE_UNIT_FEEDS.get(unit)
        if not unit_feed:
            return None
        feed, decimals = unit_feed
        unit_price = float(unit_feed_prices.get(feed) or 0.0)
        if unit_price <= 0:
            return None
        return float((quote_raw / float(10**decimals)) * unit_price)

    def _direct_oracle_price_usd(
        self,
        meta: EulerVaultMetadata,
        block_number: int,
        unit_feed_prices: dict[str, float],
    ) -> Optional[float]:
        oracle, unit = self._oracle_unit_for(meta.vault_address, block_number)
        if not oracle or not unit:
            return None
        amount = 10 ** int(meta.asset_decimals or 18)
        data = _selector("getQuote(uint256,address,address)") + _encode_uint(amount) + _encode_address(meta.asset_address) + _encode_address(unit)
        quote_raw = _decode_uint(self._eth_call(oracle, data, block_number))
        return self._quote_to_usd(quote_raw, unit, unit_feed_prices)

    def _current_direct_oracle_tvl_usd(self, meta: EulerVaultMetadata, unit_feed_prices: dict[str, float]) -> float:
        state = self._states.get(meta.vault_address)
        if not state:
            return 0.0
        price = self._direct_oracle_price_usd(meta, 0, unit_feed_prices)
        if price is None or price <= 0:
            return 0.0
        total_assets_raw = max(0, int(state.cash or 0) + int(state.total_borrows or 0))
        scale = float(10 ** int(meta.asset_decimals or 18))
        return float((total_assets_raw / scale) * price)

    def _latest_unit_feed_prices(self, ch) -> dict[str, float]:
        feeds = sorted({feed for feed, _decimals in EULER_ORACLE_UNIT_FEEDS.values()})
        escaped = ", ".join("'" + feed.replace("'", "''") + "'" for feed in feeds)
        try:
            rows = ch.query(
                f"""
                SELECT feed, argMax(price, timestamp)
                FROM chainlink_prices
                WHERE feed IN ({escaped})
                GROUP BY feed
                """
            ).result_rows
        except Exception as exc:
            log.debug("Euler unit feed latest lookup failed: %s", exc)
            return {}
        return {str(feed): float(price) for feed, price in rows if float(price or 0) > 0}

    def _price_plans(self, ch, vaults: set[str]) -> dict[str, EulerVaultPricePlan]:
        assets = {self._registry[vault].asset_address for vault in vaults if vault in self._registry and self._registry[vault].asset_address}
        history_sources = self._history_source_assets(ch, assets)
        latest_unit_prices = self._latest_unit_feed_prices(ch)
        plans: dict[str, EulerVaultPricePlan] = {}
        for vault in sorted(vaults):
            meta = self._registry.get(vault)
            if not meta:
                continue
            support, feeds, _reason = self._support_for(meta)
            asset_source = history_sources.get(meta.asset_address.lower())
            if support == "CHAINLINK_SUPPORTED":
                plans[vault] = EulerVaultPricePlan("CHAINLINK", "CHAINLINK_SUPPORTED", tuple(feeds))
                continue
            direct_tvl = self._current_direct_oracle_tvl_usd(meta, latest_unit_prices)
            direct_fallback = direct_tvl >= EULER_DIRECT_ORACLE_MIN_TVL_USD
            if asset_source == "PENDLE_OHLCV":
                plans[vault] = EulerVaultPricePlan("PENDLE_OHLCV", "PENDLE_OHLCV", direct_fallback=direct_fallback)
            elif asset_source == "FLUID_COMPONENT":
                plans[vault] = EulerVaultPricePlan("FLUID_COMPONENT", "FLUID_COMPONENT", direct_fallback=direct_fallback)
            elif direct_fallback:
                plans[vault] = EulerVaultPricePlan("EULER_ORACLE_RPC", "EULER_ORACLE_RPC")
        return plans

    def _write_snapshots(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0
        df = pd.DataFrame(decoded_rows)
        if df.empty or "timestamp" not in df or "vault_address" not in df or "event_name" not in df:
            return 0
        df = df[df["event_name"] == "VaultStatus"].copy()
        if df.empty:
            return 0
        df["ts"] = pd.to_datetime(df["timestamp"]).dt.floor("h")
        df.sort_values(["block_number", "log_index", "vault_address"], inplace=True)
        hourly = df.groupby(["ts", "vault_address"], as_index=False).last()
        vaults = {str(vault).lower() for vault in hourly["vault_address"].unique()}
        plans = self._price_plans(ch, vaults)
        if not plans:
            return 0
        required_feeds = {feed for plan in plans.values() if plan.source == "CHAINLINK" for feed in plan.feeds}
        prices = self._price_frame(ch, hourly["ts"].min(), hourly["ts"].max(), required_feeds)
        asset_addresses = {self._registry[vault].asset_address.lower() for vault in plans if vault in self._registry}
        pendle_prices = self._asset_history_frame(
            ch,
            hourly["ts"].min(),
            hourly["ts"].max(),
            "pendle_eth_price_ohlcv",
            "asset_address",
            "close",
            "close > 0",
            asset_addresses,
        )
        fluid_prices = self._asset_history_frame(
            ch,
            hourly["ts"].min(),
            hourly["ts"].max(),
            "fluid_product_components",
            "token",
            "price_usd",
            "price_usd > 0",
            asset_addresses,
        )
        unit_prices = self._price_frame(
            ch,
            hourly["ts"].min(),
            hourly["ts"].max(),
            {feed for feed, _decimals in EULER_ORACLE_UNIT_FEEDS.values()},
        )
        metrics = []
        for row in hourly.itertuples(index=False):
            vault = str(row.vault_address).lower()
            plan = plans.get(vault)
            if not plan:
                continue
            meta = self._registry.get(vault)
            if not meta:
                continue
            ts = pd.to_datetime(row.ts)
            price: Optional[float] = None
            if plan.source == "CHAINLINK":
                price_rows = prices.loc[prices.index <= ts] if not prices.empty else pd.DataFrame()
                if not price_rows.empty:
                    feed_prices = {str(feed): float(value) for feed, value in price_rows.iloc[-1].dropna().items()}
                    price = resolve_symbol_price(meta.asset_symbol, feed_prices)
            elif plan.source == "PENDLE_OHLCV":
                price = self._price_at(pendle_prices, meta.asset_address.lower(), ts)
            elif plan.source == "FLUID_COMPONENT":
                price = self._price_at(fluid_prices, meta.asset_address.lower(), ts)
            elif plan.source == "EULER_ORACLE_RPC":
                unit_feed_prices = self._unit_feed_prices_at(unit_prices, ts)
                price = self._direct_oracle_price_usd(meta, int(getattr(row, "block_number", 0) or 0), unit_feed_prices)
            if price is None or price <= 0:
                if not plan.direct_fallback:
                    continue
                unit_feed_prices = self._unit_feed_prices_at(unit_prices, ts)
                price = self._direct_oracle_price_usd(meta, int(getattr(row, "block_number", 0) or 0), unit_feed_prices)
                if price is None or price <= 0:
                    continue
                oracle_support = "EULER_ORACLE_RPC"
            else:
                oracle_support = plan.oracle_support
            total_borrows_raw = max(0, int(getattr(row, "total_borrows", 0) or 0))
            total_assets_raw = max(0, int(getattr(row, "cash", 0) or 0) + total_borrows_raw)
            scale = float(10 ** int(meta.asset_decimals or 18))
            supply_tokens = total_assets_raw / scale
            borrow_tokens = total_borrows_raw / scale
            utilization = min(max(total_borrows_raw / total_assets_raw, 0.0), 1.0) if total_assets_raw > 0 else 0.0
            borrow_apy = spy_to_apy(int(getattr(row, "interest_rate", 0) or 0))
            fee = interest_fee_ratio(int(getattr(row, "interest_fee", 1000) or 1000))
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
                    "oracle_support": oracle_support,
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
