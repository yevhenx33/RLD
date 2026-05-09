import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.scripts import rld_indexer  # noqa: E402


class RldIndexerStreamsCliTests(unittest.TestCase):
    def test_streams_check_command(self):
        with patch.object(sys, "argv", ["rld_indexer", "streams", "check", "--json"]):
            self.assertEqual(rld_indexer.main(), 0)

    def test_streams_manifest_command(self):
        with patch.object(sys, "argv", ["rld_indexer", "streams", "manifest"]):
            self.assertEqual(rld_indexer.main(), 0)


if __name__ == "__main__":
    unittest.main()
