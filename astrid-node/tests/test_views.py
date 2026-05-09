import unittest

from astrid_node.streams import Stream
from astrid_node.views import ColumnDef, create_view_ddl, parse_columns, view_name


def sample_stream(columns=None):
    manifest = {"id": "test.v1"}
    if columns is not None:
        manifest["columns"] = columns
    return Stream(
        id="test.v1",
        subject="astrid.data.v1.test",
        local_table="astrid_test.data",
        schema_version="1.0.0",
        schema_hash="abc",
        manifest=manifest,
    )


class ViewTests(unittest.TestCase):
    def test_parse_columns_from_dict_list(self):
        columns = [
            {"name": "timestamp", "type": "DateTime"},
            {"name": "supply_usd", "type": "Float64"},
            {"name": "symbol", "type": "String"},
        ]
        result = parse_columns({"columns": columns})
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0], ColumnDef(name="timestamp", type="DateTime"))
        self.assertEqual(result[1], ColumnDef(name="supply_usd", type="Float64"))

    def test_parse_columns_from_colon_strings(self):
        columns = ["timestamp:DateTime", "supply_usd:Float64"]
        result = parse_columns({"columns": columns})
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], ColumnDef(name="timestamp", type="DateTime"))

    def test_parse_columns_empty(self):
        self.assertEqual(parse_columns({}), ())
        self.assertEqual(parse_columns({"columns": []}), ())

    def test_view_name(self):
        self.assertEqual(view_name("astrid_aave.market_timeseries"), "astrid_aave.market_timeseries_v")

    def test_create_view_ddl_generates_valid_sql(self):
        columns = (
            ColumnDef(name="timestamp", type="DateTime"),
            ColumnDef(name="supply_usd", type="Float64"),
            ColumnDef(name="symbol", type="String"),
            ColumnDef(name="block_number", type="UInt64"),
        )
        stream = sample_stream()
        ddl = create_view_ddl(stream, columns)
        self.assertIn("CREATE OR REPLACE VIEW astrid_test.data_v AS", ddl)
        self.assertIn("parseDateTimeBestEffort(JSONExtractString(payload_json, 'rows', 1, 'timestamp'))", ddl)
        self.assertIn("JSONExtractFloat(payload_json, 'rows', 1, 'supply_usd') AS supply_usd", ddl)
        self.assertIn("JSONExtractString(payload_json, 'rows', 1, 'symbol') AS symbol", ddl)
        self.assertIn("JSONExtractUInt(payload_json, 'rows', 1, 'block_number') AS block_number", ddl)
        self.assertIn("FROM astrid_test.data FINAL", ddl)

    def test_create_view_ddl_empty_columns(self):
        self.assertEqual(create_view_ddl(sample_stream(), ()), "")

    def test_install_view_no_columns_returns_none(self):
        from astrid_node.views import install_view
        stream = sample_stream(columns=[])
        self.assertIsNone(install_view(None, stream))


if __name__ == "__main__":
    unittest.main()
