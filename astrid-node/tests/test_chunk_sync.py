import tempfile
import unittest
import json
from pathlib import Path

from astrid_node.chunks import sha256_file, sync_chunks, verify_chunk
from astrid_node.config import NodeConfig
from astrid_node.streams import Stream


class FakeClickHouse:
    def __init__(self):
        self.inserts = []

    def insert(self, table, rows, column_names=None):
        self.inserts.append((table, rows, column_names))


class ChunkSyncTests(unittest.TestCase):
    def test_checksum_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chunk.bin"
            path.write_bytes(b"astrid")
            digest = sha256_file(path)
            self.assertTrue(verify_chunk(path, digest))
            self.assertFalse(verify_chunk(path, "0" * 64))

    def test_jsonl_chunk_sync_loads_payloads_and_records_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            chunk = Path(tmp) / "chunk.jsonl"
            rows = [{"user": "0xabc"}, {"user": "0xdef"}]
            chunk.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            stream = Stream(
                id="aave.processed.account_profiles.v1",
                subject="astrid.data.v1.aave.ethereum.processed.account_profiles",
                local_table="astrid_aave.account_profiles",
                schema_version="1.0.0",
                schema_hash="abc",
                manifest={},
                chunks=(
                    {
                        "chunk_id": "chunk-1",
                        "uri": str(chunk),
                        "format": "jsonl",
                        "sha256": sha256_file(chunk),
                        "row_count": 2,
                    },
                ),
            )
            config = NodeConfig(cache_dir=Path(tmp) / "cache")
            ch = FakeClickHouse()
            result = sync_chunks(ch, stream, config)
            self.assertEqual(result["loadedRows"], 2)
            self.assertEqual(ch.inserts[0][0], "astrid_aave.account_profiles")
            self.assertEqual(len(ch.inserts[0][1]), 2)
            self.assertEqual(ch.inserts[0][2], ["message_id", "payload_json"])
            self.assertEqual(ch.inserts[-1][0], "astrid_meta.chunk_sync_state")


if __name__ == "__main__":
    unittest.main()
