import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from astrid_node.config import _load_dotenv


class ConfigTests(unittest.TestCase):
    def test_dotenv_loader_does_not_override_existing_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("ASTRID_NODE_CLICKHOUSE_USER=file_user\n", encoding="utf-8")
            with patch.dict(os.environ, {"ASTRID_NODE_CLICKHOUSE_USER": "env_user"}):
                _load_dotenv(path)
                self.assertEqual(os.environ["ASTRID_NODE_CLICKHOUSE_USER"], "env_user")


if __name__ == "__main__":
    unittest.main()
