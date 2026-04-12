"""
AaveV3Source — Aave V3 ReserveDataUpdated event decoder.

Decodes per-reserve rate updates from the Aave V3 Pool contract.
Each ReserveDataUpdated event carries the full rate state (supply APY,
borrow APY, liquidity index, variable borrow index) in RAY format (1e27).

This is a stateless decoder — no cumulative tracking needed.
"""

import datetime
import logging
from typing import Optional

import pandas as pd

from ..base import BaseSource

log = logging.getLogger("indexer.aave_v3")

# ── Aave V3 Pool contract ──────────────────────────────────
AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# ReserveDataUpdated(address indexed reserve,
#   uint256 liquidityRate, uint256 stableBorrowRate,
#   uint256 variableBorrowRate, uint256 liquidityIndex,
#   uint256 variableBorrowIndex)
TOPIC_RESERVE_DATA_UPDATED = (
    "0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"
)

RAY = 10**27

# ── Reserve address → symbol mapping (Aave V3 Ethereum) ────
# Addresses are lowercase, WITHOUT 0x prefix, right-padded to 64 hex
RESERVE_MAP = {
    "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "dac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": ("wstETH", 18),
    "ae78736cd615f374d3085123a210448e74fc6393": ("rETH", 18),
    "be9895146f7af43049ca1c1ae358b0541ea49704": ("cbETH", 18),
    "cd5fe23c85820f7b72d0926fc9b05b43e359b7ee": ("weETH", 18),
    "514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
    "7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": ("AAVE", 18),
    "9f8f72aa9304c8b593d555f12ef6589cc3a579a2": ("MKR", 18),
    "1f9840a85d5af5bf1d1762f925bdaddc4201f984": ("UNI", 18),
    "c011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f": ("SNX", 18),
    "ba100000625a3754423978a60c9317c58a424e3d": ("BAL", 18),
    "d533a949740bb3306d119cc777fa900ba034cd52": ("CRV", 18),
    "1a7e4e63778b4f12a199c062f3efdd288afcbce8": ("agEUR", 18),
    "5f98805a4e8be255a32880fdec7f6728c6568ba0": ("LUSD", 18),
    "853d955acef822db058eb8505911ed77f175b99e": ("FRAX", 18),
    "ae7ab96520de3a18e5e111b5eaab095312d7fe84": ("stETH", 18),
    "6c3ea9036406852006290770bedfcaba0e23a0e8": ("PYUSD", 6),
    "40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f": ("GHO", 18),
    "83f20f44975d03b1b09e64809b757c47f942beea": ("sDAI", 18),
    "a35b1b31ce002fbf2058d22f30f95d405200a15b": ("sFRAX", 18),
    "f939e0a03fb07f59a73314e73794be0e57ac1b4e": ("crvUSD", 18),
    "530824da86689c9c17cdc2871ff29b058345b44a": ("STKAAVE", 18),
    "5e8422345238f34275888049021821e8e08caa1f": ("frxETH", 18),
    "ac3e018457b222d93114458476f3e3416abbe38f": ("sfrxETH", 18),
    "d5f7838f5c461feff7fe49ea5ebaf7728bb0adfa": ("mETH", 18),
    "4c9edd5852cd905f086c759e8383e09bff1e68b3": ("USDe", 18),
    "9d39a5de30e57443bff2a8307a4256c8797a3497": ("sUSDe", 18),
    "bf5495efe5db9ce00f80364c8b423567e58d2110": ("ezETH", 18),
    "a1290d69c65a6fe4df752f95823fae25cb99e5a7": ("rsETH", 18),
    "35fa164735182de50811e8e2e824cfb9b6118ac2": ("eETH", 18),
    "e72b141df173b999ae7c1adcbf60cc9833ce56a8": ("EURV", 18),
    "edfa23602d0ec14714057867a78d01e94176bea0": ("osETH", 18),
    "5c7e299cf531eb66f2a1df637d37abb78e6200c7": ("WOETH", 18),
    "b40b6608b2743e691c9b54ddbdee7bf03cd79f1c": ("USD0", 18),
    "73a15fed60bf67631dc6cd7bc5b6e8da8190acf5": ("USD0pp", 18),
    "57f5e098cad7a3d1eed53991d4d66c45c9af7812": ("wUSDM", 18),
    "defe616913fa88a5af0c5fc6a5e0d25e89ea5471": ("Paxos", 18),
    "8236a87084f8b84306f72007f36f2618a5634494": ("LBTC", 8),
    "2416092f143378750bb29b79ed961ab195cceea5": ("ezSOL", 9),
    "8c1bed5b9a0928467c9b1341da1d7bd5e10b6549": ("lsETH", 18),
    "cbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("cbBTC", 8),
    "18084fba666a33d37592fa2633fd49a74dd93a88": ("tBTC", 18),
    "7122985931b4d0b1aa7cc69dc3e466fc6c7bca44": ("KSETH", 18),
    "657e8c867d8b37dcc18fa4caead9c45eb088c642": ("eBTC", 8),
    "917cee801a67f933f2e6b33fc0cd1ed2d5909d88": ("weETHs", 18),
    "a663b02cf0a4b149d2ad41910cb81e23e1c41c32": ("sFRX", 18),
}

