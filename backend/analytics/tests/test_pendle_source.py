import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.api.graphql import (  # noqa: E402
    _query_pendle_eth_assets,
    _query_pendle_eth_latest_prices,
    _query_pendle_eth_price_history,
)
from analytics.sources.pendle import (  # noqa: E402
    ETHEREUM_CHAIN_ID,
    PendleEthereumPtYtSource,
    _derive_yt_price_from_pt,
    _history_window_seconds,
    parse_ohlcv_csv,
)


class FakeQueryResult:
    def __init__(self, rows):
        self.result_rows = rows


class FakeClickHouse:
    def __init__(self, rows=None, command_result=None):
        self.rows = rows or []
        self.command_result = command_result
        self.inserted = []

    def query(self, _sql):
        return FakeQueryResult(self.rows)

    def command(self, _sql):
        return self.command_result

    def insert(self, table, rows, column_names=None):
        self.inserted.append((table, rows, column_names))


class FakePendleSource(PendleEthereumPtYtSource):
    def __init__(self, payloads):
        super().__init__()
        self.payloads = list(payloads)

    def _get_json(self, _path, _params=None):
        return self.payloads.pop(0)


class PendleSourceTests(unittest.TestCase):
    def test_parse_ohlcv_csv(self):
        rows = parse_ohlcv_csv(
            "time,open,high,low,close,volume\n"
            "2026-01-01T00:00:00Z,0.9,1.0,0.8,0.95,123.4\n",
            asset_address="0xabc",
            asset_type="PT",
            symbol="PT-test",
            time_frame="hour",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "0xabc")
        self.assertEqual(rows[0][1], ETHEREUM_CHAIN_ID)
        self.assertEqual(rows[0][2], "PT")
        self.assertEqual(rows[0][6:11], [0.9, 1.0, 0.8, 0.95, 123.4])

    def test_discovery_keeps_only_ethereum_pt_yt_assets(self):
        source = FakePendleSource(
            [
                {
                    "markets": [
                        {
                            "chainId": 1,
                            "address": "0xmarket",
                            "expiry": "2026-01-01T00:00:00Z",
                            "pt": {"address": "0xpt", "symbol": "PT-mainnet"},
                            "yt": {"address": "0xyt", "symbol": "YT-mainnet"},
                        },
                        {
                            "chainId": 10,
                            "address": "0xother",
                            "pt": {"address": "0xpt2", "symbol": "PT-other"},
                        },
                    ]
                }
            ]
        )

        assets = source._discover_assets()

        self.assertEqual({asset["asset_type"] for asset in assets}, {"PT", "YT"})
        self.assertEqual({asset["symbol"] for asset in assets}, {"PT-mainnet", "YT-mainnet"})
        self.assertTrue(all(asset["chain_id"] == 1 for asset in assets))

    def test_discovery_accepts_live_market_token_id_shape(self):
        source = FakePendleSource(
            [
                {
                    "results": [
                        {
                            "chainId": 1,
                            "name": "USD0++",
                            "address": "0xmarket",
                            "expiry": "2026-01-01T00:00:00Z",
                            "pt": "1-0x270d664d2fc7d962012a787aec8661ca83df24eb",
                            "yt": "1-0x4f0b4e6512630480b868e62a8a1d3451b0e9192d",
                        }
                    ]
                }
            ]
        )

        assets = source._discover_assets()

        self.assertEqual({asset["asset_type"] for asset in assets}, {"PT", "YT"})
        self.assertEqual(
            {asset["asset_address"] for asset in assets},
            {
                "0x270d664d2fc7d962012a787aec8661ca83df24eb",
                "0x4f0b4e6512630480b868e62a8a1d3451b0e9192d",
            },
        )
        self.assertEqual({asset["symbol"] for asset in assets}, {"PT-USD0++", "YT-USD0++"})
        self.assertTrue(all(asset["chain_id"] == 1 for asset in assets))

    def test_latest_prices_ignore_partial_api_errors(self):
        source = FakePendleSource(
            [
                {
                    "prices": {
                        "1-0xpt": {"price": 0.95},
                    },
                    "errors": {"1-0xyt": "missing"},
                }
            ]
        )
        ch = FakeClickHouse()

        inserted = source._sync_latest_prices(
            ch,
            [
                {
                    "asset_address": "0xpt",
                    "asset_type": "PT",
                    "symbol": "PT-mainnet",
                },
                {
                    "asset_address": "0xyt",
                    "asset_type": "YT",
                    "symbol": "YT-mainnet",
                },
            ],
        )

        self.assertEqual(inserted, 1)
        self.assertEqual(ch.inserted[0][0], "pendle_eth_price_latest")
        self.assertEqual(ch.inserted[0][1][0][4], 0.95)

    def test_latest_prices_derive_missing_active_yt_from_pt(self):
        source = FakePendleSource(
            [
                {
                    "prices": {
                        "1-0xpt": {"price": 0.95},
                    },
                    "errors": {"1-0xyt": "missing"},
                }
            ]
        )
        ch = FakeClickHouse()
        metadata = json.dumps({"market": {"details": {"impliedApy": 0.10}}})

        with patch("analytics.sources.pendle._utc_now_naive", return_value=dt.datetime(2026, 1, 1)):
            inserted = source._sync_latest_prices(
                ch,
                [
                    {
                        "asset_address": "0xpt",
                        "asset_type": "PT",
                        "symbol": "PT-mainnet",
                        "market_address": "0xmarket",
                        "active": 1,
                        "expiry": dt.datetime(2026, 7, 2),
                        "raw_metadata_json": metadata,
                    },
                    {
                        "asset_address": "0xyt",
                        "asset_type": "YT",
                        "symbol": "YT-mainnet",
                        "market_address": "0xmarket",
                        "active": 1,
                        "expiry": dt.datetime(2026, 7, 2),
                        "raw_metadata_json": metadata,
                    },
                ],
            )

        self.assertEqual(inserted, 2)
        prices = {row[2]: row[4] for row in ch.inserted[0][1]}
        self.assertAlmostEqual(prices["PT"], 0.95)
        self.assertAlmostEqual(prices["YT"], 0.95 * 0.10 * (182 / 365))

    def test_derive_yt_price_from_pt_returns_zero_after_expiry(self):
        price = _derive_yt_price_from_pt(
            {
                "expiry": dt.datetime(2026, 1, 1),
                "raw_metadata_json": json.dumps({"market": {"details": {"impliedApy": 0.10}}}),
            },
            0.95,
            dt.datetime(2026, 1, 2),
        )

        self.assertEqual(price, 0.0)

    def test_empty_history_cursor_is_treated_as_missing(self):
        source = FakePendleSource([])

        self.assertIsNone(
            source._existing_history_cursor(
                FakeClickHouse(command_result=dt.datetime(1970, 1, 1)),
                "0xpt",
                "hour",
            )
        )

    def test_hourly_window_respects_1440_point_cap(self):
        self.assertEqual(_history_window_seconds("hour"), 1440 * 3600)

    def test_graphql_asset_helper_maps_rows(self):
        ch = FakeClickHouse(
            [
                (
                    "0xpt",
                    1,
                    "PT",
                    "PT-mainnet",
                    "0xmarket",
                    dt.datetime(2026, 1, 1),
                    1,
                    0,
                )
            ]
        )

        rows = _query_pendle_eth_assets(ch, ["PT"], True, "mainnet", 10, 0)

        self.assertEqual(rows[0].asset_address, "0xpt")
        self.assertEqual(rows[0].chain_id, 1)
        self.assertEqual(rows[0].asset_type, "PT")
        self.assertTrue(rows[0].active)

    def test_graphql_latest_and_history_helpers_map_rows(self):
        latest = _query_pendle_eth_latest_prices(
            FakeClickHouse([("0xpt", 1, "PT", "PT-mainnet", 0.95, dt.datetime(2026, 1, 1))]),
            ["PT"],
            ["0xpt"],
            10,
        )
        self.assertEqual(latest[0].price_usd, 0.95)

        history = _query_pendle_eth_price_history(
            FakeClickHouse(
                [
                    (dt.datetime(2026, 1, 2), 0.95, 1.0, 0.9, 0.98, 11.0),
                    (dt.datetime(2026, 1, 1), 0.90, 0.95, 0.85, 0.92, 10.0),
                ]
            ),
            "0xpt",
            "hour",
            None,
            None,
            10,
        )

        self.assertEqual([point.timestamp for point in history], [1767225600, 1767312000])
        self.assertEqual(history[0].close, 0.92)


if __name__ == "__main__":
    unittest.main()
