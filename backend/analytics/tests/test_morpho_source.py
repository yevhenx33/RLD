import datetime as dt
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hypersync" not in sys.modules:
    hypersync_stub = types.ModuleType("hypersync")
    hypersync_stub.LogSelection = lambda **kwargs: kwargs
    sys.modules["hypersync"] = hypersync_stub

from analytics.morpho_oracle_snapshots import collateral_value_usd_from_oracle  # noqa: E402
from analytics.sources import morpho as morpho_module  # noqa: E402
from analytics.sources.morpho import (  # noqa: E402
    EVENT_MAP,
    TOPIC_CREATE_MARKET,
    TOPIC_SET_AUTHORIZATION,
    TOPIC_SET_FEE,
    TOPIC_SUPPLY,
    TOPIC_SUPPLY_COLLATERAL,
    TOPIC_WITHDRAW_COLLATERAL,
    MorphoMarketParams,
    MorphoMarketState,
    MorphoSource,
    classify_price_support,
    price_feed_requirements,
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


class FakeQueryResult:
    def __init__(self, rows=None):
        self.result_rows = rows or []


class FakeClickHouse:
    def __init__(self, price_rows=None):
        self.price_rows = price_rows or []

    def command(self, _query):
        return 0

    def query(self, _query):
        return FakeQueryResult()

    def query_df(self, _query):
        return pd.DataFrame(self.price_rows)


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

    def test_resolver_and_classifier_agree_for_pricing_aliases(self):
        cases = {
            "LBTC": ({"LBTC / BTC": 1.01, "BTC / USD": 65_000.0}, 65_650.0),
            "tBTC": ({"TBTC / USD": 64_000.0}, 64_000.0),
            "cbETH": ({"CBETH / ETH": 1.04, "ETH / USD": 3_000.0}, 3_120.0),
            "lsETH": ({"LsETH / ETH Exchange Rate": 1.05, "ETH / USD": 3_000.0}, 3_150.0),
            "XAUt": ({"XAU / USD": 2_400.0}, 2_400.0),
            "USD0pp": ({"USD0++ / USD": 1.0}, 1.0),
            "crvUSD": ({"CRVUSD / USD": 1.0}, 1.0),
            "frxUSD": ({"frxUSD / USD": 1.0}, 1.0),
            "wstETH": ({"wstETH/stETH exchange rate": 1.2, "STETH / USD": 3_100.0}, 3_720.0),
        }
        loan_feed = {"USDC / USD": 1.0}

        for symbol, (collateral_feed_prices, expected_price) in cases.items():
            with self.subTest(symbol=symbol):
                available = set(loan_feed) | set(collateral_feed_prices)
                status, loan_feeds, collateral_feeds, reason = classify_price_support("USDC", symbol, available)

                self.assertEqual(status, "CHAINLINK_SUPPORTED", reason)
                self.assertEqual(set(loan_feeds), set(loan_feed))
                self.assertEqual(set(collateral_feeds), set(collateral_feed_prices))

                classifier_prices = {
                    feed: {**loan_feed, **collateral_feed_prices}[feed]
                    for feed in (*loan_feeds, *collateral_feeds)
                }
                resolved_price = resolve_symbol_price(symbol, classifier_prices)
                self.assertIsNotNone(resolved_price)
                self.assertAlmostEqual(resolved_price, expected_price)

    def test_missing_loan_feed_is_not_oracle_snapshot_supported(self):
        source = MorphoSource()
        source._available_feeds = set()
        params = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="UNKNOWN_COLLATERAL",
            oracle="0x" + ORACLE,
        )

        status, _loan_feeds, _collateral_feeds, _reason = source._support_for(params)

        self.assertNotEqual(status, "ORACLE_SNAPSHOT_SUPPORTED")

    def test_zero_metric_rows_are_kept_before_serving_filter(self):
        source = MorphoSource()
        source._available_feeds = {"USDC / USD", "ETH / USD"}
        source._params[MARKET_ID] = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="WETH",
            loan_decimals=6,
            collateral_decimals=18,
            oracle="0x" + ORACLE,
            lltv=860_000_000_000_000_000,
        )
        source._markets[MARKET_ID] = MorphoMarketState()
        ts = dt.datetime(2026, 1, 1, 12)
        ch = FakeClickHouse(
            [
                {"ts": ts, "feed": "USDC / USD", "price": 1.0},
                {"ts": ts, "feed": "ETH / USD", "price": 3_000.0},
            ]
        )
        inserts = []

        def capture_insert(_ch, table, df, *args, **kwargs):
            inserts.append((table, df.copy()))

        with patch.object(morpho_module, "insert_df_batched", capture_insert),              patch.object(morpho_module, "upsert_market_timeseries", lambda *_args, **_kwargs: 0),              patch.object(morpho_module, "upsert_api_market_latest", lambda *_args, **_kwargs: 0),              patch.object(morpho_module, "refresh_api_protocol_tvl_weekly", lambda *_args, **_kwargs: 0),              patch.object(morpho_module, "rewrite_protocol_window_if_enabled", lambda *_args, **_kwargs: 0):
            written = source._write_snapshots(
                ch,
                [{"kind": "snapshot", "market_id": MARKET_ID, "block_number": 100, "timestamp": ts}],
            )

        self.assertGreater(written, 0)
        metrics = [df for table, df in inserts if table == "morpho_market_metrics"]
        served = [df for table, df in inserts if table == source.output_table]
        self.assertEqual(len(metrics), 1)
        self.assertEqual(len(metrics[0]), 1)
        self.assertEqual(len(served), 1)
        row = metrics[0].iloc[0]
        self.assertEqual(row["supply_usd"], 0.0)
        self.assertEqual(row["borrow_usd"], 0.0)
        self.assertEqual(row["collateral_usd"], 0.0)
        self.assertTrue(((served[0]["supply_usd"] == 0.0) & (served[0]["borrow_usd"] == 0.0)).all())


    def test_resolver_aliases_cover_non_usd_paths(self):
        feed_prices = {
            "BTC / USD": 100_000.0,
            "LBTC / BTC": 1.01,
            "TBTC / USD": 99_500.0,
            "ETH / USD": 3_000.0,
            "CBETH / ETH": 1.05,
            "LsETH / ETH Exchange Rate": 1.03,
            "XAU / USD": 2_400.0,
            "USD0++ / USD": 0.99,
            "CRVUSD / USD": 1.0,
            "frxUSD / USD": 1.0,
            "wstETH/stETH exchange rate": 1.2,
            "STETH / USD": 3_100.0,
        }
        self.assertEqual(price_feed_requirements("LBTC", set(feed_prices)), ("LBTC / BTC", "BTC / USD"))
        self.assertAlmostEqual(resolve_symbol_price("LBTC", feed_prices), 101_000.0)
        self.assertAlmostEqual(resolve_symbol_price("tBTC", feed_prices), 99_500.0)
        self.assertAlmostEqual(resolve_symbol_price("cbETH", feed_prices), 3_150.0)
        self.assertAlmostEqual(resolve_symbol_price("lsETH", feed_prices), 3_090.0)
        self.assertAlmostEqual(resolve_symbol_price("XAUt", feed_prices), 2_400.0)
        self.assertAlmostEqual(resolve_symbol_price("USD0pp", feed_prices), 0.99)
        self.assertAlmostEqual(resolve_symbol_price("crvUSD", feed_prices), 1.0)
        self.assertAlmostEqual(resolve_symbol_price("FRXUSD", feed_prices), 1.0)
        self.assertAlmostEqual(resolve_symbol_price("wstETH", feed_prices), 3_720.0)

    def test_classifier_and_resolver_agree_for_supported_aliases(self):
        feeds = {
            "BTC / USD",
            "LBTC / BTC",
            "TBTC / USD",
            "ETH / USD",
            "CBETH / ETH",
            "LsETH / ETH Exchange Rate",
            "XAU / USD",
            "USD0++ / USD",
            "CRVUSD / USD",
            "frxUSD / USD",
            "USDC / USD",
        }
        for symbol in ("LBTC", "tBTC", "cbETH", "lsETH", "XAUt", "USD0pp", "crvUSD", "FRXUSD"):
            with self.subTest(symbol=symbol):
                status, loan_feeds, collateral_feeds, reason = classify_price_support("USDC", symbol, feeds)
                self.assertEqual(status, "CHAINLINK_SUPPORTED", reason)
                probe = {feed: 1.0 for feed in (*loan_feeds, *collateral_feeds)}
                self.assertIsNotNone(resolve_symbol_price("USDC", probe))
                self.assertIsNotNone(resolve_symbol_price(symbol, probe))

    def test_oracle_snapshot_support_requires_loan_usd_conversion(self):
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        params = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDTB",
            collateral_symbol="UNKNOWN_COLLATERAL",
            oracle="0x" + ORACLE,
        )
        status, loan_feeds, collateral_feeds, reason = source._support_for(params)
        self.assertEqual(status, "UNPRICED")
        self.assertEqual(loan_feeds, ("USDTB / USD",))
        self.assertIn("missing loan-side USD conversion feed", reason)

    def test_oracle_snapshot_support_when_loan_is_priced_and_collateral_uses_oracle(self):
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        params = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="UNKNOWN_COLLATERAL",
            oracle="0x" + ORACLE,
        )
        status, loan_feeds, collateral_feeds, reason = source._support_for(params)
        self.assertEqual(status, "ORACLE_SNAPSHOT_SUPPORTED")
        self.assertEqual(loan_feeds, ("USDC / USD",))
        self.assertEqual(collateral_feeds, ())
        self.assertIn("IOracle.price", reason)

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


class MorphoOracleSnapshotTests(unittest.TestCase):
    def test_collateral_value_usd_from_oracle_uses_morpho_scale(self):
        # 10 WETH collateral, USDC loan, ETH at $2,000.
        collateral_raw = 10 * 10**18
        oracle_price_raw = 2_000 * 10**24
        value = collateral_value_usd_from_oracle(collateral_raw, oracle_price_raw, 6, 1.0)
        self.assertAlmostEqual(value, 20_000.0)

    def test_collateral_value_usd_from_oracle_rejects_bad_values(self):
        self.assertIsNone(collateral_value_usd_from_oracle(1, "not-int", 6, 1.0))
        self.assertEqual(collateral_value_usd_from_oracle(0, 10**36, 6, 1.0), 0.0)


if __name__ == "__main__":
    unittest.main()
