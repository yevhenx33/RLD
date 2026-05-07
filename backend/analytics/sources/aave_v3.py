"""
AaveV3Source — Aave V3 ReserveDataUpdated event decoder.

Decodes per-reserve rate updates from the Aave V3 Pool contract.
Each ReserveDataUpdated event carries the full rate state (supply APY,
borrow APY, liquidity index, variable borrow index) in RAY format (1e27).

It also tracks Supply, Withdraw, Borrow, Repay, and LiquidationCall 
events to precisely reconstruct native token physics tracking base principal,
combining it with Reserve indices to calculate true accounting TVL organically.
"""

import datetime
import logging
import os
from dataclasses import dataclass
from typing import Optional

from eth_abi import decode as abi_decode
import pandas as pd

from ..base import (
    BaseSource,
    forward_fill_hourly,
    insert_df_batched,
    upsert_market_timeseries,
    upsert_api_market_latest,
    refresh_api_protocol_tvl_weekly,
    rewrite_protocol_window_if_enabled,
)
from ..aave_constants import (
    AAVE_V3_POOL,
    AAVE_V3_POOL_CONFIGURATOR,
    AAVE_V3_GENESIS_ANCHOR_BLOCK,
    AAVE_TOPIC_RESERVE_DATA_UPDATED,
    AAVE_TOPIC_SUPPLY,
    AAVE_TOPIC_WITHDRAW,
    AAVE_TOPIC_BORROW,
    AAVE_TOPIC_REPAY,
    AAVE_TOPIC_LIQUIDATION_CALL,
    AAVE_TOPIC_MINTED_TO_TREASURY,
    AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED,
    AAVE_TOPIC_COLLATERAL_CONFIGURATION_CHANGED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED_UINT256,
    AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED,
)
from ..tokens import (TOKENS as RESERVE_MAP, get_chainlink_prices, get_usd_price)

log = logging.getLogger("indexer.aave_v3")

AAVE_POOL = AAVE_V3_POOL
AAVE_POOL_CONFIGURATOR = AAVE_V3_POOL_CONFIGURATOR

TOPIC_RESERVE_DATA_UPDATED = AAVE_TOPIC_RESERVE_DATA_UPDATED
TOPIC_SUPPLY = AAVE_TOPIC_SUPPLY
TOPIC_WITHDRAW = AAVE_TOPIC_WITHDRAW
TOPIC_BORROW = AAVE_TOPIC_BORROW
TOPIC_REPAY = AAVE_TOPIC_REPAY
TOPIC_LIQUIDATION_CALL = AAVE_TOPIC_LIQUIDATION_CALL
TOPIC_MINTED_TO_TREASURY = AAVE_TOPIC_MINTED_TO_TREASURY
TOPIC_RESERVE_CONFIGURATION_CHANGED = AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED
TOPIC_COLLATERAL_CONFIGURATION_CHANGED = AAVE_TOPIC_COLLATERAL_CONFIGURATION_CHANGED
TOPIC_EMODE_CATEGORY_ADDED = AAVE_TOPIC_EMODE_CATEGORY_ADDED
TOPIC_EMODE_CATEGORY_ADDED_UINT256 = AAVE_TOPIC_EMODE_CATEGORY_ADDED_UINT256
TOPIC_EMODE_ASSET_CATEGORY_CHANGED = AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED

RAY = 10**27
BPS = 10_000

EVENT_MAP = {
    TOPIC_RESERVE_DATA_UPDATED: "ReserveDataUpdated",
    TOPIC_SUPPLY: "Supply",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_BORROW: "Borrow",
    TOPIC_REPAY: "Repay",
    TOPIC_LIQUIDATION_CALL: "LiquidationCall",
    TOPIC_MINTED_TO_TREASURY: "MintedToTreasury",
    TOPIC_RESERVE_CONFIGURATION_CHANGED: "ReserveConfigurationChanged",
    TOPIC_COLLATERAL_CONFIGURATION_CHANGED: "CollateralConfigurationChanged",
    TOPIC_EMODE_CATEGORY_ADDED: "EModeCategoryAdded",
    TOPIC_EMODE_CATEGORY_ADDED_UINT256: "EModeCategoryAdded",
    TOPIC_EMODE_ASSET_CATEGORY_CHANGED: "EModeAssetCategoryChanged",
}

