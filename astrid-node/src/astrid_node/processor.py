"""Processor SDK for Astrid Node."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ProcessorError(RuntimeError):
    pass


@dataclass
class ProcessorContext:
    ch: Any | None = None
    writes: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def insert(self, table: str, row: dict[str, Any]) -> None:
        if not table.startswith("astrid_user."):
            raise ProcessorError("custom processors may only write to astrid_user.* tables")
        self.writes.append((table, row))
        if self.ch is not None:
            columns = list(row)
            self.ch.insert(table, [[row[col] for col in columns]], column_names=columns)


class Processor:
    inputs: list[str] = []

    def handle(self, msg: dict[str, Any], ctx: ProcessorContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def run_fixture(cls, messages: list[dict[str, Any]], ch: Any | None = None) -> ProcessorContext:
        processor = cls()
        ctx = ProcessorContext(ch=ch)
        for message in messages:
            for item in iter_payload_messages(message):
                processor.handle(item, ctx)
        return ctx

    @classmethod
    def run_payloads(cls, payloads: list[str], ch: Any | None = None) -> ProcessorContext:
        import json

        return cls.run_fixture([json.loads(payload) for payload in payloads], ch=ch)

    @classmethod
    def run(cls) -> None:
        raise SystemExit("Use `astrid-node processor run <processor.py> --fixture ...` or `--table ...`.")


def iter_payload_messages(message: dict[str, Any]):
    rows = message.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                yield row
        return
    yield message
