import json
import tempfile
import unittest
from pathlib import Path

from astrid_node.streams import find_stream, load_manifest, parse_manifest


class ManifestTests(unittest.TestCase):
    def test_parse_manifest_keeps_chunks(self):
        streams = parse_manifest(
            {
                "streams": [
                    {
                        "id": "aave.processed.market_latest.v1",
                        "subject": "astrid.data.v1.aave.ethereum.processed.market_latest",
                        "local_table": "astrid_aave.market_latest",
                        "schema_version": "1.0.0",
                        "schema_hash": "abc",
                        "chunks": [{"chunk_id": "latest", "uri": "file:///tmp/latest.jsonl"}],
                    }
                ]
            }
        )
        self.assertEqual(streams[0].id, "aave.processed.market_latest.v1")
        self.assertEqual(streams[0].chunks[0]["chunk_id"], "latest")

    def test_load_manifest_from_file_and_find_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "streams": [
                            {
                                "id": "chainlink.processed.prices.v1",
                                "subject": "astrid.data.v1.chainlink.ethereum.processed.prices",
                                "local_table": "astrid_chainlink.prices",
                                "schema_version": "1.0.0",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            stream = find_stream(load_manifest(str(manifest)), "chainlink.processed.prices.v1")
            self.assertEqual(stream.local_table, "astrid_chainlink.prices")


if __name__ == "__main__":
    unittest.main()