# Asset classification for USD pricing
STABLES = {
    "USDC", "DAI", "USDT", "GHO", "LUSD", "FRAX", "crvUSD", "PYUSD",
    "sDAI", "sFRAX", "USDe", "sUSDe", "agEUR", "USD0", "USD0pp",
    "wUSDM", "Paxos", "EURV",
}
ETH_ASSETS = {
    "WETH", "wstETH", "rETH", "cbETH", "weETH", "stETH", "frxETH",
    "sfrxETH", "mETH", "ezETH", "rsETH", "eETH", "osETH", "WOETH",
    "KSETH", "weETHs", "lsETH",
}
BTC_ASSETS = {"WBTC", "LBTC", "cbBTC", "tBTC", "eBTC"}


class AaveV3Source(BaseSource):
    """Decode Aave V3 ReserveDataUpdated events into rate timeseries."""

    name = "AAVE_MARKET"
    contracts = [AAVE_POOL]
    topics = [TOPIC_RESERVE_DATA_UPDATED]
    raw_table = "aave_events"

    def get_cursor(self, ch) -> int:
        """Track cursor via unified_timeseries AAVE_MARKET rows."""
        result = ch.command(
            "SELECT max(block_number) FROM aave_events"
        )
        return int(result) if result else 0

    def _event_name(self, log_entry) -> str:
        return "ReserveDataUpdated"

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        """Decode ReserveDataUpdated into supply/borrow APY."""
        topics = log_entry.topics or []
        data = log_entry.data

        if len(topics) < 2 or not data or len(data) < 322:
            return None

        # topic1 = indexed reserve address (right-padded bytes32)
        reserve_addr = topics[1][26:].lower()
        if reserve_addr not in RESERVE_MAP:
            return None

        symbol, decimals = RESERVE_MAP[reserve_addr]

        # data layout: 5 × uint256 words
        #   word 0: liquidityRate     (supply APY in RAY)
        #   word 1: stableBorrowRate  (deprecated, usually 0)
        #   word 2: variableBorrowRate (borrow APY in RAY)
        #   word 3: liquidityIndex
        #   word 4: variableBorrowIndex
        raw = data[2:]
        liquidity_rate = int(raw[0:64], 16)
        variable_borrow_rate = int(raw[128:192], 16)

        supply_apy = liquidity_rate / RAY
        borrow_apy = variable_borrow_rate / RAY

        # Utilization ≈ borrow_apy / (borrow_apy * (1 - reserve_factor))
        # We don't have reserve_factor in events, approximate from rates
        utilization = supply_apy / borrow_apy if borrow_apy > 0 else 0.0
        utilization = min(utilization, 1.0)

        ts = block_ts_map.get(
            log_entry.block_number,
            datetime.datetime.now(datetime.UTC),
        )

        return {
            "block_number": log_entry.block_number,
            "symbol": symbol,
            "entity_id": "0x" + reserve_addr,
            "supply_apy": supply_apy,
            "borrow_apy": borrow_apy,
            "utilization": utilization,
            "timestamp": ts.replace(tzinfo=None),
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        """Merge decoded Aave events into unified_timeseries with prices."""
        if not decoded_rows:
            return 0

        df = pd.DataFrame(decoded_rows)
        df["ts"] = pd.to_datetime(df["timestamp"]).dt.floor("h")

        # Take last event per (hour, symbol)
        df.sort_values("block_number", inplace=True)
        hourly = df.groupby(["ts", "symbol"]).last().reset_index()

        # Get Chainlink prices for USD conversion
        eth_prices = ch.query_df("""
            SELECT toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS eth_usd
            FROM chainlink_prices WHERE feed = 'ETH/USD'
            GROUP BY ts ORDER BY ts
        """)
        btc_prices = ch.query_df("""
            SELECT toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS btc_usd
            FROM chainlink_prices WHERE feed = 'BTC/USD'
            GROUP BY ts ORDER BY ts
        """)

        merged = pd.merge(hourly, eth_prices, on="ts", how="left")
        merged = pd.merge(merged, btc_prices, on="ts", how="left")
        merged["eth_usd"] = merged["eth_usd"].ffill().bfill()
        merged["btc_usd"] = merged["btc_usd"].ffill().bfill()

        def get_price(row):
            sym = row["symbol"]
            if sym in STABLES:
                return 1.0
            if sym in ETH_ASSETS:
                return row.get("eth_usd", 2000)
            if sym in BTC_ASSETS:
                return row.get("btc_usd", 70000)
            if sym in ("AAVE", "UNI", "LINK", "MKR", "SNX", "BAL", "CRV"):
                return 10.0  # rough placeholder for governance tokens
            return 1.0

        merged["price_usd"] = merged.apply(get_price, axis=1)

        # Delete existing rows for affected hours, then re-insert
        hours = merged["ts"].unique()
        for h in hours:
            ts_str = pd.Timestamp(h).strftime("%Y-%m-%d %H:%M:%S")
            ch.command(
                f"ALTER TABLE unified_timeseries DELETE "
                f"WHERE protocol='AAVE_MARKET' AND timestamp='{ts_str}'"
            )

        # Carry forward last known TVL per entity (from old daemon data)
        try:
            last_tvl = ch.query_df("""
                SELECT entity_id,
                       argMax(supply_usd, timestamp) AS last_supply,
                       argMax(borrow_usd, timestamp) AS last_borrow
                FROM unified_timeseries
                WHERE protocol = 'AAVE_MARKET'
                  AND supply_usd > 0
                GROUP BY entity_id
            """)
            tvl_map = {}
            if len(last_tvl) > 0:
                for _, r in last_tvl.iterrows():
                    tvl_map[r["entity_id"]] = (r["last_supply"], r["last_borrow"])
        except Exception:
            tvl_map = {}

        merged["supply_usd_out"] = merged["entity_id"].map(
            lambda eid: tvl_map.get(eid, (0.0, 0.0))[0]
        )
        merged["borrow_usd_out"] = merged["entity_id"].map(
            lambda eid: tvl_map.get(eid, (0.0, 0.0))[1]
        )

        final = pd.DataFrame({
            "timestamp": merged["ts"],
            "protocol": "AAVE_MARKET",
            "symbol": merged["symbol"],
            "entity_id": merged["entity_id"],
            "target_id": "",
            "supply_usd": merged["supply_usd_out"],
            "borrow_usd": merged["borrow_usd_out"],
            "supply_apy": merged["supply_apy"],
            "borrow_apy": merged["borrow_apy"],
            "utilization": merged["utilization"],
            "price_usd": merged["price_usd"],
        })

        if len(final) > 0:
            ch.insert_df("unified_timeseries", final)

        return len(final)
