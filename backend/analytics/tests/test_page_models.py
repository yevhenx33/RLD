import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.api.graphql import (  # noqa: E402
    API_DEFAULT_PAGE_SIZE,
    API_MAX_PAGE_SIZE,
    AnalyticsFreshness,
    GRAPHQL_RATE_LIMIT_PER_MINUTE,
    MarketDetail,
    MarketFlowPoint,
    MarketSnapshot,
    MarketTimeseriesPoint,
    ProtocolApyPoint,
    ProtocolTvlPoint,
    _api_page_size,
    _build_lending_data_page_payload,
    _build_lending_pool_page_payload,
    _build_protocol_markets_page_payload,
    _connection_page,
    _is_introspection_query,
    _protocol_readiness_items,
    _query_depth,
    _query_morpho_allocation_timeseries,
    _rate_limit_allowed,
)


class PageModelTests(unittest.TestCase):
    def freshness(self) -> AnalyticsFreshness:
        return AnalyticsFreshness(
            ready=True,
            status="ready",
            version="test",
            generated_at=1_700_000_000,
        )

    def test_lending_data_page_is_render_ready_and_bounded(self):
        payload = _build_lending_data_page_payload(
            self.freshness(),
            [
                MarketSnapshot(
                    entity_id="0xusdc",
                    symbol="USDC",
                    protocol="AAVE_MARKET",
                    supply_usd=1_000.0,
                    borrow_usd=500.0,
                    supply_apy=0.04,
                    borrow_apy=0.08,
                    utilization=0.5,
                ),
                MarketSnapshot(
                    entity_id="0xdai",
                    symbol="DAI",
                    protocol="AAVE_MARKET",
                    supply_usd=0.0,
                    borrow_usd=math.inf,
                    supply_apy=math.nan,
                    borrow_apy=-1.0,
                    utilization=0.0,
                ),
            ],
            [ProtocolTvlPoint(date="2023-04-03", aave=1_000.0, euler=0.0, fluid=0.0)],
            [ProtocolApyPoint(timestamp=1_680_480_000, average_supply_apy=0.04, average_borrow_apy=0.08)],
        )

        self.assertEqual(payload.stats.market_count, 2)
        self.assertEqual(payload.stats.total_supply_usd, 1_000.0)
        self.assertEqual(payload.stats.total_borrow_usd, 500.0)
        self.assertEqual(payload.stats.average_supply_apy, 0.04)
        self.assertEqual(payload.stats.average_borrow_apy, 0.08)
        self.assertEqual(payload.markets[0].entity_id, "0xusdc")
        self.assertEqual(payload.markets[1].borrow_usd, 0.0)
        self.assertEqual(payload.chart_data[0].average_supply_apy, 4.0)
        self.assertEqual(payload.chart_data[0].average_borrow_apy, 8.0)

    def test_protocol_markets_page_excludes_trapped_markets_from_averages(self):
        payload = _build_protocol_markets_page_payload(
            self.freshness(),
            [
                MarketDetail(
                    entity_id="safe",
                    symbol="USDC",
                    protocol="AAVE_MARKET",
                    supply_usd=1_000.0,
                    borrow_usd=500.0,
                    supply_apy=0.04,
                    borrow_apy=0.08,
                    utilization=0.5,
                ),
                MarketDetail(
                    entity_id="trapped",
                    symbol="DAI",
                    protocol="AAVE_MARKET",
                    supply_usd=1_000.0,
                    borrow_usd=1_000.0,
                    supply_apy=2.0,
                    borrow_apy=3.0,
                    utilization=1.0,
                ),
            ],
        )

        self.assertEqual(payload.stats.market_count, 2)
        self.assertEqual(payload.stats.total_supply_usd, 2_000.0)
        self.assertEqual(payload.stats.average_supply_apy, 0.04)
        self.assertEqual(payload.stats.average_borrow_apy, 0.08)
        self.assertTrue(next(row for row in payload.rows if row.entity_id == "trapped").is_trapped)

    def test_lending_pool_page_returns_chart_ready_percent_values(self):
        payload = _build_lending_pool_page_payload(
            self.freshness(),
            [
                MarketDetail(
                    entity_id="0xusdc",
                    symbol="USDC",
                    protocol="AAVE_MARKET",
                    supply_usd=1_000.0,
                    borrow_usd=500.0,
                    supply_apy=0.04,
                    borrow_apy=0.08,
                    utilization=0.5,
                )
            ],
            [
                MarketTimeseriesPoint(
                    timestamp=2,
                    supply_apy=0.05,
                    borrow_apy=0.09,
                    utilization=0.6,
                    supply_usd=2_000.0,
                    borrow_usd=1_200.0,
                ),
                MarketTimeseriesPoint(
                    timestamp=1,
                    supply_apy=0.04,
                    borrow_apy=0.08,
                    utilization=0.5,
                    supply_usd=1_000.0,
                    borrow_usd=500.0,
                ),
            ],
            [
                MarketFlowPoint(
                    timestamp=1,
                    supply_inflow_usd=10.0,
                    supply_outflow_usd=0.0,
                    borrow_inflow_usd=5.0,
                    borrow_outflow_usd=0.0,
                    net_supply_flow_usd=10.0,
                    net_borrow_flow_usd=5.0,
                    cumulative_supply_net_inflow_usd=10.0,
                    cumulative_borrow_net_inflow_usd=5.0,
                )
            ],
        )

        self.assertEqual(payload.market.symbol, "USDC")
        self.assertEqual([point.timestamp for point in payload.rate_chart], [1, 2])
        self.assertEqual(payload.rate_chart[0].supply_apy, 4.0)
        self.assertEqual(payload.rate_chart[0].borrow_apy, 8.0)
        self.assertEqual(payload.rate_chart[0].utilization, 50.0)
        self.assertEqual(len(payload.flow_chart), 1)

    def test_morpho_allocation_timeseries_is_backend_bucketed(self):
        class Result:
            result_rows = [
                (1_700_000_000, "0xvault", "Vault", 123.45, 0.25),
            ]

        class FakeClickHouse:
            sql = ""
            parameters = {}

            def query(self, sql, parameters=None):
                self.sql = sql
                self.parameters = parameters or {}
                return Result()

        ch = FakeClickHouse()
        rows = _query_morpho_allocation_timeseries(
            ch,
            "0x64d65c9a2d91c36d56fbc42d69e979335320169b3df63bf92789e2c8883fcc64",
            limit=500,
        )

        self.assertIn("toStartOfDay(a.timestamp)", ch.sql)
        self.assertIn("top_vaults", ch.sql)
        self.assertIn("timestamp >= %(start_ts)s", ch.sql)
        self.assertEqual(ch.parameters["top_n"], 15)
        self.assertEqual(
            ch.parameters["market_id"],
            "0x64d65c9a2d91c36d56fbc42d69e979335320169b3df63bf92789e2c8883fcc64",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].timestamp, 1_700_000_000)
        self.assertEqual(rows[0].vault_address, "0xvault")
        self.assertEqual(rows[0].supplied_usd, 123.45)

    def test_graphql_introspection_detector_ignores_regular_queries(self):
        self.assertFalse(_is_introspection_query("query Market { latestRates { timestamp } }"))
        self.assertTrue(_is_introspection_query("query IntrospectionQuery { __schema { queryType { name } } }"))
        self.assertTrue(_is_introspection_query('query TypeLookup { __type(name: "Query") { name } }'))

    def test_graphql_depth_counter_ignores_strings(self):
        query = """
        query Deep {
          lendingDataPage {
            freshness { status }
            markets { symbol protocol }
          }
          latestRates { timestamp }
        }
        """
        self.assertEqual(_query_depth(query), 3)
        self.assertEqual(_query_depth('query Q { latestRates { symbol: "__schema { nope }" } }'), 2)

    def test_rate_limit_helper_enforces_rolling_window(self):
        key = "unit-test-rate-limit"
        allowed = [
            _rate_limit_allowed(key, now=1000.0 + index * 0.01)
            for index in range(GRAPHQL_RATE_LIMIT_PER_MINUTE)
        ]
        self.assertTrue(all(allowed))
        self.assertFalse(_rate_limit_allowed(key, now=1001.5))
        self.assertTrue(_rate_limit_allowed(key, now=1061.0))

    def test_pendle_public_readiness_does_not_require_processor_lag(self):
        readiness = _protocol_readiness_items(
            {"PENDLE_ETHEREUM_PT_YT_PRICES": 0},
            {"PENDLE_ETHEREUM_PT_YT_PRICES": -1},
            ("PENDLE_ETHEREUM_PT_YT_PRICES",),
        )
        self.assertTrue(readiness[0].ready)
        self.assertEqual(readiness[0].issues, [])

    def test_api_pagination_defaults_and_caps(self):
        self.assertEqual(_api_page_size(None), API_DEFAULT_PAGE_SIZE)
        self.assertEqual(_api_page_size(25), 25)
        self.assertEqual(_api_page_size(5000), API_MAX_PAGE_SIZE)
        nodes, page_info, total_count = _connection_page(list(range(250)), None, None)
        self.assertEqual(nodes, list(range(100)))
        self.assertTrue(page_info.has_next_page)
        self.assertEqual(page_info.end_cursor, "100")
        self.assertEqual(total_count, 250)
        nodes, page_info, total_count = _connection_page(list(range(250)), 1000, "100")
        self.assertEqual(nodes, list(range(100, 250)))
        self.assertFalse(page_info.has_next_page)
        self.assertIsNone(page_info.end_cursor)
        self.assertEqual(total_count, 250)


if __name__ == "__main__":
    unittest.main()
