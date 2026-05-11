import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.streams.canonical import encode_envelope, envelope, message_id  # noqa: E402
from analytics.streams.publisher import _cursor_payload, select_rows_query  # noqa: E402
from analytics.streams.registry import load_registry  # noqa: E402


class CanonicalPublisherTests(unittest.TestCase):
    def setUp(self):
        self.stream = next(stream for stream in load_registry() if stream.id == "aave.processed.account_profiles.v1")
        self.row = {
            "deployment_id": "ethereum:aave-v3-core",
            "timestamp": dt.datetime(2024, 1, 1, 12),
            "user": "0xabc",
            "last_event_block": 123,
            "total_collateral_usd": 10.0,
        }

    def test_envelope_contains_canonical_fields(self):
        payload = envelope(self.stream, [self.row], processor_version="test")
        self.assertEqual(payload["message_id"], message_id(self.stream, self.row))
        self.assertEqual(payload["schema"], "astrid.aave.account_profile.v1")
        self.assertEqual(payload["stream_id"], self.stream.id)
        self.assertEqual(payload["block_number"], 123)
        self.assertEqual(payload["processor_version"], "test")
        self.assertIn(b'"stream_id"', encode_envelope(payload))

    def test_message_id_is_deterministic(self):
        self.assertEqual(message_id(self.stream, self.row), message_id(self.stream, dict(self.row)))

    def test_select_query_uses_cursor_order_and_limit(self):
        query = select_rows_query(self.stream, last_cursor="2024-01-01 00:00:00", limit=5)
        self.assertIn("FROM aave_account_profile_timeseries", query)
        self.assertIn("WHERE timestamp >", query)
        self.assertIn("LIMIT 5", query)

    def test_select_query_combines_source_filter_with_cursor(self):
        stream = next(stream for stream in load_registry() if stream.id == "spark.serving.market_latest.v1")
        query = select_rows_query(stream, last_cursor="2026-01-01 00:00:00", limit=10)
        self.assertIn("FROM api_market_latest", query)
        self.assertIn("WHERE (protocol = 'SPARK_MARKET') AND timestamp >", query)
        self.assertIn("LIMIT 10", query)

    def test_select_query_uses_composite_cursor_payload(self):
        stream = next(stream for stream in load_registry() if stream.id == "spark.raw.events.v1")
        cursor = _cursor_payload(
            stream,
            {
                "block_number": 123,
                "block_timestamp": dt.datetime(2024, 1, 1, 12),
                "tx_hash": "0xabc",
                "log_index": 7,
            },
        )
        query = select_rows_query(stream, last_cursor=cursor, limit=5)
        self.assertIn("tuple(block_number, block_timestamp, tx_hash, log_index) >", query)
        self.assertIn("tuple(123, toDateTime('2024-01-01 12:00:00'), '0xabc', 7)", query)
        self.assertIn("ORDER BY block_number, block_timestamp, tx_hash, log_index", query)


if __name__ == "__main__":
    unittest.main()
