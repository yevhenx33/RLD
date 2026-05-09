import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.streams.publisher import export_jsonl_chunk, load_chunk_sidecars, manifest_with_chunks  # noqa: E402
from analytics.streams.registry import load_registry  # noqa: E402


class FakeResult:
    column_names = ["deployment_id", "timestamp", "user", "last_event_block"]
    result_rows = [("ethereum:aave-v3-core", dt.datetime(2024, 1, 1, 12), "0xabc", 123)]


class FakeClickHouse:
    def query(self, _query):
        return FakeResult()


class ChunkExportTests(unittest.TestCase):
    def test_export_jsonl_chunk_writes_sidecar_and_manifest_chunks(self):
        stream = next(stream for stream in load_registry() if stream.id == "aave.processed.account_profiles.v1")
        with tempfile.TemporaryDirectory() as tmp:
            metadata = export_jsonl_chunk(FakeClickHouse(), stream, tmp, base_uri="https://example.test/chunks", limit=1)
            self.assertEqual(metadata["row_count"], 1)
            self.assertEqual(metadata["format"], "jsonl")
            data = Path(tmp) / f"{metadata['chunk_id']}.jsonl"
            self.assertTrue(data.exists())
            line = json.loads(data.read_text(encoding="utf-8").strip())
            self.assertEqual(line["schema"], "astrid.aave.account_profile.v1")
            chunks = load_chunk_sidecars(tmp)
            manifest = manifest_with_chunks([stream], chunks)
            self.assertEqual(manifest["streams"][0]["chunks"][0]["chunk_id"], metadata["chunk_id"])


if __name__ == "__main__":
    unittest.main()
