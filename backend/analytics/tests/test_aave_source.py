import datetime
import sys
import unittest
from pathlib import Path

from eth_abi import encode as abi_encode


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.aave_constants import (  # noqa: E402
    AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED,
    AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED,
    AAVE_TOPIC_RESERVE_DATA_UPDATED,
)
from analytics.sources.aave_v3 import AaveV3Source, RAY  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
