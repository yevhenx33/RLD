"""Integration test: publisher -> NATS -> node consumer -> local ClickHouse.

Requires Docker services to be running:
    astrid-node up

Run with:
    python3 -m pytest tests/integration/ -v -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest

import pytest

try:
    import clickhouse_connect
    import nats as nats_mod
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


CLICKHOUSE_HOST = os.getenv("ASTRID_NODE_CLICKHOUSE_HOST", "127.0.0.1")
CLICKHOUSE_PORT = int(os.getenv("ASTRID_NODE_CLICKHOUSE_PORT", "8124"))
CLICKHOUSE_USER = os.getenv("ASTRID_NODE_CLICKHOUSE_USER", "astrid")
CLICKHOUSE_PASSWORD = os.getenv("ASTRID_NODE_CLICKHOUSE_PASSWORD", "astrid")
NATS_URL = os.getenv("ASTRID_NODE_NATS_URL", "nats://127.0.0.1:4223")

STREAM_SUBJECT = "astrid.data.v1.test.integration.profiles"
STREAM_ID = "test.integration.profiles.v1"
LOCAL_TABLE = "astrid_test.integration_profiles"


def _ch():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
    )


def _is_docker_running() -> bool:
    try:
        ch = _ch()
        ch.command("SELECT 1")
        ch.close()
        return True
    except Exception:
        return False


@pytest.mark.integration
@unittest.skipUnless(HAS_DEPS and _is_docker_running(), "Docker services not running")
class PublishConsumeTests(unittest.TestCase):
    """End-to-end: publish synthetic messages to NATS, consume into local ClickHouse."""

    @classmethod
    def setUpClass(cls):
        cls.ch = _ch()
        # Set up metadata and test table
        from astrid_node.metadata import ensure_metadata
        ensure_metadata(cls.ch)
        cls.ch.command("CREATE DATABASE IF NOT EXISTS astrid_test")
        cls.ch.command(f"DROP TABLE IF EXISTS {LOCAL_TABLE}")
        cls.ch.command(
            f"""
            CREATE TABLE {LOCAL_TABLE} (
                message_id String,
                payload_json String,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            ORDER BY message_id
            """
        )

    @classmethod
    def tearDownClass(cls):
        cls.ch.command(f"DROP TABLE IF EXISTS {LOCAL_TABLE}")
        cls.ch.close()

    def test_publish_and_consume_roundtrip(self):
        """Publish 5 messages to NATS JetStream, consume into ClickHouse, verify count and content."""
        messages = [
            {
                "message_id": f"test-msg-{i}",
                "schema": "test.profile.v1",
                "stream_id": STREAM_ID,
                "rows": [{"user": f"0x{i:040x}", "health_factor": 1.5 + i * 0.1}],
            }
            for i in range(5)
        ]

        # 1. Publish to NATS
        async def publish():
            nc = await nats_mod.connect(NATS_URL, name="integration-test-publisher")
            js = nc.jetstream()
            from nats.js.api import RetentionPolicy, StorageType, StreamConfig
            config = StreamConfig(
                name="ASTRID_TEST_INTEGRATION",
                subjects=[STREAM_SUBJECT],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.MEMORY,
                duplicate_window=60,
            )
            try:
                await js.stream_info("ASTRID_TEST_INTEGRATION")
                await js.delete_stream("ASTRID_TEST_INTEGRATION")
            except Exception:
                pass
            await js.add_stream(config)
            for msg in messages:
                payload = json.dumps(msg, sort_keys=True).encode("utf-8")
                await js.publish(
                    STREAM_SUBJECT,
                    payload,
                    headers={"Nats-Msg-Id": msg["message_id"]},
                )
            await nc.drain()
            await nc.close()

        asyncio.run(publish())

        # 2. Consume with Astrid Node consumer
        from astrid_node.streams import Stream
        from astrid_node.nats_client import consume_stream
        from astrid_node.config import NodeConfig

        stream = Stream(
            id=STREAM_ID,
            subject=STREAM_SUBJECT,
            local_table=LOCAL_TABLE,
            schema_version="1.0.0",
            schema_hash="test",
            manifest={"id": STREAM_ID},
        )
        config = NodeConfig(nats_url=NATS_URL)
        result = asyncio.run(
            consume_stream(stream, config, self.ch, durable="integration_test", once=True, batch_size=10)
        )

        # 3. Verify
        self.assertEqual(result["messages"], 5)

        count = self.ch.command(f"SELECT count() FROM {LOCAL_TABLE} FINAL")
        self.assertEqual(int(count), 5)

        # Verify content
        rows = self.ch.query(f"SELECT payload_json FROM {LOCAL_TABLE} FINAL ORDER BY message_id").result_rows
        for i, row in enumerate(rows):
            payload = json.loads(row[0])
            self.assertEqual(payload["rows"][0]["user"], f"0x{i:040x}")

        # Verify cursor was tracked
        cursor = self.ch.query(
            f"SELECT last_cursor FROM astrid_meta.stream_cursors FINAL WHERE stream_id = '{STREAM_ID}'"
        ).result_rows
        self.assertTrue(len(cursor) > 0, "Cursor should be tracked in astrid_meta.stream_cursors")

        # Cleanup NATS stream
        async def cleanup():
            nc = await nats_mod.connect(NATS_URL)
            js = nc.jetstream()
            try:
                await js.delete_stream("ASTRID_TEST_INTEGRATION")
            except Exception:
                pass
            await nc.drain()
            await nc.close()

        asyncio.run(cleanup())


if __name__ == "__main__":
    unittest.main()
