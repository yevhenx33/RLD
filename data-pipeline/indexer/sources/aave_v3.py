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
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from ..base import BaseSource, forward_fill_hourly
from ..tokens import (TOKENS as RESERVE_MAP, STABLES, ETH_ASSETS, BTC_ASSETS,
                      PRICE_MULTIPLIERS, get_chainlink_prices, get_usd_price)
from ..sources.morpho import SYM_DECIMALS  # Reusing shared dictionary for simplicity

log = logging.getLogger("indexer.aave_v3")

AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

TOPIC_RESERVE_DATA_UPDATED = "0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"
TOPIC_SUPPLY = "0x2b627736bca15cd5381dcf80b0bf11fd197d01a037c52b927a881a10fb73ba61"
TOPIC_WITHDRAW = "0x3115d1449a7b732c986cba18244e897a450f61e1bb8d589cd2e69e6c8924f9f7"
TOPIC_BORROW = "0xb3d084820fb1a9decffb176436bd02558d15fac9b0ddfed8c465bc7359d7dce0"
TOPIC_REPAY = "0xa534c8dbe71f871f9f3530e97a74601fea17b426cae02e1c5aee42c96c784051"
TOPIC_LIQUIDATION_CALL = "0xe413a321e8681d831f4dbccbca790d2952b56f977908e45be37335533e005286"

RAY = 10**27

EVENT_MAP = {
    TOPIC_RESERVE_DATA_UPDATED: "ReserveDataUpdated",
    TOPIC_SUPPLY: "Supply",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_BORROW: "Borrow",
    TOPIC_REPAY: "Repay",
    TOPIC_LIQUIDATION_CALL: "LiquidationCall",
}

@dataclass
class AaveReserveState:
    total_supply_principal: float = 0.0
    total_borrow_principal: float = 0.0


SYMBOL_TO_DEC = {sym: dec for sym, dec in RESERVE_MAP.values()}

class AaveV3Source(BaseSource):
    name = "AAVE_MARKET"
    contracts = [AAVE_POOL]
    topics = list(EVENT_MAP.keys())
    raw_table = "aave_events"

    def __init__(self):
        self._reserves: dict[str, AaveReserveState] = {}
        self._initialized = False

    def get_cursor(self, ch) -> int:
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

        # Reserve address is typically topic1 unless LiquidationCall (where it's collateral in topic1 and debt in topic2)
        if evt == "LiquidationCall":
            if len(topics) < 3: return None
            collateral_addr = "0x" + topics[1][26:].lower()
            debt_addr = "0x" + topics[2][26:].lower()
            
            raw = data[2:]
            if len(raw) >= 128:
                debt_covered = int(raw[0:64], 16)
                liquidated_collateral = int(raw[64:128], 16)
                
                if debt_addr not in self._reserves:
                    self._reserves[debt_addr] = AaveReserveState()
                self._reserves[debt_addr].total_borrow_principal -= debt_covered

                if collateral_addr not in self._reserves:
                    self._reserves[collateral_addr] = AaveReserveState()
                self._reserves[collateral_addr].total_supply_principal -= liquidated_collateral
            return None

        # Standard reserve interactions
        if len(topics) < 2: return None
        reserve_addr = topics[1][26:].lower()
        eid = "0x" + reserve_addr

        if reserve_addr not in RESERVE_MAP:
            return None

        if eid not in self._reserves:
            self._reserves[eid] = AaveReserveState()
        state = self._reserves[eid]
        
        raw = data[2:]

        if evt == "Supply" and len(raw) >= 128:
            # user(data0), amount(data1)
            amount = int(raw[64:128], 16)
            state.total_supply_principal += amount
        
        elif evt == "Withdraw" and len(raw) >= 64:
            # amount(data0)
            amount = int(raw[0:64], 16)
            state.total_supply_principal -= amount
            
        elif evt == "Borrow" and len(raw) >= 128:
            # user(data0), amount(data1)
            amount = int(raw[64:128], 16)
            state.total_borrow_principal += amount
            
        elif evt == "Repay" and len(raw) >= 64:
            # amount(data0), useATokens(data1)
            amount = int(raw[0:64], 16)
            state.total_borrow_principal -= amount

        elif evt == "ReserveDataUpdated" and len(raw) >= 320:
            liquidity_rate = int(raw[0:64], 16)
            variable_borrow_rate = int(raw[128:192], 16)
            liquidity_index = int(raw[192:256], 16)
            variable_borrow_index = int(raw[256:320], 16)

            supply_apy = liquidity_rate / RAY
            borrow_apy = variable_borrow_rate / RAY
            utilization = supply_apy / borrow_apy if borrow_apy > 0 else 0.0
            utilization = min(utilization, 1.0)
            
            # Scale active principal values mathematically perfectly
            l_idx = liquidity_index / RAY
            v_idx = variable_borrow_index / RAY
            
            scaled_supply_tokens = max(0, state.total_supply_principal * l_idx)
            scaled_borrow_tokens = max(0, state.total_borrow_principal * v_idx)

            ts = block_ts_map.get(log_entry.block_number, datetime.datetime.now(datetime.UTC))
            symbol, _ = RESERVE_MAP[reserve_addr]

            return {
                "block_number": log_entry.block_number,
                "symbol": symbol,
                "entity_id": eid,
                "supply_apy": supply_apy,
                "borrow_apy": borrow_apy,
                "utilization": utilization,
                "total_supply": scaled_supply_tokens,
                "total_borrow": scaled_borrow_tokens,
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
        })

        final = forward_fill_hourly(final, ch, "AAVE_MARKET")

        if len(final) > 0:
            min_ts = final["timestamp"].min().strftime("%Y-%m-%d %H:%M:%S")
            max_ts = final["timestamp"].max().strftime("%Y-%m-%d %H:%M:%S")
            ch.command(
                f"ALTER TABLE unified_timeseries DELETE "
                f"WHERE protocol='AAVE_MARKET' "
                f"AND timestamp >= '{min_ts}' AND timestamp <= '{max_ts}'"
            )
            ch.insert_df("unified_timeseries", final)

        return len(final)
