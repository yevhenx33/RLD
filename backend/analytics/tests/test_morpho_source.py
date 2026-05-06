import datetime as dt
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hypersync" not in sys.modules:
    hypersync_stub = types.ModuleType("hypersync")
    hypersync_stub.LogSelection = lambda **kwargs: kwargs
    sys.modules["hypersync"] = hypersync_stub

from analytics.sources.morpho import (  # noqa: E402
    EVENT_MAP,
    TOPIC_CREATE_MARKET,
    TOPIC_SET_AUTHORIZATION,
    TOPIC_SET_FEE,
    TOPIC_SUPPLY,
    TOPIC_SUPPLY_COLLATERAL,
    TOPIC_WITHDRAW_COLLATERAL,
    MorphoSource,
    classify_price_support,
    resolve_symbol_price,
)


USDC = "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
WETH = "c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
ORACLE = "1111111111111111111111111111111111111111"
IRM = "2222222222222222222222222222222222222222"
MARKET_ID = "0x" + "ab" * 32


class FakeLog:
    def __init__(self, topic0, data, block=100, topics=None):
        self.block_number = block
        self.transaction_hash = "0xhash"
        self.log_index = 0
        self.address = "0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb"
        self.topics = topics or [topic0, MARKET_ID]
        self.data = data


def word(value: int) -> str:
    return f"{value:064x}"


def address_word(address: str) -> str:
    return "0" * 24 + address.lower().removeprefix("0x")


def data_words(*values: str | int) -> str:
    encoded = []
    for value in values:
        encoded.append(word(value) if isinstance(value, int) else value)
    return "0x" + "".join(encoded)


class MorphoSourceTests(unittest.TestCase):
    def test_event_mapping_uses_correct_set_fee_topic(self):
        self.assertEqual(EVENT_MAP[TOPIC_SET_FEE], "SetFee")
        self.assertNotIn(TOPIC_SET_AUTHORIZATION, EVENT_MAP)

    def test_create_market_decodes_params_and_token_metadata(self):
        source = MorphoSource()
        ts = dt.datetime(2026, 1, 1, 12)
        log = FakeLog(
            TOPIC_CREATE_MARKET,
            data_words(
                address_word(USDC),
                address_word(WETH),
                address_word(ORACLE),
                address_word(IRM),
                860_000_000_000_000_000,
            ),
        )

        decoded = source.decode(log, {100: ts})
        params = source._params[MARKET_ID]

        self.assertEqual(decoded, {"kind": "market_params", "market_id": MARKET_ID})
        self.assertEqual(params.loan_symbol, "USDC")
        self.assertEqual(params.loan_decimals, 6)
        self.assertEqual(params.collateral_symbol, "WETH")
        self.assertEqual(params.collateral_decimals, 18)
        self.assertEqual(params.oracle, "0x" + ORACLE)
        self.assertEqual(params.lltv, 860_000_000_000_000_000)

    def test_collateral_events_update_market_state(self):
        source = MorphoSource()
        ts = dt.datetime(2026, 1, 1, 12)

        supplied = source.decode(
            FakeLog(TOPIC_SUPPLY_COLLATERAL, data_words(5 * 10**18)),
            {100: ts},
        )
        withdrawn = source.decode(
            FakeLog(TOPIC_WITHDRAW_COLLATERAL, data_words(address_word("33" * 20), 2 * 10**18), block=101),
            {101: ts},
        )

        self.assertEqual(supplied["kind"], "snapshot")
        self.assertEqual(withdrawn["kind"], "snapshot")
        self.assertEqual(source._markets[MARKET_ID].collateral_assets, 3 * 10**18)

    def test_supply_updates_assets_and_shares(self):
        source = MorphoSource()
        source.decode(
            FakeLog(TOPIC_SUPPLY, data_words(1_000_000, 999_999)),
            {100: dt.datetime(2026, 1, 1, 12)},
        )
        state = source._markets[MARKET_ID]
        self.assertEqual(state.total_supply_assets, 1_000_000)
        self.assertEqual(state.total_supply_shares, 999_999)

    def test_chainlink_support_classification(self):
        feeds = {"USDC / USD", "ETH / USD", "STETH / USD", "wstETH/stETH exchange rate"}
        status, loan_feeds, collateral_feeds, reason = classify_price_support("USDC", "WETH", feeds)

        self.assertEqual(status, "CHAINLINK_SUPPORTED")
        self.assertEqual(loan_feeds, ("USDC / USD",))
        self.assertEqual(collateral_feeds, ("ETH / USD",))
        self.assertEqual(reason, "")

        unsupported, *_ = classify_price_support("USDC", "0xunknown", feeds)
        self.assertEqual(unsupported, "UNSUPPORTED_ORACLE")

        unpriced, *_ = classify_price_support("USDC", "wstETH", {"USDC / USD", "STETH / USD"})
        self.assertEqual(unpriced, "UNPRICED")

    def test_resolve_symbol_price_never_invents_unknown_prices(self):
        self.assertEqual(resolve_symbol_price("USDC", {"USDC / USD": 1.0}), 1.0)
        self.assertEqual(resolve_symbol_price("WETH", {"ETH / USD": 3_000.0}), 3_000.0)
        self.assertEqual(
            resolve_symbol_price(
                "wstETH",
                {"wstETH/stETH exchange rate": 1.2, "STETH / USD": 3_100.0},
            ),
            3_720.0,
        )
        self.assertEqual(
            resolve_symbol_price(
                "wstETH",
                {"wstETH/stETH exchange rate": 1_200_000_000_000_000_000, "STETH / USD": 3_100.0},
            ),
            3_720.0,
        )
        self.assertIsNone(resolve_symbol_price("UNKNOWN", {"ETH / USD": 3_000.0}))


if __name__ == "__main__":
    unittest.main()