@dataclass
class AaveEModeCategory:
    ltv: float = 0.0
    liquidation_threshold: float = 0.0
    liquidation_penalty: float = 0.0
    price_source: str = ""
    label: str = ""


@dataclass
class AaveReserveState:
    total_scaled_supply: float = 0.0
    total_scaled_borrow: float = 0.0
    liquidity_index: float = 1e27
    variable_borrow_index: float = 1e27
    ltv: float = 0.0
    liquidation_threshold: float = 0.0
    liquidation_penalty: float = 0.0
    e_mode_category: int = 0

# ─── Genesis Anchor ───────────────────────────────────────────────────
# On-chain scaledTotalSupply and scaledTotalVariableDebt at block 17,700,000.
# This seeds the accumulator to account for V2→V3 migration debt that was
# created via direct variableDebtToken.mint() without emitting Borrow events.
# Values obtained via eth_call to aToken.scaledTotalSupply() and
# variableDebtToken.scaledTotalSupply() at the anchor block.
GENESIS_ANCHOR_BLOCK = AAVE_V3_GENESIS_ANCHOR_BLOCK
GENESIS_SEEDS = {
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": (307376798754970392718879, 167045603813717742595917),    # WETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": (262854201852641, 227564742571021),                    # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": (156933242326229, 122942336945879),                       # USDT
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": (695551167972, 90478052718),                             # WBTC
    "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": (380495417011617437752159, 7051050913738286218974),      # wstETH
}

SYMBOL_TO_DEC = {sym: dec for sym, dec in RESERVE_MAP.values()}


def _topic_address(topic: str) -> str:
    return "0x" + str(topic or "")[-40:].lower()


def _topic_uint(topic: str) -> int:
    return int(str(topic or "0x0"), 16)


def _data_words(data: str) -> list[int]:
    raw = str(data or "")
    if raw.startswith("0x"):
        raw = raw[2:]
    return [int(raw[i:i + 64], 16) for i in range(0, len(raw), 64) if len(raw[i:i + 64]) == 64]


def _bps(value: int | float) -> float:
    return float(value) / BPS


def _liquidation_penalty(liquidation_bonus_bps: int | float) -> float:
    return max(0.0, _bps(liquidation_bonus_bps) - 1.0)

