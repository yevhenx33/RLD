import datetime
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.aave_accounts import (  # noqa: E402
    AAVE_V3_POOL,
    RAY,
    TOPIC_BALANCE_TRANSFER,
    TOPIC_RESERVE_USED_AS_COLLATERAL_DISABLED,
    TOPIC_RESERVE_USED_AS_COLLATERAL_ENABLED,
    TOPIC_TOKEN_BURN,
    TOPIC_TOKEN_MINT,
    TOPIC_USER_EMODE_SET,
    ReserveToken,
    decoded_account_rows,
    ray_div_signed,
)


USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ATOKEN = "0x1111111111111111111111111111111111111111"
VTOKEN = "0x2222222222222222222222222222222222222222"
ALICE = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BOB = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CALLER = "0xcccccccccccccccccccccccccccccccccccccccc"


class FakeLog:
    def __init__(self, address, topics, data="0x", block_number=10, log_index=1):
        self.address = address
        self.topics = topics
        self.data = data
        self.block_number = block_number
        self.log_index = log_index
        self.transaction_hash = "0xabc"


def topic_address(address: str) -> str:
    return "0x" + ("0" * 24) + address[2:].lower()


def words(*values: int) -> str:
    return "0x" + "".join(f"{value:064x}" for value in values)


def registry():
    token = ReserveToken(USDC, ATOKEN, VTOKEN, "USDC", 6)
    return {
        ATOKEN.lower(): ("ATOKEN", USDC, token),
        VTOKEN.lower(): ("VARIABLE_DEBT", USDC, token),
    }


class AaveAccountReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.block_ts = {10: datetime.datetime(2026, 1, 1)}

    def test_ray_div_signed_uses_aave_half_up_rounding(self):
        self.assertEqual(ray_div_signed(5 * RAY, RAY), 5 * RAY)
        self.assertEqual(ray_div_signed(-5 * RAY, RAY), -5 * RAY)
        self.assertEqual(ray_div_signed(1, 2), 500000000000000000000000000)

    def test_decodes_token_mint_as_net_scaled_delta(self):
        log = FakeLog(
            ATOKEN,
            [TOPIC_TOKEN_MINT, topic_address(CALLER), topic_address(ALICE)],
            words(1100, 100, RAY),
        )
        rows = decoded_account_rows(log, self.block_ts, registry())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_name"], "Mint")
        self.assertEqual(rows[0]["user"], ALICE)
        self.assertEqual(rows[0]["token_type"], "ATOKEN")
        self.assertEqual(rows[0]["scaled_delta_raw"], 1000)

    def test_decodes_token_burn_as_negative_net_scaled_delta(self):
        log = FakeLog(
            VTOKEN,
            [TOPIC_TOKEN_BURN, topic_address(ALICE), topic_address(CALLER)],
            words(900, 100, RAY),
        )
        rows = decoded_account_rows(log, self.block_ts, registry())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_name"], "Burn")
        self.assertEqual(rows[0]["token_type"], "VARIABLE_DEBT")
        self.assertEqual(rows[0]["scaled_delta_raw"], -1000)

    def test_decodes_atoken_balance_transfer_as_two_scaled_deltas(self):
        log = FakeLog(
            ATOKEN,
            [TOPIC_BALANCE_TRANSFER, topic_address(ALICE), topic_address(BOB)],
            words(250, RAY),
        )
        rows = decoded_account_rows(log, self.block_ts, registry())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user"], ALICE)
        self.assertEqual(rows[0]["scaled_delta_raw"], -250)
        self.assertEqual(rows[1]["user"], BOB)
        self.assertEqual(rows[1]["scaled_delta_raw"], 250)

    def test_decodes_collateral_and_emode_pool_events(self):
        enabled = decoded_account_rows(
            FakeLog(
                AAVE_V3_POOL,
                [TOPIC_RESERVE_USED_AS_COLLATERAL_ENABLED, topic_address(USDC), topic_address(ALICE)],
            ),
            self.block_ts,
            registry(),
        )
        disabled = decoded_account_rows(
            FakeLog(
                AAVE_V3_POOL,
                [TOPIC_RESERVE_USED_AS_COLLATERAL_DISABLED, topic_address(USDC), topic_address(ALICE)],
            ),
            self.block_ts,
            registry(),
        )
        emode = decoded_account_rows(
            FakeLog(AAVE_V3_POOL, [TOPIC_USER_EMODE_SET, topic_address(ALICE)], words(1)),
            self.block_ts,
            registry(),
        )
        self.assertEqual(enabled[0]["collateral_enabled"], 1)
        self.assertEqual(disabled[0]["collateral_enabled"], 0)
        self.assertEqual(emode[0]["emode_category"], 1)


if __name__ == "__main__":
    unittest.main()

