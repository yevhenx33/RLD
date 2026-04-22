"""
FluidSource — Fluid Protocol LogOperate event decoder.

Decodes BigMath-packed totalAmounts and exchangePricesAndConfig from
LogOperate events to reconstruct supply/borrow balances per token.
"""

import datetime
import logging
from typing import Optional

import pandas as pd

from ..base import (
    BaseSource,
    forward_fill_hourly,
    insert_df_batched,
    upsert_api_market_latest,
    rewrite_protocol_window_if_enabled,
)
from ..tokens import (TOKENS as ADDR_MAP, STABLES, ETH_ASSETS, BTC_ASSETS,
                      PRICE_MULTIPLIERS, get_usd_price, get_chainlink_prices)

log = logging.getLogger("indexer.fluid")

MASK_64 = (1 << 64) - 1


def bigmath(p):
    """Fluid BigMath: 56-bit coefficient + 8-bit exponent."""
    return (p >> 8) << (p & 0xFF)


class FluidSource(BaseSource):
    name = "FLUID_MARKET"
    contracts = ["0x52Aa899454998Be5b000Ad077a46Bbe360F4e497"]
    topics = [
        "0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15",  # LogOperate
    ]
    raw_table = "fluid_events"

    def _event_name(self, log_entry) -> str:
        return "Operate"

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        """Decode BigMath-packed LogOperate event."""
        topics = log_entry.topics or []
        data = log_entry.data

        if len(topics) < 3 or not data or len(data) < 386:
            return None

        addr = topics[2][26:].lower()
        if addr not in ADDR_MAP:
            return None

        try:
            raw = bytes.fromhex(data[2:])
            w4 = int.from_bytes(raw[128:160], 'big')
            w5 = int.from_bytes(raw[160:192], 'big')
        except Exception:
            return None

        sup_int = bigmath(w4 & MASK_64)
        sup_free = bigmath((w4 >> 64) & MASK_64)
        bor_int = bigmath((w4 >> 128) & MASK_64)
        bor_free = bigmath((w4 >> 192) & MASK_64)

        sup_ep = (w5 >> 91) & MASK_64
        bor_ep = (w5 >> 155) & MASK_64
        if sup_ep == 0: sup_ep = int(1e12)
        if bor_ep == 0: bor_ep = int(1e12)

        util_raw = (w5 >> 30) & 0x3FFF
        rate_raw = w5 & 0xFFFF
        fee_raw = (w5 >> 16) & 0x3FFF

        symbol, decimals = ADDR_MAP[addr]
        scale = 10 ** decimals

        supply_tokens = (sup_int * sup_ep / 1e12 + sup_free) / scale
        borrow_tokens = (bor_int * bor_ep / 1e12 + bor_free) / scale
        utilization = util_raw / 10000.0
        borrow_apy = rate_raw / 10000.0
        fee = fee_raw / 10000.0
        supply_apy = max(0, borrow_apy * utilization * (1.0 - fee))

        ts = block_ts_map.get(log_entry.block_number, datetime.datetime.now(datetime.UTC))

        return {
            "block_number": log_entry.block_number,
            "symbol": symbol,
            "entity_id": topics[2].lower(),
            "supply_tokens": supply_tokens,
            "borrow_tokens": borrow_tokens,
            "utilization": utilization,
            "borrow_apy": borrow_apy,
            "supply_apy": supply_apy,
            "timestamp": ts.replace(tzinfo=None),
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        """Merge decoded Fluid events into unified_timeseries with Chainlink prices."""
        if not decoded_rows:
            return 0

        df = pd.DataFrame(decoded_rows)
        df['ts'] = pd.to_datetime(df['timestamp']).dt.floor('h')

        # Take last event per (hour, symbol)
        df.sort_values('block_number', inplace=True)
        hourly = df.groupby(['ts', 'symbol']).last().reset_index()

        # Get Chainlink prices
        eth_prices = ch.query_df("""
            SELECT toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS eth_usd
            FROM chainlink_prices WHERE feed = 'ETH / USD'
            GROUP BY ts ORDER BY ts
        """)
        btc_prices = ch.query_df("""
            SELECT toStartOfHour(timestamp) AS ts, argMax(price, timestamp) AS btc_usd
            FROM chainlink_prices WHERE feed = 'BTC / USD'
            GROUP BY ts ORDER BY ts
        """)

        merged = pd.merge(hourly, eth_prices, on='ts', how='left')
        merged = pd.merge(merged, btc_prices, on='ts', how='left')
        merged['eth_usd'] = merged['eth_usd'].ffill().bfill()
        merged['btc_usd'] = merged['btc_usd'].ffill().bfill()

        def get_price(row):
            sym = row['symbol']
            if sym in STABLES:
                return 1.0
            if sym in PRICE_MULTIPLIERS:
                mult = PRICE_MULTIPLIERS[sym]
                if sym in ETH_ASSETS:
                    return row.get('eth_usd', 2000) * mult
                elif sym in BTC_ASSETS:
                    return row.get('btc_usd', 70000) * mult
            if sym in ('PAXG', 'XAUt'):
                return 3300.0
            if sym == 'FLUID':
                return 0.50
            return 1.0

        merged['price_usd'] = merged.apply(get_price, axis=1)
        merged['supply_usd'] = merged['supply_tokens'] * merged['price_usd']
        merged['borrow_usd'] = merged['borrow_tokens'] * merged['price_usd']

        final = pd.DataFrame({
            'timestamp': merged['ts'],
            'protocol': 'FLUID_MARKET',
            'symbol': merged['symbol'],
            'entity_id': merged['entity_id'],
            'target_id': '',
            'supply_usd': merged['supply_usd'],
            'borrow_usd': merged['borrow_usd'],
            'supply_apy': merged['supply_apy'],
            'borrow_apy': merged['borrow_apy'],
            'utilization': merged['utilization'],
            'price_usd': merged['price_usd'],
        })
        final = final[final['supply_usd'] > 0]

        # Forward-fill: ensure contiguous hourly data per entity
        final = forward_fill_hourly(final, ch, 'FLUID_MARKET')

        # Delete existing rows for all hours in the filled range, then re-insert
        if len(final) > 0:
            min_ts = final['timestamp'].min().strftime('%Y-%m-%d %H:%M:%S')
            max_ts = final['timestamp'].max().strftime('%Y-%m-%d %H:%M:%S')
            rewrite_protocol_window_if_enabled(
                ch,
                self.output_table,
                "FLUID_MARKET",
                min_ts,
                max_ts,
            )
            insert_df_batched(ch, self.output_table, final)
            upsert_api_market_latest(ch, final)

        return len(final)
