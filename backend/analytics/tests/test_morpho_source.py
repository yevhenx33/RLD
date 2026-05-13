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
    TOPIC_BORROW,
    TOPIC_CREATE_MARKET,
    TOPIC_SET_AUTHORIZATION,
    TOPIC_SET_FEE,
    TOPIC_SUPPLY,
    TOPIC_SUPPLY_COLLATERAL,
    TOPIC_WITHDRAW_COLLATERAL,
    MorphoMarketParams,
    MorphoMarketState,
    MorphoSource,
    MorphoUserPosition,
    _project_market_assets,
    _w_taylor_compounded,
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
    def __init__(self, topic0, data, block=100, topics=None, log_index=0):
        self.block_number = block
        self.transaction_hash = "0xhash"
        self.log_index = log_index
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

    def test_borrow_and_withdraw_events_use_indexed_on_behalf_for_positions(self):
        source = MorphoSource()
        ts = dt.datetime(2026, 1, 1, 12)
        caller = "0x" + "11" * 20
        on_behalf = "0x" + "22" * 20
        receiver = "0x" + "33" * 20
        topics = [
            TOPIC_BORROW,
            MARKET_ID,
            "0x" + address_word(on_behalf),
            "0x" + address_word(receiver),
        ]

        source.decode(
            FakeLog(TOPIC_BORROW, data_words(address_word(caller), 1_000, 900), topics=topics),
            {100: ts},
        )
        event = source._event_facts[-1]

        self.assertEqual(event["caller"], caller)
        self.assertEqual(event["on_behalf"], on_behalf)
        self.assertEqual(event["receiver"], receiver)
        self.assertEqual(source._positions[(MARKET_ID, on_behalf)].borrow_shares, 900)
        self.assertNotIn((MARKET_ID, receiver), source._positions)

    def test_withdraw_collateral_uses_indexed_receiver_and_on_behalf(self):
        source = MorphoSource()
        ts = dt.datetime(2026, 1, 1, 12)
        caller = "0x" + "11" * 20
        on_behalf = "0x" + "22" * 20
        receiver = "0x" + "33" * 20
        source.decode(
            FakeLog(TOPIC_SUPPLY_COLLATERAL, data_words(10_000), topics=[TOPIC_SUPPLY_COLLATERAL, MARKET_ID, "0x" + address_word(caller), "0x" + address_word(on_behalf)]),
            {100: ts},
        )
        source.decode(
            FakeLog(
                TOPIC_WITHDRAW_COLLATERAL,
                data_words(address_word(caller), 4_000),
                block=101,
                topics=[TOPIC_WITHDRAW_COLLATERAL, MARKET_ID, "0x" + address_word(on_behalf), "0x" + address_word(receiver)],
            ),
            {101: ts},
        )
        event = source._event_facts[-1]

        self.assertEqual(event["caller"], caller)
        self.assertEqual(event["on_behalf"], on_behalf)
        self.assertEqual(event["receiver"], receiver)
        self.assertEqual(source._positions[(MARKET_ID, on_behalf)].collateral_assets, 6_000)

    def test_decoded_snapshot_rows_include_event_sourced_state(self):
        source = MorphoSource()
        first = source.decode(
            FakeLog(TOPIC_SUPPLY, data_words(1_000_000, 999_999), block=100),
            {100: dt.datetime(2026, 1, 1, 12)},
        )
        second = source.decode(
            FakeLog(TOPIC_SUPPLY, data_words(2_000_000, 1_999_999), block=200),
            {200: dt.datetime(2026, 1, 1, 13)},
        )

        self.assertEqual(first["event_name"], "Supply")
        self.assertEqual(first["total_supply_assets"], 1_000_000)
        self.assertEqual(first["total_supply_shares"], 999_999)
        self.assertEqual(second["total_supply_assets"], 3_000_000)
        self.assertEqual(second["total_supply_shares"], 2_999_998)

    def test_decode_records_event_granular_state_and_position_history(self):
        source = MorphoSource()
        ts1 = dt.datetime(2026, 1, 1, 12)
        ts2 = dt.datetime(2026, 1, 1, 13)
        caller = "0x" + "11" * 20
        on_behalf = "0x" + "22" * 20
        topics = [
            TOPIC_SUPPLY,
            MARKET_ID,
            "0x" + address_word(caller),
            "0x" + address_word(on_behalf),
        ]

        source.decode(
            FakeLog(TOPIC_SUPPLY, data_words(1_000_000, 999_999), block=100, topics=topics, log_index=7),
            {100: ts1},
        )
        source.decode(
            FakeLog(TOPIC_SUPPLY, data_words(2_000_000, 1_999_999), block=200, topics=topics, log_index=9),
            {200: ts2},
        )

        self.assertEqual(len(source._state_history_rows), 2)
        self.assertEqual(len(source._position_history_rows), 2)
        state_columns = source._state_columns()
        position_columns = source._position_columns()
        self.assertEqual(source._state_history_rows[0][state_columns.index("total_supply_assets")], "1000000")
        self.assertEqual(source._state_history_rows[0][state_columns.index("last_event_block")], 100)
        self.assertEqual(source._state_history_rows[0][state_columns.index("last_event_log_index")], 7)
        self.assertEqual(source._state_history_rows[1][state_columns.index("total_supply_assets")], "3000000")
        self.assertEqual(source._state_history_rows[1][state_columns.index("last_event_block")], 200)
        self.assertEqual(source._state_history_rows[1][state_columns.index("last_event_log_index")], 9)
        self.assertEqual(source._position_history_rows[0][position_columns.index("supply_shares")], "999999")
        self.assertEqual(source._position_history_rows[1][position_columns.index("supply_shares")], "2999998")

    def test_persist_writes_current_tables_and_event_history(self):
        source = MorphoSource()
        ts = dt.datetime(2026, 1, 1, 12)
        source._markets[MARKET_ID] = MorphoMarketState(
            total_supply_assets=1_000_000,
            total_supply_shares=999_999,
            last_event_block=123,
            last_event_log_index=7,
            last_event_timestamp=ts,
        )
        user = "0x" + "44" * 20
        source._positions[(MARKET_ID, user)] = MorphoUserPosition(
            supply_shares=999_999,
            last_event_block=123,
            last_event_log_index=7,
            last_event_timestamp=ts,
        )
        source._touched_markets.add(MARKET_ID)
        source._touched_positions.add((MARKET_ID, user))
        source._record_state_history(MARKET_ID)
        source._record_position_history((MARKET_ID, user))
        inserts = []

        def capture_insert(_ch, table, rows, column_names, *args, **kwargs):
            inserts.append((table, rows, column_names))

        with patch.object(morpho_module, "insert_rows_batched", capture_insert):
            source._persist_history(FakeClickHouse())
            source._persist_state(FakeClickHouse())
            source._persist_positions(FakeClickHouse())

        tables = [table for table, _rows, _columns in inserts]
        self.assertIn("morpho_market_state", tables)
        self.assertIn("morpho_market_state_history", tables)
        self.assertIn("morpho_market_positions", tables)
        self.assertIn("morpho_market_position_history", tables)
        for table, rows, columns in inserts:
            self.assertIn("last_event_log_index", columns)
            self.assertEqual(rows[0][columns.index("last_event_log_index")], 7)

    def test_replay_uses_each_snapshot_row_not_latest_state(self):
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
        source._markets[MARKET_ID] = MorphoMarketState(total_supply_assets=9_000_000)
        ts1 = dt.datetime(2026, 1, 1, 12)
        ts2 = dt.datetime(2026, 1, 1, 13)
        ch = FakeClickHouse(
            [
                {"ts": ts1, "feed": "USDC / USD", "price": 1.0},
                {"ts": ts1, "feed": "ETH / USD", "price": 3_000.0},
                {"ts": ts2, "feed": "USDC / USD", "price": 1.0},
                {"ts": ts2, "feed": "ETH / USD", "price": 3_000.0},
            ]
        )
        inserts = []

        def capture_insert(_ch, table, df, *args, **kwargs):
            inserts.append((table, df.copy()))

        def capture_row_insert(_ch, table, rows, columns, *args, **kwargs):
            inserts.append((table, pd.DataFrame(rows, columns=columns)))

        with patch.object(morpho_module, "insert_df_batched", capture_insert), \
             patch.object(morpho_module, "insert_rows_batched", capture_row_insert), \
             patch.object(morpho_module, "upsert_market_timeseries", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "upsert_api_market_latest", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "refresh_api_protocol_tvl_weekly", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "rewrite_protocol_window_if_enabled", lambda *_args, **_kwargs: 0):
            written = source._write_snapshots(
                ch,
                [
                    {
                        "kind": "snapshot",
                        "market_id": MARKET_ID,
                        "block_number": 100,
                        "log_index": 1,
                        "timestamp": ts1,
                        "total_supply_assets": 1_000_000,
                        "total_supply_shares": 1_000_000,
                        "total_borrow_assets": 400_000,
                        "total_borrow_shares": 400_000,
                        "collateral_assets": 0,
                        "fee_wad": 0,
                        "last_borrow_rate_wad": 0,
                        "last_update_timestamp": ts1,
                    },
                    {
                        "kind": "snapshot",
                        "market_id": MARKET_ID,
                        "block_number": 200,
                        "log_index": 1,
                        "timestamp": ts2,
                        "total_supply_assets": 2_000_000,
                        "total_supply_shares": 2_000_000,
                        "total_borrow_assets": 800_000,
                        "total_borrow_shares": 800_000,
                        "collateral_assets": 0,
                        "fee_wad": 0,
                        "last_borrow_rate_wad": 0,
                        "last_update_timestamp": ts2,
                    },
                ],
            )

        self.assertGreater(written, 0)
        metrics = pd.concat([df for table, df in inserts if table == "morpho_market_metrics"], ignore_index=True)
        metrics = metrics.sort_values("timestamp").reset_index(drop=True)
        self.assertEqual(metrics["supply_usd"].tolist(), [1.0, 2.0])
        self.assertEqual(metrics["borrow_usd"].tolist(), [0.4, 0.8])

    def test_pending_interest_projection_uses_last_update(self):
        state = MorphoMarketState(
            total_supply_assets=1_000_000_000,
            total_borrow_assets=500_000_000,
            last_borrow_rate_wad=10**9,
            last_update_timestamp=dt.datetime(2026, 1, 1, 0, 0, 0),
            last_event_timestamp=dt.datetime(2026, 1, 1, 1, 0, 0),
        )

        supply, borrow, interest = _project_market_assets(state, dt.datetime(2026, 1, 1, 2, 0, 0))

        expected_factor = _w_taylor_compounded(10**9, 7200)
        expected_interest = 500_000_000 * expected_factor // 10**18
        self.assertEqual(interest, expected_interest)
        self.assertEqual(supply, 1_000_000_000 + expected_interest)
        self.assertEqual(borrow, 500_000_000 + expected_interest)

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
            "EURCV": ({"EUR / USD": 1.17}, 1.17),
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
            "EUR / USD": 1.17,
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
        self.assertEqual(price_feed_requirements("EURCV", set(feed_prices)), ("EUR / USD",))
        self.assertAlmostEqual(resolve_symbol_price("EURCV", feed_prices), 1.17)
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
            "EUR / USD",
            "frxUSD / USD",
            "USDC / USD",
        }
        for symbol in ("LBTC", "tBTC", "cbETH", "lsETH", "XAUt", "USD0pp", "crvUSD", "EURCV", "FRXUSD"):
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

    def test_missing_collateral_feed_keeps_loan_tvl_supported(self):
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        params = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="sUSDe",
            oracle="0x" + ORACLE,
        )

        status, loan_feeds, collateral_feeds, reason = source._support_for(params)

        self.assertEqual(status, "LOAN_ONLY_SUPPORTED")
        self.assertEqual(loan_feeds, ("USDC / USD",))
        self.assertEqual(collateral_feeds, ("sUSDe / USD",))
        self.assertIn("loan priced", reason)

    def test_write_snapshots_keeps_supply_when_collateral_is_unpriced(self):
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        source._params[MARKET_ID] = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="sUSDe",
            loan_decimals=6,
            collateral_decimals=18,
            oracle="0x" + ORACLE,
            lltv=860_000_000_000_000_000,
        )
        ts = dt.datetime(2026, 1, 1, 12)
        ch = FakeClickHouse([{"ts": ts, "feed": "USDC / USD", "price": 1.0}])
        inserts = []

        def capture_insert(_ch, table, df, *args, **kwargs):
            inserts.append((table, df.copy()))

        def capture_row_insert(_ch, table, rows, columns, *args, **kwargs):
            inserts.append((table, pd.DataFrame(rows, columns=columns)))

        with patch.object(morpho_module, "insert_df_batched", capture_insert), \
             patch.object(morpho_module, "insert_rows_batched", capture_row_insert), \
             patch.object(morpho_module, "upsert_market_timeseries", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "upsert_api_market_latest", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "refresh_api_protocol_tvl_weekly", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "rewrite_protocol_window_if_enabled", lambda *_args, **_kwargs: 0):
            written = source._write_snapshots(
                ch,
                [{
                    "kind": "snapshot",
                    "market_id": MARKET_ID,
                    "block_number": 100,
                    "timestamp": ts,
                    "total_supply_assets": 1_000_000,
                    "total_supply_shares": 1_000_000,
                    "total_borrow_assets": 400_000,
                    "total_borrow_shares": 400_000,
                    "collateral_assets": 10 * 10**18,
                    "fee_wad": 0,
                    "last_borrow_rate_wad": 0,
                    "last_update_timestamp": ts,
                }],
            )

        self.assertGreater(written, 0)
        metrics = [df for table, df in inserts if table == "morpho_market_metrics"]
        self.assertEqual(len(metrics), 1)
        row = metrics[0].iloc[0]
        self.assertEqual(row["oracle_support"], "LOAN_ONLY_SUPPORTED")
        self.assertEqual(row["supply_usd"], 1.0)
        self.assertEqual(row["borrow_usd"], 0.4)
        self.assertEqual(row["collateral_usd"], 0.0)

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

    def test_pendle_pt_collateral_classified_as_pendle_supported(self):
        """Market with PT collateral found in _pendle_assets gets PENDLE_SUPPORTED."""
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        pt_address = "9f56094c450763769ba0ea9fe2876070c0fd5f77"
        source._pendle_assets = {
            pt_address: {
                "asset_address": f"0x{pt_address}",
                "asset_type": "PT",
                "symbol": "PT-sUSDE-25SEP2025",
            }
        }
        params = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="PT-sUSDE-25SEP2025",
            collateral_token=f"0x{pt_address}",
            oracle="0x" + ORACLE,
        )
        status, loan_feeds, collateral_feeds, reason = source._support_for(params)
        self.assertEqual(status, "PENDLE_SUPPORTED")
        self.assertIn("PT", reason)
        self.assertIn("PT-sUSDE-25SEP2025", reason)

    def test_pendle_support_falls_back_to_oracle_snapshot(self):
        """Without a matching _pendle_assets entry, non-zero oracle → ORACLE_SNAPSHOT_SUPPORTED."""
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        source._pendle_assets = {}  # empty — no Pendle data
        params = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="PT-sUSDE-25SEP2025",
            collateral_token="0x9f56094c450763769ba0ea9fe2876070c0fd5f77",
            oracle="0x" + ORACLE,
        )
        status, _loan_feeds, _collateral_feeds, _reason = source._support_for(params)
        self.assertEqual(status, "ORACLE_SNAPSHOT_SUPPORTED")

    def test_pendle_price_resolution_in_write_snapshots(self):
        """Pendle-priced market produces correct USD metrics from mocked price data."""
        source = MorphoSource()
        source._available_feeds = {"USDC / USD"}
        pt_address = "9f56094c450763769ba0ea9fe2876070c0fd5f77"
        source._pendle_assets = {
            pt_address: {
                "asset_address": f"0x{pt_address}",
                "asset_type": "PT",
                "symbol": "PT-sUSDE-25SEP2025",
            }
        }
        source._params[MARKET_ID] = MorphoMarketParams(
            market_id=MARKET_ID,
            loan_symbol="USDC",
            collateral_symbol="PT-sUSDE-25SEP2025",
            loan_decimals=6,
            collateral_decimals=18,
            collateral_token=f"0x{pt_address}",
            oracle="0x" + ORACLE,
            lltv=860_000_000_000_000_000,
        )
        source._markets[MARKET_ID] = MorphoMarketState()
        ts = dt.datetime(2026, 1, 1, 12)

        # Mock ClickHouse that returns Chainlink + Pendle prices
        class PendleFakeClickHouse:
            def command(self, _query):
                return 0

            def query(self, _query):
                return FakeQueryResult()

            def query_df(self, query):
                if "pendle_eth_price_ohlcv" in query:
                    return pd.DataFrame([
                        {"ts": ts, "asset_address": f"0x{pt_address}", "price": 0.95},
                    ])
                if "chainlink_prices" in query:
                    return pd.DataFrame([
                        {"ts": ts, "feed": "USDC / USD", "price": 1.0},
                    ])
                return pd.DataFrame()

        ch = PendleFakeClickHouse()
        inserts = []

        def capture_insert(_ch, table, df, *args, **kwargs):
            inserts.append((table, df.copy()))

        def capture_row_insert(_ch, table, rows, columns, *args, **kwargs):
            inserts.append((table, pd.DataFrame(rows, columns=columns)))

        with patch.object(morpho_module, "insert_df_batched", capture_insert), \
             patch.object(morpho_module, "insert_rows_batched", capture_row_insert), \
             patch.object(morpho_module, "upsert_market_timeseries", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "upsert_api_market_latest", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "refresh_api_protocol_tvl_weekly", lambda *_args, **_kwargs: 0), \
             patch.object(morpho_module, "rewrite_protocol_window_if_enabled", lambda *_args, **_kwargs: 0):
            written = source._write_snapshots(
                ch,
                [{
                    "kind": "snapshot",
                    "market_id": MARKET_ID,
                    "block_number": 100,
                    "timestamp": ts,
                    "total_supply_assets": 1_000_000,
                    "total_supply_shares": 1_000_000,
                    "total_borrow_assets": 400_000,
                    "total_borrow_shares": 400_000,
                    "collateral_assets": 10 * 10**18,
                    "fee_wad": 0,
                    "last_borrow_rate_wad": 0,
                    "last_update_timestamp": ts,
                }],
            )

        self.assertGreater(written, 0)
        metrics = [df for table, df in inserts if table == "morpho_market_metrics"]
        self.assertEqual(len(metrics), 1)
        row = metrics[0].iloc[0]
        self.assertEqual(row["oracle_support"], "PENDLE_SUPPORTED")
        # 10 PT tokens × $0.95 = $9.50 collateral
        self.assertAlmostEqual(row["collateral_usd"], 9.5, places=1)
        self.assertAlmostEqual(row["collateral_price_usd"], 0.95, places=2)
        dependency_edges = [df for table, df in inserts if table == "oracle_dependency_edges"]
        self.assertEqual(len(dependency_edges), 1)
        self.assertIn("PENDLE", set(dependency_edges[0]["source"]))


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
