import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.streams.registry import StreamRegistryError, load_registry, registry_manifest, validate_registry  # noqa: E402


class StreamRegistryTests(unittest.TestCase):
    def test_registry_loads_canonical_streams(self):
        streams = load_registry()
        ids = {stream.id for stream in streams}
        self.assertIn("aave.processed.account_profiles.v1", ids)
        self.assertIn("aave.raw.account_events.v1", ids)
        self.assertIn("chainlink.processed.prices.v1", ids)
        self.assertTrue(all(stream.subject.startswith("astrid.data.v1.") for stream in streams))

    def test_manifest_has_stable_schema_hashes(self):
        manifest = registry_manifest(load_registry())
        hashes = [stream["schema_hash"] for stream in manifest["streams"]]
        self.assertTrue(all(len(value) == 64 for value in hashes))
        self.assertEqual(manifest, registry_manifest(load_registry()))

    def test_duplicate_subject_is_rejected(self):
        streams = load_registry()
        duplicate = streams[0].__class__(**{**streams[0].__dict__, "id": "duplicate.id"})
        with self.assertRaises(StreamRegistryError):
            validate_registry([streams[0], duplicate])


if __name__ == "__main__":
    unittest.main()
