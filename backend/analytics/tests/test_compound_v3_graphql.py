import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.api.graphql import (  # noqa: E402
    COMPOUND_V3_MARKET,
    AnalyticsFreshness,
    MarketSnapshot,
    ProtocolTvlPoint,
    _build_lending_data_page_payload,
    _query_protocol_markets,
)


class FakeQueryResult:
    def __init__(self, rows=None):
        self.result_rows = rows or []


class FakeClickHouse:
    def __init__(self, rows, lltv_rows=None):
        self.rows = rows
        self.lltv_rows = lltv_rows or []
        self.last_query = ""
        self.queries = []

    def query(self, query):
        self.last_query = query
        self.queries.append(query)
        if "SELECT entity_id, lltv_min, lltv_max" in query:
            return FakeQueryResult(self.lltv_rows)
        return FakeQueryResult(self.rows)


def test_compound_v3_protocol_markets_query_returns_api_latest_rows():
    comet = "0xc3d688b66703497daa19211eedff47f25384cdc3"
    ch = FakeClickHouse(
        [
            (
                comet,
                "USDC",
                COMPOUND_V3_MARKET,
                1_000_000.0,
                400_000.0,
                0.032,
                0.047,
                0.4,
                "",
                0.0,
                0.0,
                "",
                "PRICED",
                "USDC",
                "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                6,
                "",
                "",
                0,
                1.0,
                0.0,
                "",
                "",
                "",
                "",
                "Compound Price Feed",
            )
        ],
        [(comet, 0.5, 0.825)],
    )

    markets = _query_protocol_markets(ch, COMPOUND_V3_MARKET)

    assert len(markets) == 1
    assert markets[0].entity_id == comet
    assert markets[0].symbol == "USDC"
    assert markets[0].protocol == COMPOUND_V3_MARKET
    assert markets[0].lltv == 0.825
    assert markets[0].lltv_min == 0.5
    assert markets[0].lltv_max == 0.825
    assert markets[0].pricing_status == "PRICED"
    assert markets[0].oracle_support == "Compound Price Feed"
    assert any("api_compound_v3_market_latest_enriched" in query for query in ch.queries)


def test_lending_data_payload_includes_compound_v3_tvl_and_isolated_supply():
    payload = _build_lending_data_page_payload(
        AnalyticsFreshness(ready=True, status="ready", version="test", generated_at=1),
        [
            MarketSnapshot(
                entity_id="0xc3d688b66703497daa19211eedff47f25384cdc3",
                symbol="USDC",
                protocol=COMPOUND_V3_MARKET,
                supply_usd=1_000_000.0,
                borrow_usd=400_000.0,
                supply_apy=0.032,
                borrow_apy=0.047,
                utilization=0.4,
            )
        ],
        [
            ProtocolTvlPoint(
                date="2026-01-05",
                aave=10.0,
                spark=20.0,
                euler=30.0,
                fluid=40.0,
                morpho=50.0,
                compound_v3=60.0,
            )
        ],
        [],
    )

    assert payload.stats.isolated_supply_usd == 1_000_000.0
    assert payload.chart_data[0].compound_v3_tvl == 60.0
    assert payload.chart_data[0].tvl == 210.0
