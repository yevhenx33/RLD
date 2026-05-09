import unittest

from astrid_node.streams import Stream, install_plan, install_stream


class FakeClickHouse:
    def __init__(self):
        self.commands = []
        self.inserts = []

    def command(self, sql):
        self.commands.append(sql)

    def insert(self, table, rows, column_names=None):
        self.inserts.append((table, rows, column_names))


def sample_stream():
    return Stream(
        id="aave.processed.account_profiles.v1",
        subject="astrid.data.v1.aave.ethereum.processed.account_profiles",
        local_table="astrid_aave.account_profiles",
        schema_version="1.0.0",
        schema_hash="abc",
        manifest={"id": "aave.processed.account_profiles.v1"},
    )


class StreamInstallTests(unittest.TestCase):
    def test_dry_run_does_not_touch_clickhouse(self):
        ch = FakeClickHouse()
        result = install_stream(ch, sample_stream(), dry_run=True)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(ch.commands, [])
        self.assertEqual(ch.inserts, [])

    def test_install_creates_only_stream_table_and_metadata(self):
        ch = FakeClickHouse()
        result = install_stream(ch, sample_stream())
        self.assertEqual(result["status"], "INSTALLED")
        rendered = "\n".join(ch.commands)
        self.assertIn("CREATE DATABASE IF NOT EXISTS astrid_aave", rendered)
        self.assertIn("CREATE TABLE IF NOT EXISTS astrid_aave.account_profiles", rendered)
        self.assertIn("ORDER BY message_id", rendered)
        self.assertEqual({item[0] for item in ch.inserts}, {"astrid_meta.installed_streams", "astrid_meta.stream_registry"})

    def test_install_plan_declares_unmodified_tables(self):
        plan = install_plan(sample_stream())
        self.assertIn("unrelated astrid_user tables", plan["will_not_modify"])

    def test_install_plan_rejects_unsafe_table_identifiers(self):
        stream = Stream(
            id="unsafe.v1",
            subject="astrid.data.v1.unsafe",
            local_table="astrid_aave.account_profiles; DROP TABLE x",
            schema_version="1.0.0",
            schema_hash="abc",
            manifest={"id": "unsafe.v1"},
        )
        with self.assertRaises(ValueError):
            install_plan(stream)


if __name__ == "__main__":
    unittest.main()
