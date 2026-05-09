import datetime as dt
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analytics.streams.state import ensure_publisher_state_tables, read_cursor, upsert_cursor  # noqa: E402


class FakeResult:
    def __init__(self, rows):
        self.result_rows = rows


class RecordingClickHouse:
    def __init__(self):
        self.commands = []
        self.inserts = []
        self.cursor_rows = []

    def command(self, sql):
        self.commands.append(sql)

    def insert(self, table, rows, column_names=None):
        self.inserts.append((table, rows, column_names))
        if table == "stream_publisher_state":
            self.cursor_rows = rows

    def query(self, _sql):
        if not self.cursor_rows:
            return FakeResult([])
        row = self.cursor_rows[-1]
        return FakeResult([[row[1], row[2], row[3], row[4]]])


class PublisherStateTests(unittest.TestCase):
    def test_schema_is_idempotent_and_non_destructive(self):
        ch = RecordingClickHouse()
        ensure_publisher_state_tables(ch)
        ensure_publisher_state_tables(ch)
        rendered = "\n".join(ch.commands).upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS STREAM_PUBLISHER_STATE", rendered)
        self.assertNotIn("DROP TABLE", rendered)
        self.assertNotIn("TRUNCATE", rendered)

    def test_cursor_roundtrip(self):
        ch = RecordingClickHouse()
        now = dt.datetime(2024, 1, 1)
        upsert_cursor(ch, stream_id="s", last_cursor="10", last_block=10, last_timestamp=now, last_nats_sequence=7)
        cursor = read_cursor(ch, "s")
        self.assertEqual(cursor["last_cursor"], "10")
        self.assertEqual(cursor["last_block"], 10)
        self.assertEqual(cursor["last_nats_sequence"], 7)


if __name__ == "__main__":
    unittest.main()
