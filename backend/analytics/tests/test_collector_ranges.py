import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hypersync" not in sys.modules:
    hypersync_stub = types.ModuleType("hypersync")
    hypersync_stub.LogField = types.SimpleNamespace(
        BLOCK_NUMBER="block_number",
        LOG_INDEX="log_index",
        TRANSACTION_HASH="transaction_hash",
        ADDRESS="address",
        TOPIC0="topic0",
        TOPIC1="topic1",
        TOPIC2="topic2",
        TOPIC3="topic3",
        DATA="data",
    )
    hypersync_stub.BlockField = types.SimpleNamespace(NUMBER="number", TIMESTAMP="timestamp")
    hypersync_stub.LogSelection = lambda **kwargs: kwargs
    sys.modules["hypersync"] = hypersync_stub

from analytics.collector import (  # noqa: E402
    advance_hypersync_cursor,
    hypersync_to_block_exclusive,
    scanned_block_from_exclusive,
)


class CollectorRangeTests(unittest.TestCase):
    def test_hypersync_to_block_is_exclusive_and_covers_head(self):
        self.assertEqual(hypersync_to_block_exclusive(100, 105, batch_size=1000), 106)
        self.assertEqual(scanned_block_from_exclusive(106), 105)

    def test_hypersync_to_block_respects_batch_size(self):
        self.assertEqual(hypersync_to_block_exclusive(100, 1000, batch_size=25), 125)
        self.assertEqual(scanned_block_from_exclusive(125), 124)

    def test_non_advancing_hypersync_cursor_fails(self):
        with self.assertRaises(RuntimeError):
            advance_hypersync_cursor(100, 100)
