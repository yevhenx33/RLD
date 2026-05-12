import datetime
import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from analytics.base import forward_fill_hourly  # noqa: E402
from analytics.protocols import COMPOUND_V3_MARKET  # noqa: E402
from analytics.sources.compound import (  # noqa: E402
    COMPOUND_V3_USDC,
    CompoundV2Market,
    CompoundV2Source,
    CompoundV2State,
    CompoundV3Comet,
    CompoundV3Source,
    STATIC_V3_COMETS,
    TOPIC_V2_ACCRUE,
    TOPIC_V2_BORROW,
    TOPIC_V2_LIQUIDATE,
    TOPIC_V2_MINT,
    TOPIC_V2_REDEEM,
    TOPIC_V2_REPAY,
    TOPIC_V3_ABSORB_DEBT,
    TOPIC_V3_BUY_COLLATERAL,
    TOPIC_V3_SUPPLY,
    TOPIC_V3_SUPPLY_COLLATERAL,
    TOPIC_V3_TRANSFER,
    TOPIC_V3_TRANSFER_COLLATERAL,
    TOPIC_V3_WITHDRAW,
    TOPIC_V3_WITHDRAW_COLLATERAL,
    TOPIC_V3_WITHDRAW_RESERVES,
    WAD,
)


class Log:
    def __init__(self, address, topics, data):
        self.address = address
        self.topics = topics
        self.data = data
        self.block_number = 100
        self.transaction_hash = "0xabc"
        self.log_index = 1


class FakeFillClickHouse:
    def query_df(self, _query):
        return pd.DataFrame(
            [
                {
                    "entity_id": COMPOUND_V3_USDC,
                    "symbol": "USDC",
                    "target_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    "last_ts": pd.Timestamp("2026-05-11 07:00:00"),
                    "supply_usd": 6_437_836.905469,
                    "borrow_usd": 4_404_852.787726,
                    "supply_apy": 0.022236927967818905,
                    "borrow_apy": 0.03894746086622416,
                    "utilization": 0.6842131685541829,
                    "price_usd": 1.0,
                }
            ]
        )


def word(value: int) -> str:
    return f"{int(value):064x}"


