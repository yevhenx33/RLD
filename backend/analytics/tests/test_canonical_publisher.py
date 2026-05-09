import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.streams.canonical import encode_envelope, envelope, message_id  # noqa: E402
from analytics.streams.publisher import select_rows_query  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
