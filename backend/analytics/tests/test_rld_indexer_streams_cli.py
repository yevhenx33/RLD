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

    def test_fluid_repair_command_wires_to_handler(self):
        with patch.object(rld_indexer, "cmd_fluid_repair", return_value=0) as handler:
            with patch.object(sys, "argv", ["rld_indexer", "fluid-repair", "--from-block", "25057000", "--to-block", "25057100", "--dry-run"]):
                self.assertEqual(rld_indexer.main(), 0)
            self.assertTrue(handler.called)

    def test_fluid_validate_rpc_command_wires_to_handler(self):
        with patch.object(rld_indexer, "cmd_fluid_validate_rpc", return_value=0) as handler:
            with patch.object(sys, "argv", ["rld_indexer", "fluid-validate-rpc", "--from-block", "25057000", "--to-block", "25057100", "--fail-on-drift"]):
                self.assertEqual(rld_indexer.main(), 0)
            self.assertTrue(handler.called)



if __name__ == "__main__":
    unittest.main()
