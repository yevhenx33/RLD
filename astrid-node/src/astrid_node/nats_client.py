"""NATS JetStream consumer helpers for Astrid Node."""

from __future__ import annotations

import asyncio

from astrid_node.clickhouse import insert_json_payloads
from astrid_node.config import NodeConfig
from astrid_node.streams import Stream


async def consume_stream(
    stream: Stream,
    config: NodeConfig,
    ch,
    *,
    durable: str | None = None,
    batch_size: int = 100,
    once: bool = False,
) -> dict:
    import nats
    from nats.errors import TimeoutError as NatsTimeoutError

    nc = await nats.connect(config.nats_url, name="astrid-node")
    js = nc.jetstream()
    durable = durable or stream.id.replace(".", "_")
    consumed = 0
    try:
        sub = await js.pull_subscribe(stream.subject, durable=durable)
        while True:
            try:
                messages = await sub.fetch(batch_size, timeout=1)
            except (TimeoutError, NatsTimeoutError):
                if once:
                    break
                await asyncio.sleep(0.2)
                continue
            payloads = [msg.data.decode("utf-8") for msg in messages]
            written = insert_json_payloads(ch, stream.local_table, payloads)
            for msg in messages:
                await msg.ack()
            consumed += written
            ch.insert(
                "astrid_meta.stream_cursors",
                [[stream.id, str(consumed), 0]],
                column_names=["stream_id", "last_cursor", "last_block"],
            )
            if once:
                break
    finally:
        await nc.drain()
        await nc.close()
    return {"stream": stream.id, "messages": consumed, "durable": durable}