class AaveV3Source(BaseSource):
    name = "AAVE_MARKET"
    contracts = [AAVE_POOL, AAVE_POOL_CONFIGURATOR]
    topics = list(EVENT_MAP.keys())
    raw_table = "aave_events"
    genesis_block = GENESIS_ANCHOR_BLOCK

    def __init__(self):
        self._reserves: dict[str, AaveReserveState] = {}
        self._emode_categories: dict[int, AaveEModeCategory] = {}
        self._unknown_reserves: set[str] = set()
        self._strict_reserve_coverage = (
            os.getenv("AAVE_STRICT_RESERVE_COVERAGE", "false").strip().lower()
            in {"1", "true", "yes"}
        )
        # Seed accumulators from on-chain anchor
        for eid, (sup_seed, bor_seed) in GENESIS_SEEDS.items():
            self._reserves[eid] = AaveReserveState(
                total_scaled_supply=float(sup_seed),
                total_scaled_borrow=float(bor_seed),
            )
        self._initialized = False

    def get_cursor(self, ch) -> int:
        if not self._initialized:
            ch.command("""
            CREATE TABLE IF NOT EXISTS aave_scaled_state (
                entity_id String,
                total_scaled_supply Float64,
                total_scaled_borrow Float64,
                liquidity_index Float64,
                variable_borrow_index Float64,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY entity_id
            """)
            ch.command("""
            CREATE TABLE IF NOT EXISTS aave_reserve_risk_state (
                entity_id String,
                ltv Float64 DEFAULT 0,
                liquidation_threshold Float64 DEFAULT 0,
                liquidation_penalty Float64 DEFAULT 0,
                e_mode_category UInt8 DEFAULT 0,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY entity_id
            """)
            ch.command("""
            CREATE TABLE IF NOT EXISTS aave_emode_categories (
                category_id UInt8,
                ltv Float64 DEFAULT 0,
                liquidation_threshold Float64 DEFAULT 0,
                liquidation_penalty Float64 DEFAULT 0,
                price_source String DEFAULT '',
                label String DEFAULT '',
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY category_id
            """)
            ch.command("""
            CREATE TABLE IF NOT EXISTS aave_timeseries (
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
                ltv Float64 DEFAULT 0,
                liquidation_threshold Float64 DEFAULT 0,
                liquidation_penalty Float64 DEFAULT 0,
                e_mode_category UInt8 DEFAULT 0,
                e_mode_ltv Float64 DEFAULT 0,
                e_mode_liquidation_threshold Float64 DEFAULT 0,
                e_mode_liquidation_penalty Float64 DEFAULT 0,
                e_mode_label String DEFAULT '',
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (protocol, entity_id, timestamp)
            TTL timestamp + INTERVAL 36 MONTH DELETE
            """)
            for table in ("aave_timeseries", "market_timeseries", "api_market_latest"):
                for column, column_type in (
                    ("ltv", "Float64 DEFAULT 0"),
                    ("liquidation_threshold", "Float64 DEFAULT 0"),
                    ("liquidation_penalty", "Float64 DEFAULT 0"),
                    ("e_mode_category", "UInt8 DEFAULT 0"),
                    ("e_mode_ltv", "Float64 DEFAULT 0"),
                    ("e_mode_liquidation_threshold", "Float64 DEFAULT 0"),
                    ("e_mode_liquidation_penalty", "Float64 DEFAULT 0"),
                    ("e_mode_label", "String DEFAULT ''"),
                ):
                    ch.command(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}")
            
            try:
                res = ch.query_df("SELECT entity_id, argMax(total_scaled_supply, updated_at) AS sup, argMax(total_scaled_borrow, updated_at) AS bor, argMax(liquidity_index, updated_at) AS li, argMax(variable_borrow_index, updated_at) AS vbi FROM aave_scaled_state GROUP BY entity_id")
                if not res.empty:
                    for _, row in res.iterrows():
                        self._reserves[row['entity_id']] = AaveReserveState(
                            total_scaled_supply=row['sup'],
                            total_scaled_borrow=row['bor'],
                            liquidity_index=row['li'],
                            variable_borrow_index=row['vbi']
                        )
                risk = ch.query_df("SELECT entity_id, argMax(ltv, updated_at) AS ltv, argMax(liquidation_threshold, updated_at) AS lt, argMax(liquidation_penalty, updated_at) AS penalty, argMax(e_mode_category, updated_at) AS emode FROM aave_reserve_risk_state GROUP BY entity_id")
                if not risk.empty:
                    for _, row in risk.iterrows():
                        eid = str(row["entity_id"])
                        state = self._reserves.setdefault(eid, AaveReserveState())
                        state.ltv = float(row["ltv"] or 0.0)
                        state.liquidation_threshold = float(row["lt"] or 0.0)
                        state.liquidation_penalty = float(row["penalty"] or 0.0)
                        state.e_mode_category = int(row["emode"] or 0)
                emode = ch.query_df("SELECT category_id, argMax(ltv, updated_at) AS ltv, argMax(liquidation_threshold, updated_at) AS lt, argMax(liquidation_penalty, updated_at) AS penalty, argMax(price_source, updated_at) AS price_source, argMax(label, updated_at) AS label FROM aave_emode_categories GROUP BY category_id")
                if not emode.empty:
                    for _, row in emode.iterrows():
                        self._emode_categories[int(row["category_id"] or 0)] = AaveEModeCategory(
                            ltv=float(row["ltv"] or 0.0),
                            liquidation_threshold=float(row["lt"] or 0.0),
                            liquidation_penalty=float(row["penalty"] or 0.0),
                            price_source=str(row["price_source"] or ""),
                            label=str(row["label"] or ""),
                        )
                log.info(f"[AAVE_MARKET] State Initialized: Rehydrated {len(self._reserves)} reserves and {len(self._emode_categories)} E-Mode categories from persistence layer.")
            except Exception as e:
                log.error(f"[AAVE_MARKET] Failed to load state: {e}")
                
            self._initialized = True

        result = ch.command("SELECT max(block_number) FROM aave_events")
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        topics = log_entry.topics or []
        if not topics:
            return ""
        return EVENT_MAP.get(topics[0], "")


    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        topics = log_entry.topics or []
        data = log_entry.data
        if not topics or not data:
            return None

        evt = self._event_name(log_entry)
        if not evt:
            return None

        if evt == "EModeCategoryAdded":
            if len(topics) < 2:
                return None
            raw = data[2:] if str(data).startswith("0x") else str(data)
            number_type = "uint16" if topics[0] == TOPIC_EMODE_CATEGORY_ADDED else "uint256"
            try:
                ltv, threshold, bonus, price_source, label = abi_decode(
                    [number_type, number_type, number_type, "address", "string"],
                    bytes.fromhex(raw),
                )
            except Exception as exc:
                log.warning("[AAVE_MARKET] Failed to decode EModeCategoryAdded: %s", exc)
                return None
            category_id = _topic_uint(topics[1])
            self._emode_categories[category_id] = AaveEModeCategory(
                ltv=_bps(int(ltv)),
                liquidation_threshold=_bps(int(threshold)),
                liquidation_penalty=_liquidation_penalty(int(bonus)),
                price_source=str(price_source).lower(),
                label=str(label or ""),
            )
            return None

        if evt in {"ReserveConfigurationChanged", "CollateralConfigurationChanged"}:
            if len(topics) < 2:
                return None
            eid = _topic_address(topics[1])
            reserve_addr = eid[2:]
            if reserve_addr not in RESERVE_MAP:
                return None
            if eid not in self._reserves:
                self._reserves[eid] = AaveReserveState()
            words = _data_words(data)
            if len(words) < 3:
                return None
            state = self._reserves[eid]
            state.ltv = _bps(words[0])
            state.liquidation_threshold = _bps(words[1])
            state.liquidation_penalty = _liquidation_penalty(words[2])
            return None

        if evt == "EModeAssetCategoryChanged":
            if len(topics) < 2:
                return None
            eid = _topic_address(topics[1])
            reserve_addr = eid[2:]
            if reserve_addr not in RESERVE_MAP:
                return None
            if eid not in self._reserves:
                self._reserves[eid] = AaveReserveState()
            words = _data_words(data)
            if len(words) < 2:
                return None
            self._reserves[eid].e_mode_category = int(words[1])
            return None

        # Reserve address is typically topic1 unless LiquidationCall (where it's collateral in topic1 and debt in topic2)
        if evt == "LiquidationCall":
            if len(topics) < 3:
                return None
            collateral_addr = "0x" + topics[1][26:].lower()
            debt_addr = "0x" + topics[2][26:].lower()
            
            raw = data[2:]
            if len(raw) >= 128:
                debt_covered = int(raw[0:64], 16)
                liquidated_collateral = int(raw[64:128], 16)
                
                if debt_addr not in self._reserves:
                    self._reserves[debt_addr] = AaveReserveState()
                d_state = self._reserves[debt_addr]
                d_state.total_scaled_borrow -= debt_covered / (d_state.variable_borrow_index / RAY)

                if collateral_addr not in self._reserves:
                    self._reserves[collateral_addr] = AaveReserveState()
                c_state = self._reserves[collateral_addr]
                c_state.total_scaled_supply -= liquidated_collateral / (c_state.liquidity_index / RAY)
            return None

        # Standard reserve interactions
        if len(topics) < 2:
            return None
        reserve_addr = topics[1][26:].lower()
        eid = "0x" + reserve_addr

        if reserve_addr not in RESERVE_MAP:
            if reserve_addr not in self._unknown_reserves:
                self._unknown_reserves.add(reserve_addr)
                msg = (
                    f"[AAVE_MARKET] Unknown reserve detected: {eid}. "
                    "Add this asset to indexer/tokens.py to prevent silent coverage gaps."
                )
                if self._strict_reserve_coverage:
                    raise RuntimeError(msg)
                log.warning(msg)
            return None

        if eid not in self._reserves:
            self._reserves[eid] = AaveReserveState()
        state = self._reserves[eid]
        
        raw = data[2:]

        if evt == "Supply" and len(raw) >= 128:
            # user(data0), amount(data1)
            amount = int(raw[64:128], 16)
            state.total_scaled_supply += amount / (state.liquidity_index / RAY)
        
        elif evt == "Withdraw" and len(raw) >= 64:
            # amount(data0)
            amount = int(raw[0:64], 16)
            state.total_scaled_supply -= amount / (state.liquidity_index / RAY)
            
        elif evt == "Borrow" and len(raw) >= 128:
            # user(data0), amount(data1)
            amount = int(raw[64:128], 16)
            state.total_scaled_borrow += amount / (state.variable_borrow_index / RAY)
            
        elif evt == "Repay" and len(raw) >= 64:
            # amount(data0), useATokens(data1)
            amount = int(raw[0:64], 16)
            state.total_scaled_borrow -= amount / (state.variable_borrow_index / RAY)
            # When useATokens=true, aTokens are burned to cover the repayment,
            # which also reduces supply. Without this, we accumulate ~10% drift.
            if len(raw) >= 128:
                use_a_tokens = int(raw[64:128], 16)
                if use_a_tokens == 1:
                    state.total_scaled_supply -= amount / (state.liquidity_index / RAY)
                    
        elif evt == "MintedToTreasury" and len(raw) >= 64:
            amount = int(raw[0:64], 16)
            state.total_scaled_supply += amount / (state.liquidity_index / RAY)

        elif evt == "ReserveDataUpdated" and len(raw) >= 320:
            liquidity_rate = int(raw[0:64], 16)
            variable_borrow_rate = int(raw[128:192], 16)
            liquidity_index = int(raw[192:256], 16)
            variable_borrow_index = int(raw[256:320], 16)

            state.liquidity_index = liquidity_index
            state.variable_borrow_index = variable_borrow_index

            supply_apy = liquidity_rate / RAY
            borrow_apy = variable_borrow_rate / RAY
            utilization = supply_apy / borrow_apy if borrow_apy > 0 else 0.0
            utilization = min(utilization, 1.0)
            
            # Scale active principal values mathematically perfectly
            l_idx = liquidity_index / RAY
            v_idx = variable_borrow_index / RAY
            
            scaled_supply_tokens = max(0, state.total_scaled_supply * l_idx)
            scaled_borrow_tokens = max(0, state.total_scaled_borrow * v_idx)

            ts = block_ts_map.get(log_entry.block_number, datetime.datetime.now(datetime.UTC))
            symbol, _ = RESERVE_MAP[reserve_addr]
            emode = self._emode_categories.get(state.e_mode_category, AaveEModeCategory())

            return {
                "block_number": log_entry.block_number,
                "symbol": symbol,
                "entity_id": eid,
                "supply_apy": supply_apy,
                "borrow_apy": borrow_apy,
                "utilization": utilization,
                "total_supply": scaled_supply_tokens,
                "total_borrow": scaled_borrow_tokens,
                "ltv": state.ltv,
                "liquidation_threshold": state.liquidation_threshold,
                "liquidation_penalty": state.liquidation_penalty,
                "e_mode_category": state.e_mode_category,
                "e_mode_ltv": emode.ltv,
                "e_mode_liquidation_threshold": emode.liquidation_threshold,
                "e_mode_liquidation_penalty": emode.liquidation_penalty,
                "e_mode_label": emode.label,
                "timestamp": ts.replace(tzinfo=None),
            }

        return None

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        if not decoded_rows:
            return 0


        df = pd.DataFrame(decoded_rows)
        df["ts"] = pd.to_datetime(df["timestamp"]).dt.floor("h")

        df.sort_values("block_number", inplace=True)
        hourly = df.groupby(["ts", "symbol"]).last().reset_index()

        eth_price, btc_price = get_chainlink_prices(ch)

        def token_to_usd(symbol, raw_amount):
            decimals = SYMBOL_TO_DEC[symbol]
            human = raw_amount / (10 ** decimals)
            return human * get_usd_price(symbol, eth_price, btc_price)

        hourly["supply_usd"] = hourly.apply(lambda r: token_to_usd(r["symbol"], r["total_supply"]), axis=1)
        hourly["borrow_usd"] = hourly.apply(lambda r: token_to_usd(r["symbol"], r["total_borrow"]), axis=1)

        final = pd.DataFrame({
            "timestamp": hourly["ts"],
            "protocol": "AAVE_MARKET",
            "symbol": hourly["symbol"],
            "entity_id": hourly["entity_id"],
            "target_id": "",
            "supply_usd": hourly["supply_usd"],
            "borrow_usd": hourly["borrow_usd"],
            "supply_apy": hourly["supply_apy"],
            "borrow_apy": hourly["borrow_apy"],
            "utilization": hourly["utilization"],
            "price_usd": hourly["symbol"].map(lambda s: get_usd_price(s, eth_price, btc_price)),
            "ltv": hourly["ltv"].fillna(0.0),
            "liquidation_threshold": hourly["liquidation_threshold"].fillna(0.0),
            "liquidation_penalty": hourly["liquidation_penalty"].fillna(0.0),
            "e_mode_category": hourly["e_mode_category"].fillna(0).astype("uint8"),
            "e_mode_ltv": hourly["e_mode_ltv"].fillna(0.0),
            "e_mode_liquidation_threshold": hourly["e_mode_liquidation_threshold"].fillna(0.0),
            "e_mode_liquidation_penalty": hourly["e_mode_liquidation_penalty"].fillna(0.0),
            "e_mode_label": hourly["e_mode_label"].fillna("").astype(str),
        })

        final = forward_fill_hourly(final, ch, "AAVE_MARKET", compound=False)
        if len(final) > 0:
            def risk_fields(entity_id: str) -> dict:
                state = self._reserves.get(str(entity_id), AaveReserveState())
                emode = self._emode_categories.get(state.e_mode_category, AaveEModeCategory())
                return {
                    "ltv": float(state.ltv),
                    "liquidation_threshold": float(state.liquidation_threshold),
                    "liquidation_penalty": float(state.liquidation_penalty),
                    "e_mode_category": int(state.e_mode_category),
                    "e_mode_ltv": float(emode.ltv),
                    "e_mode_liquidation_threshold": float(emode.liquidation_threshold),
                    "e_mode_liquidation_penalty": float(emode.liquidation_penalty),
                    "e_mode_label": str(emode.label),
                }

            risk_frame = final["entity_id"].map(risk_fields).apply(pd.Series)
            for column in risk_frame.columns:
                final[column] = risk_frame[column]
            final["e_mode_category"] = final["e_mode_category"].fillna(0).astype("uint8")
            final["e_mode_label"] = final["e_mode_label"].fillna("").astype(str)

        if len(final) > 0:
            min_ts_dt = final["timestamp"].min()
            max_ts_dt = final["timestamp"].max()
            min_ts = min_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
            max_ts = max_ts_dt.strftime("%Y-%m-%d %H:%M:%S")
            rewrite_protocol_window_if_enabled(
                ch,
                self.output_table,
                "AAVE_MARKET",
                min_ts,
                max_ts,
            )
            insert_df_batched(ch, self.output_table, final)
            upsert_market_timeseries(ch, final)
            upsert_api_market_latest(ch, final)
            refresh_api_protocol_tvl_weekly(ch, min_ts_dt, max_ts_dt)
            
            # Persist dynamic physical state boundaries
            if len(self._reserves) > 0:
                state_df = pd.DataFrame([
                    {
                        "entity_id": eid,
                        "total_scaled_supply": float(r.total_scaled_supply),
                        "total_scaled_borrow": float(r.total_scaled_borrow),
                        "liquidity_index": float(r.liquidity_index),
                        "variable_borrow_index": float(r.variable_borrow_index)
                    } for eid, r in self._reserves.items()
                ])
                insert_df_batched(ch, "aave_scaled_state", state_df)
                risk_df = pd.DataFrame([
                    {
                        "entity_id": eid,
                        "ltv": float(r.ltv),
                        "liquidation_threshold": float(r.liquidation_threshold),
                        "liquidation_penalty": float(r.liquidation_penalty),
                        "e_mode_category": int(r.e_mode_category),
                    } for eid, r in self._reserves.items()
                ])
                insert_df_batched(ch, "aave_reserve_risk_state", risk_df)
            if len(self._emode_categories) > 0:
                emode_df = pd.DataFrame([
                    {
                        "category_id": int(category_id),
                        "ltv": float(category.ltv),
                        "liquidation_threshold": float(category.liquidation_threshold),
                        "liquidation_penalty": float(category.liquidation_penalty),
                        "price_source": str(category.price_source),
                        "label": str(category.label),
                    } for category_id, category in self._emode_categories.items()
                ])
                insert_df_batched(ch, "aave_emode_categories", emode_df)

        return len(final)
