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

from analytics.sources.fluid import (  # noqa: E402
    EVENT_MAP,
    TOPIC_LOG_OPERATE,
    TOPIC_LOG_UPDATE_EXCHANGE_PRICES,
    FluidReserveState,
    FluidSource,
    bigmath,
)

USDC = "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


class FakeLog:
    def __init__(self, topic0, data, topics=None, block=100):
        self.block_number = block
        self.transaction_hash = "0xhash"
        self.log_index = 0
        self.address = "0x52aa899454998be5b000ad077a46bbe360f4e497"
        self.topics = topics or [topic0]
        self.data = data


def word(value: int) -> str:
    return f"{value:064x}"


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


def data_words(*values: int) -> str:
    return "0x" + "".join(word(v) for v in values)


def pack_bigmath(value: int, exponent: int = 0) -> int:
    return (value << 8) | exponent


class FluidSourceTests(unittest.TestCase):
    def test_event_mapping_includes_operate_and_exchange_price_updates(self):
        self.assertEqual(EVENT_MAP[TOPIC_LOG_OPERATE], "LogOperate")
        self.assertEqual(EVENT_MAP[TOPIC_LOG_UPDATE_EXCHANGE_PRICES], "LogUpdateExchangePrices")

    def test_bigmath_decodes_coefficient_and_exponent(self):
        self.assertEqual(bigmath(pack_bigmath(123, 0)), 123)
        self.assertEqual(bigmath(pack_bigmath(123, 2)), 492)

    def test_log_operate_decodes_absolute_reserve_snapshot(self):
        source = FluidSource()
        ts = dt.datetime(2026, 1, 1, 12)
        w4 = (
            pack_bigmath(1_000_000_000)
            | (pack_bigmath(25_000_000) << 64)
            | (pack_bigmath(400_000_000) << 128)
            | (pack_bigmath(10_000_000) << 192)
        )
        w5 = (int(1e12) << 91) | (int(1e12) << 155) | (6_000 << 30) | (500 << 16) | 450
        log = FakeLog(
            TOPIC_LOG_OPERATE,
            data_words(0, 0, 0, 0, w4, w5),
            topics=[TOPIC_LOG_OPERATE, word(0), address_topic(USDC)],
        )

        decoded = source.decode(log, {100: ts})
        state = source._states["0x" + USDC]

        self.assertEqual(decoded["kind"], "reserve_snapshot")
        self.assertEqual(decoded["event_name"], "LogOperate")
        self.assertEqual(state.symbol, "USDC")
        self.assertAlmostEqual(state.total_supply_tokens, 1025.0)
        self.assertAlmostEqual(state.total_borrow_tokens, 410.0)
        self.assertAlmostEqual(state.utilization, 0.6)
        self.assertAlmostEqual(state.borrow_apy, 0.045)
        self.assertAlmostEqual(state.supply_apy, 0.045 * 0.6 * 0.95)

    def test_log_update_exchange_prices_updates_rates_without_balances(self):
        source = FluidSource()
        token = "0x" + USDC
        source._states[token] = FluidReserveState(
            token=token,
            symbol="USDC",
            decimals=6,
            total_supply_tokens=100.0,
            total_borrow_tokens=50.0,
            fee=0.1,
        )
        ts = dt.datetime(2026, 1, 1, 13)
        log = FakeLog(
            TOPIC_LOG_UPDATE_EXCHANGE_PRICES,
            data_words(50_000_000_000_000_000, 700_000_000_000_000_000),
            topics=[TOPIC_LOG_UPDATE_EXCHANGE_PRICES, address_topic(USDC), word(10**12 + 1), word(10**12 + 2)],
            block=101,
        )

        decoded = source.decode(log, {101: ts})
        state = source._states[token]

        self.assertEqual(decoded["event_name"], "LogUpdateExchangePrices")
        self.assertEqual(state.total_supply_tokens, 100.0)
        self.assertEqual(state.total_borrow_tokens, 50.0)
        self.assertAlmostEqual(state.borrow_apy, 0.05)
        self.assertAlmostEqual(state.utilization, 0.7)
        self.assertAlmostEqual(state.supply_apy, 0.05 * 0.7 * 0.9)
        self.assertEqual(state.supply_exchange_price, str(10**12 + 1))

    def test_fluid_full_coverage_helpers_require_explicit_snapshots(self):
        from analytics.fluid_full_coverage import needs_explicit_snapshot, support_hint

        self.assertTrue(needs_explicit_snapshot("LBTC"))
        self.assertTrue(needs_explicit_snapshot("sUSDe"))
        self.assertIn("share rate", support_hint("sUSDe"))
        self.assertFalse(needs_explicit_snapshot("USDC"))

    def test_fluid_product_feed_aliases_use_explicit_chainlink_feeds(self):
        from analytics.scripts.backfill_fluid_product_snapshots import resolve_fluid_feed_price

        feeds = {
            "XAU / USD": 2400.0,
            "TBTC / USD": 64000.0,
            "LBTC / BTC": 1.01,
            "BTC / USD": 65000.0,
        }

        self.assertEqual(resolve_fluid_feed_price("XAUt", feeds), (2400.0, "CHAINLINK:XAU / USD"))
        self.assertEqual(resolve_fluid_feed_price("tBTC", feeds), (64000.0, "CHAINLINK:TBTC / USD"))
        self.assertEqual(resolve_fluid_feed_price("LBTC", feeds), (65650.0, "CHAINLINK:LBTC / BTC*BTC / USD"))
        self.assertEqual(resolve_fluid_feed_price("GHO", feeds), (0.0, ""))

    def test_chainlink_proxy_feed_registry_contains_verified_fluid_assets(self):
        from analytics.scripts.backfill_fluid_product_snapshots import CHAINLINK_PROXY_FEEDS

        self.assertEqual(CHAINLINK_PROXY_FEEDS["GHO"]["feed"], "GHO / USD")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["USDTB"]["feed"], "USDtb / USD")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["USR"]["feed"], "USR / USD")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["METH"]["quote"], "ETH")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["EZETH"]["feed"], "ezETH / ETH")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["RSETH"]["feed"], "rsETH / ETH Exchange Rate")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["DEUSD"]["feed"], "deUSD / USD")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["EBTC"]["method"], "latestAnswer")
        self.assertEqual(CHAINLINK_PROXY_FEEDS["OSETH"]["quote"], "ETH")

    def test_rate_provider_registry_contains_weeths(self):
        from analytics.scripts.backfill_fluid_product_snapshots import RATE_PROVIDER_FEEDS

        self.assertEqual(RATE_PROVIDER_FEEDS["WEETHS"]["feed"], "Ether.fi weETHs Accountant / ETH")
        self.assertEqual(RATE_PROVIDER_FEEDS["WEETHS"]["method"], "getRate")

    def test_strict_price_support_classification(self):
        source = FluidSource()
        source._available_feeds = {"USDC / USD", "BTC / USD", "USDe / USD"}

        status, feeds, reason = source._support_for_symbol("USDC")
        self.assertEqual(status, "CHAINLINK_SUPPORTED")
        self.assertEqual(feeds, ("USDC / USD",))
        self.assertEqual(reason, "")

        status, feeds, reason = source._support_for_symbol("LBTC")
        self.assertEqual(status, "ORACLE_SNAPSHOT_REQUIRED")
        self.assertIn("BTC", reason)

        status, feeds, reason = source._support_for_symbol("sUSDe")
        self.assertEqual(status, "ORACLE_SNAPSHOT_REQUIRED")
        self.assertIn("share rate", reason)

        status, feeds, reason = source._support_for_symbol("ezETH")
        self.assertEqual(status, "ORACLE_SNAPSHOT_REQUIRED")
        self.assertIn("Renzo", reason)


if __name__ == "__main__":
    unittest.main()
