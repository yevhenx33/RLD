import tempfile
import unittest
from pathlib import Path

from astrid_node.clickhouse import export_query


class FakeClickHouse:
    def __init__(self):
        self.queries = []

    def raw_query(self, query):
        self.queries.append(query)
        return b"payload"


class ExportTests(unittest.TestCase):
    def test_export_uses_clickhouse_format_stream(self):
        ch = FakeClickHouse()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "data.csv"
            export_query(ch, "astrid_aave.account_profiles", output_format="csv", out=str(out))
            self.assertEqual(out.read_bytes(), b"payload")
            self.assertIn("FORMAT CSVWithNames", ch.queries[0])

    def test_export_rejects_unsafe_table_identifiers(self):
        ch = FakeClickHouse()
        with self.assertRaises(ValueError):
            export_query(ch, "astrid_aave.account_profiles; DROP TABLE x", output_format="csv", out="ignored.csv")


if __name__ == "__main__":
    unittest.main()
