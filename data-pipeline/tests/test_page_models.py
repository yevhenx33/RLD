import math
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indexer.api.graphql import (  # noqa: E402
    AnalyticsFreshness,
    MarketDetail,
    MarketFlowPoint,
    MarketSnapshot,
    MarketTimeseriesPoint,
    ProtocolApyPoint,
    ProtocolTvlPoint,
    _build_lending_data_page_payload,
    _build_lending_pool_page_payload,
    _build_protocol_markets_page_payload,
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


if __name__ == "__main__":
    unittest.main()