def topic_address(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


class CompoundSourceTest(unittest.TestCase):
    def test_forward_fill_hourly_uses_intra_hour_compound_v3_anchor(self):
        raw = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-05-11 08:08:11"),
                    "protocol": COMPOUND_V3_MARKET,
                    "symbol": "USDC",
                    "entity_id": COMPOUND_V3_USDC,
                    "target_id": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    "supply_usd": 333_209_215.938101,
                    "borrow_usd": 291_736_831.001125,
                    "supply_apy": 0.03151931403359357,
                    "borrow_apy": 0.03932065292292118,
                    "utilization": 0.8755365009331549,
                    "price_usd": 1.0,
                }
            ]
        )

        filled = forward_fill_hourly(raw, FakeFillClickHouse(), COMPOUND_V3_MARKET, compound=False)

        self.assertEqual(filled["timestamp"].tolist(), [pd.Timestamp("2026-05-11 08:00:00")])
        self.assertEqual(filled["supply_usd"].tolist(), [333_209_215.938101])
        self.assertEqual(filled["borrow_usd"].tolist(), [291_736_831.001125])

    def test_v3_static_mainnet_seed_matches_official_ethereum_markets(self):
        expected_symbols = {"USDC", "WETH", "USDT", "WBTC", "wstETH", "USDS"}
        self.assertEqual({values[2] for values in STATIC_V3_COMETS.values()}, expected_symbols)

        source = CompoundV3Source()
        source._comets[COMPOUND_V3_USDC] = CompoundV3Comet(
            COMPOUND_V3_USDC,
            "cUSDCv3",
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "USDC",
            6,
        )

        class PartialRegistryClickHouse:
            def query(self, _query):
                class Result:
                    result_rows = []

                return Result()

        source._load_registry(PartialRegistryClickHouse())
        self.assertEqual(len(source._comets), 6)

    def test_v2_decodes_core_market_events(self):
        source = CompoundV2Source()
        ctoken = "0x39aa39c021dfbae8fac545936693ac917d5e7563"
        source._markets[ctoken] = CompoundV2Market(ctoken, "cUSDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
        source._states[ctoken] = CompoundV2State(reserve_factor=0.1, borrow_index=WAD, last_event_timestamp=datetime.datetime(2026, 1, 1))
        ts = datetime.datetime(2026, 1, 1, 1)

        mint = source.decode(Log(ctoken, [TOPIC_V2_MINT], "0x" + word(0x1234) + word(1_000_000) + word(50_000)), {100: ts})
        metric = source._apply_event(mint)
        self.assertEqual(source._states[ctoken].cash, 1_000_000)
        self.assertEqual(source._states[ctoken].total_supply_ctokens, 50_000)
        self.assertEqual(metric["symbol"], "USDC")

        borrow = source.decode(Log(ctoken, [TOPIC_V2_BORROW], "0x" + word(0x1234) + word(250_000) + word(250_000) + word(250_000)), {100: ts})
        source._apply_event(borrow)
        self.assertEqual(source._states[ctoken].cash, 750_000)
        self.assertEqual(source._states[ctoken].total_borrows, 250_000)

        repay = source.decode(Log(ctoken, [TOPIC_V2_REPAY], "0x" + word(0x1234) + word(0x1234) + word(100_000) + word(150_000) + word(150_000)), {100: ts})
        source._apply_event(repay)
        self.assertEqual(source._states[ctoken].cash, 850_000)
        self.assertEqual(source._states[ctoken].total_borrows, 150_000)

        liquidate = source.decode(Log(ctoken, [TOPIC_V2_LIQUIDATE], "0x" + word(0x1234) + word(0x5678) + word(50_000) + word(0x9ABC) + word(1_000)), {100: ts})
        source._apply_event(liquidate)
        self.assertEqual(source._states[ctoken].cash, 900_000)
        self.assertEqual(source._states[ctoken].total_borrows, 100_000)

        redeem = source.decode(Log(ctoken, [TOPIC_V2_REDEEM], "0x" + word(0x1234) + word(200_000) + word(10_000)), {100: ts})
        source._apply_event(redeem)
        self.assertEqual(source._states[ctoken].cash, 700_000)
        self.assertEqual(source._states[ctoken].total_supply_ctokens, 40_000)

        accrue = source.decode(Log(ctoken, [TOPIC_V2_ACCRUE], "0x" + word(1_000_000) + word(10_000) + word(WAD + 10**14) + word(200_000)), {100: ts})
        metric = source._apply_event(accrue)
        self.assertEqual(source._states[ctoken].total_borrows, 200_000)
        self.assertGreaterEqual(metric["borrow_apy"], 0.0)

    def test_v3_base_events_reclassify_supply_and_borrow(self):
        source = CompoundV3Source()
        comet = COMPOUND_V3_USDC
        source._comets[comet] = CompoundV3Comet(comet, "cUSDCv3", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
        account = "0x1111111111111111111111111111111111111111"
        ts = datetime.datetime(2026, 1, 1)

        supply = source.decode(Log(comet, [TOPIC_V3_SUPPLY, topic_address(account), topic_address(account)], "0x" + word(1_000_000)), {100: ts})
        mint = source.decode(Log(comet, [TOPIC_V3_TRANSFER, topic_address("0x0000000000000000000000000000000000000000"), topic_address(account)], "0x" + word(1_000_000)), {100: ts})
        source._apply_tx([supply, mint])
        self.assertEqual(source._states[comet].total_supply_base, 1_000_000)
        self.assertEqual(source._states[comet].total_borrow_base, 0)

        withdraw = source.decode(Log(comet, [TOPIC_V3_WITHDRAW, topic_address(account), topic_address(account)], "0x" + word(1_500_000)), {100: ts})
        burn = source.decode(Log(comet, [TOPIC_V3_TRANSFER, topic_address(account), topic_address("0x0000000000000000000000000000000000000000")], "0x" + word(1_000_000)), {100: ts})
        source._apply_tx([withdraw, burn])
        self.assertEqual(source._states[comet].total_supply_base, 0)
        self.assertEqual(source._states[comet].total_borrow_base, 500_000)

        recipient = "0x2222222222222222222222222222222222222222"
        transfer_mint = source.decode(Log(comet, [TOPIC_V3_TRANSFER, topic_address("0x0000000000000000000000000000000000000000"), topic_address(recipient)], "0x" + word(100_000)), {100: ts})
        source._apply_tx([transfer_mint])
        self.assertEqual(source._states[comet].total_supply_base, 100_000)
        self.assertEqual(source._states[comet].total_borrow_base, 600_000)

        absorb = source.decode(Log(comet, [TOPIC_V3_ABSORB_DEBT, topic_address(account), topic_address(account)], "0x" + word(600_000)), {100: ts})
        source._apply_tx([absorb])
        self.assertEqual(source._states[comet].total_borrow_base, 0)

        reserve = source.decode(Log(comet, [TOPIC_V3_WITHDRAW_RESERVES, topic_address(account)], "0x" + word(50_000)), {100: ts})
        source._states[comet].reserves_base = 75_000
        source._apply_tx([reserve])
        self.assertEqual(source._states[comet].reserves_base, 25_000)

    def test_v3_collateral_events(self):
        source = CompoundV3Source()
        comet = COMPOUND_V3_USDC
        source._comets[comet] = CompoundV3Comet(comet, "cUSDCv3", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", 6)
        account = "0x1111111111111111111111111111111111111111"
        asset = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2".lower()
        ts = datetime.datetime(2026, 1, 1)

        supply = source.decode(Log(comet, [TOPIC_V3_SUPPLY_COLLATERAL, topic_address(account), topic_address(account), topic_address(asset)], "0x" + word(1_000)), {100: ts})
        source._apply_tx([supply])
        self.assertEqual(source._collateral[(comet, asset)], 1_000)

        recipient = "0x2222222222222222222222222222222222222222"
        transfer = source.decode(
            Log(
                comet,
                [TOPIC_V3_TRANSFER_COLLATERAL, topic_address(account), topic_address(recipient), topic_address(asset)],
                "0x" + word(400),
            ),
            {100: ts},
        )
        source._apply_tx([transfer])
        self.assertEqual(source._collateral[(comet, asset)], 1_000)

        buy = source.decode(Log(comet, [TOPIC_V3_BUY_COLLATERAL, topic_address(account), topic_address(asset)], "0x" + word(100) + word(250)), {100: ts})
        source._apply_tx([buy])
        self.assertEqual(source._collateral[(comet, asset)], 1_000)

        sale_withdraw = source.decode(
            Log(
                comet,
                [TOPIC_V3_WITHDRAW_COLLATERAL, topic_address(account), topic_address(account), topic_address(asset)],
                "0x" + word(125),
            ),
            {100: ts},
        )
        sale_buy = source.decode(Log(comet, [TOPIC_V3_BUY_COLLATERAL, topic_address(account), topic_address(asset)], "0x" + word(50) + word(125)), {100: ts})
        source._apply_tx([sale_withdraw, sale_buy])
        self.assertEqual(source._collateral[(comet, asset)], 1_000)

        withdraw = source.decode(
            Log(
                comet,
                [TOPIC_V3_WITHDRAW_COLLATERAL, topic_address(account), topic_address(account), topic_address(asset)],
                "0x" + word(250),
            ),
            {100: ts},
        )
        source._apply_tx([withdraw])
        self.assertEqual(source._collateral[(comet, asset)], 750)

    def test_v3_latest_upsert_uses_raw_metrics_before_forward_fill(self):
        source = CompoundV3Source()
        comet = COMPOUND_V3_USDC
        base_token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
        source._comets[comet] = CompoundV3Comet(comet, "cUSDCv3", base_token, "USDC", 6)
        raw_ts = pd.Timestamp("2026-01-01 01:23:00")
        raw_metric = {
            "timestamp": raw_ts,
            "entity_id": comet,
            "symbol": "USDC",
            "base_token": base_token,
            "supply_usd": 333_000_000.0,
            "borrow_usd": 291_000_000.0,
            "supply_apy": 0.0315,
            "borrow_apy": 0.0393,
            "utilization": 0.874,
            "price_usd": 1.0,
        }
        decoded_rows = [
            {
                "block_number": 1,
                "tx_hash": "0x1",
                "log_index": 0,
                "comet": comet,
                "timestamp": raw_ts.to_pydatetime(),
            }
        ]
        latest_frames = []

        def stale_forward_fill(frame, _ch, protocol, compound=False):
            self.assertEqual(protocol, COMPOUND_V3_MARKET)
            self.assertFalse(compound)
            filled = frame.copy()
            filled["timestamp"] = pd.Timestamp("2026-01-01 02:00:00")
            filled["supply_apy"] = 0.01
            filled["borrow_apy"] = 0.02
            return filled

        def capture_latest(_ch, frame):
            latest_frames.append(frame.copy())
            return len(frame)

        with patch.object(source, "_apply_tx", return_value=raw_metric), \
            patch.object(source, "_persist_state"), \
            patch("analytics.sources.compound.insert_df_batched"), \
            patch("analytics.sources.compound.forward_fill_hourly", side_effect=stale_forward_fill), \
            patch("analytics.sources.compound.upsert_market_timeseries"), \
            patch("analytics.sources.compound.upsert_api_market_latest", side_effect=capture_latest), \
            patch("analytics.sources.compound.refresh_api_protocol_tvl_weekly"), \
            patch("analytics.sources.compound.rewrite_protocol_window_if_enabled"):
            row_count = source.merge(None, decoded_rows)

        self.assertEqual(row_count, 1)
        self.assertEqual(len(latest_frames), 1)
        latest = latest_frames[0]
        self.assertEqual(latest["timestamp"].tolist(), [raw_ts])
        self.assertEqual(latest["supply_apy"].tolist(), [0.0315])
        self.assertEqual(latest["borrow_apy"].tolist(), [0.0393])
        self.assertEqual(latest["target_id"].tolist(), [base_token])

if __name__ == "__main__":
    unittest.main()
