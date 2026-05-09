import unittest

from astrid_node.metadata import DATABASES, ensure_metadata


class FakeClickHouse:
    def __init__(self):
        self.commands = []

    def command(self, sql):
        self.commands.append(sql)


class LocalMetadataTests(unittest.TestCase):
    def test_metadata_schema_is_additive(self):
        ch = FakeClickHouse()
        ensure_metadata(ch)
        rendered = "\n".join(ch.commands).upper()
        for database in DATABASES:
            self.assertIn(f"CREATE DATABASE IF NOT EXISTS {database.upper()}", rendered)
        self.assertIn("ASTRID_META.INSTALLED_STREAMS", rendered)
        self.assertNotIn("DROP TABLE", rendered)
        self.assertNotIn("TRUNCATE", rendered)


if __name__ == "__main__":
    unittest.main()
