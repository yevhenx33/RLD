"""NATS JetStream client wrapper for Astrid publisher commands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PublishAck:
    stream: str = ""
    seq: int = 0


class JetStreamUnavailable(RuntimeError):
    """Raised when nats-py is not installed or NATS is unavailable."""


class AstridJetStreamClient:
    def __init__(self, url: str, *, name: str = "astrid-publisher") -> None:
        self.url = url
        self.name = name
        self._nc = None
        self._js = None

    async def connect(self) -> None:
        try:
            import nats
        except ImportError as exc:
            raise JetStreamUnavailable("nats-py is required for Astrid publisher") from exc
        self._nc = await nats.connect(self.url, name=self.name)
        self._js = self._nc.jetstream()

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
            await self._nc.close()

    async def publish(self, subject: str, payload: bytes, *, message_id: str, headers: dict[str, str] | None = None) -> PublishAck:
        if self._js is None:
            raise JetStreamUnavailable("JetStream client is not connected")
        nats_headers = dict(headers or {})
        nats_headers["Nats-Msg-Id"] = message_id
        ack: Any = await self._js.publish(subject, payload, headers=nats_headers)
        return PublishAck(stream=getattr(ack, "stream", ""), seq=int(getattr(ack, "seq", 0) or 0))

    async def ensure_stream(self, name: str, subjects: list[str]) -> None:
        if self._js is None:
            raise JetStreamUnavailable("JetStream client is not connected")
        from nats.js.api import RetentionPolicy, StorageType, StreamConfig

        config = StreamConfig(
            name=name,
            subjects=subjects,
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            duplicate_window=120,
        )
        try:
            await self._js.stream_info(name)
            await self._js.update_stream(config)
        except Exception:
            await self._js.add_stream(config)
