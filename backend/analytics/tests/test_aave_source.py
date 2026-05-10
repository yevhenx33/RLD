import datetime
import sys
import unittest
from pathlib import Path

from eth_abi import encode as abi_encode
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.aave_constants import (  # noqa: E402
    AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED,
    AAVE_TOPIC_RESERVE_INITIALIZED,
    AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED,
    AAVE_TOPIC_RESERVE_DATA_UPDATED,
)
from analytics.sources.aave_v3 import AaveReserveState, AaveV3Source, SparkLendSource, RAY  # noqa: E402


WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"


class FakeLog:
    def __init__(self, topic0, topic1, data, block_number=1):
        self.topics = [topic0, topic1]
        self.data = data
        self.block_number = block_number


def topic_address(address: str) -> str:
    return "0x" + ("0" * 24) + address[2:].lower()


def topic_uint(value: int) -> str:
    return "0x" + f"{value:064x}"


def words(*values: int) -> str:
    return "0x" + "".join(f"{value:064x}" for value in values)


class AaveV3SourceRiskConfigTests(unittest.TestCase):
    def test_config_and_emode_state_are_attached_to_rate_rows(self):
        source = AaveV3Source()
        block_ts = {4: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)}

        source.decode(
            FakeLog(
                AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED,
                topic_address(WETH),
                words(8250, 8600, 10500, 1000, 1, 1, 0, 1, 0),
                block_number=1,
            ),
            {},
        )
        source.decode(
            FakeLog(
                AAVE_TOPIC_EMODE_CATEGORY_ADDED,
                topic_uint(1),
                "0x"
                + abi_encode(
                    ["uint16", "uint16", "uint16", "address", "string"],
                    [9700, 9750, 10100, "0x0000000000000000000000000000000000000000", "ETH correlated"],
                ).hex(),
                block_number=2,
            ),
            {},
        )
        source.decode(
            FakeLog(
                AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED,
                topic_address(WETH),
                words(0, 1),
                block_number=3,
            ),
            {},
        )

        row = source.decode(
            FakeLog(
                AAVE_TOPIC_RESERVE_DATA_UPDATED,
                topic_address(WETH),
                words(int(0.03 * RAY), 0, int(0.06 * RAY), RAY, RAY),
                block_number=4,
            ),
            block_ts,
        )

        self.assertIsNotNone(row)
        self.assertEqual(row["entity_id"], WETH)
        self.assertAlmostEqual(row["ltv"], 0.825)
        self.assertAlmostEqual(row["liquidation_threshold"], 0.86)
        self.assertAlmostEqual(row["liquidation_penalty"], 0.05)
        self.assertEqual(row["e_mode_category"], 1)
        self.assertAlmostEqual(row["e_mode_ltv"], 0.97)
        self.assertAlmostEqual(row["e_mode_liquidation_threshold"], 0.975)
        self.assertAlmostEqual(row["e_mode_liquidation_penalty"], 0.01)
        self.assertEqual(row["e_mode_label"], "ETH correlated")


class ReconciledAaveV3Source(AaveV3Source):
    def __init__(self, scaled_values):
        super().__init__()
        self.scaled_values = scaled_values

    def _rpc_url(self) -> str:
        return "https://rpc.example"

    def _reserve_token_map(self, ch, entity_ids):
        return {WETH: ("0xatoken", "0xdebt")}

    def _rpc_scaled_total_supply(self, rpc_url: str, token_address: str, block_number: int) -> int:
        return self.scaled_values[(token_address, block_number)]


class AaveV3SourceReconciliationTests(unittest.TestCase):
    def test_reconcile_scaled_totals_updates_row_and_state(self):
        source = ReconciledAaveV3Source({
            ("0xatoken", 123): 2_000,
            ("0xdebt", 123): 1_500,
        })
        state = source._reserves[WETH]
        state.liquidity_index = 2 * RAY
        state.variable_borrow_index = 3 * RAY

        frame = pd.DataFrame([{
            "block_number": 123,
            "entity_id": WETH,
            "total_supply": 0.0,
            "total_borrow": 0.0,
            "utilization": 0.0,
        }])

        reconciled = source._reconcile_scaled_totals(None, frame)

        self.assertEqual(state.total_scaled_supply, 2_000.0)
        self.assertEqual(state.total_scaled_borrow, 1_500.0)
        self.assertEqual(reconciled.loc[0, "total_supply"], 4_000.0)
        self.assertAlmostEqual(reconciled.loc[0, "total_borrow"], 4_500.0)
        self.assertEqual(reconciled.loc[0, "utilization"], 1.0)

    def test_apply_current_scaled_state_only_updates_latest_hour(self):
        source = ReconciledAaveV3Source({})
        state = source._reserves[WETH]
        state.total_scaled_supply = 2_000
        state.total_scaled_borrow = 1_000
        state.liquidity_index = 2 * RAY
        state.variable_borrow_index = 3 * RAY

        frame = pd.DataFrame([
            {
                "timestamp": datetime.datetime(2026, 1, 1, 1),
                "entity_id": WETH,
                "symbol": "WETH",
                "supply_usd": 1.0,
                "borrow_usd": 1.0,
                "utilization": 1.0,
                "price_usd": 10.0,
            },
            {
                "timestamp": datetime.datetime(2026, 1, 1, 2),
                "entity_id": WETH,
                "symbol": "WETH",
                "supply_usd": 1.0,
                "borrow_usd": 1.0,
                "utilization": 1.0,
                "price_usd": 10.0,
            },
        ])

        updated = source._apply_current_scaled_state(frame, eth_price=10.0, btc_price=0.0)

        self.assertEqual(updated.loc[0, "supply_usd"], 1.0)
        self.assertAlmostEqual(updated.loc[1, "supply_usd"], 4_000 * 10 / 10**18)
        self.assertAlmostEqual(updated.loc[1, "borrow_usd"], 3_000 * 10 / 10**18)
        self.assertAlmostEqual(updated.loc[1, "utilization"], 0.75)


class SparkLendSourceTests(unittest.TestCase):
    def test_spark_source_emits_spark_protocol_rows_without_aave_tables(self):
        source = SparkLendSource()
        block_ts = {1: datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)}
        state = source._reserves.setdefault(WETH, AaveReserveState())
        state.total_scaled_supply = 2_000
        state.total_scaled_borrow = 1_000

        row = source.decode(
            FakeLog(
                AAVE_TOPIC_RESERVE_DATA_UPDATED,
                topic_address(WETH),
                words(int(0.03 * RAY), 0, int(0.06 * RAY), RAY, RAY),
                block_number=1,
            ),
            block_ts,
        )

        self.assertIsNotNone(row)
        self.assertEqual(source.name, "SPARK_MARKET")
        self.assertEqual(source.raw_table, "spark_events")
        self.assertEqual(source.output_table, "spark_timeseries")
        self.assertEqual(source.scaled_state_table, "spark_scaled_state")
        self.assertEqual(source.risk_state_table, "spark_reserve_risk_state")
        self.assertIn(AAVE_TOPIC_RESERVE_INITIALIZED, source.topics)
        self.assertIsNone(
            source.decode(
                FakeLog(
                    AAVE_TOPIC_RESERVE_INITIALIZED,
                    topic_address(WETH),
                    words(0, 0, 0),
                    block_number=1,
                ),
                block_ts,
            )
        )
        self.assertEqual(row["entity_id"], WETH)
        self.assertFalse(source._rpc_reconcile_enabled)


if __name__ == "__main__":
    unittest.main()
