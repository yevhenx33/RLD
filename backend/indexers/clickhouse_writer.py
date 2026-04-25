"""
ClickHouse mirror writer for simulation indexer dual-write.

This module keeps the P0 mirror intentionally narrow: indexer cursor,
raw events, latest block snapshots, and recent candles.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import clickhouse_connect

log = logging.getLogger(__name__)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_utc(value: Any) -> datetime:
    if value is None:
        return _utc_now_naive()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return _utc_now_naive()


def _safe_u64(value: Any) -> int:
    if value is None:
        return 0
    iv = int(value)
    return iv if iv >= 0 else 0


def _safe_i32(value: Any) -> int:
    if value is None:
        return 0
    return int(value)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class SimClickHouseMirrorWriter:
    def __init__(
        self,
        host: str,
        port: int,
        database: str,
        schema_path: str,
    ) -> None:
        self.host = host
        self.port = port
        self.database = database
        self.schema_path = schema_path
        self._client = None

    def _get_client(self):
        if self._client is None:
            settings = {}
            if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
                settings["async_insert"] = 1
                settings["wait_for_async_insert"] = (
                    1 if os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"} else 0
                )
            self._client = clickhouse_connect.get_client(
                host=self.host,
                port=self.port,
                database=self.database,
                settings=settings,
            )
        return self._client

    def reset_client(self) -> None:
        try:
            self.close()
        except Exception:
            log.debug("ClickHouse mirror client close failed during reset", exc_info=True)

    def ensure_schema(self) -> None:
        schema_sql = Path(self.schema_path).read_text(encoding="utf-8")
        client = self._get_client()
        statements = [stmt.strip() for stmt in schema_sql.split(";") if stmt.strip()]
        for statement in statements:
            client.command(statement)
        log.info("ClickHouse mirror schema ensured (%d statements)", len(statements))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def write_batch(self, payload: dict[str, Any]) -> None:
        try:
            self._write_batch(payload)
        except Exception:
            self.reset_client()
            raise

    def _write_batch(self, payload: dict[str, Any]) -> None:
        client = self._get_client()
        synced_at = _utc_now_naive()

        cursor = payload.get("cursor")
        if cursor:
            client.insert(
                "sim_indexer_cursor",
                [
                    [
                        _safe_str(cursor.get("market_id")),
                        _safe_u64(cursor.get("last_indexed_block")),
                        _as_naive_utc(cursor.get("last_indexed_at")),
                        _safe_u64(cursor.get("total_events")),
                        synced_at,
                    ]
                ],
                column_names=[
                    "market_id",
                    "last_indexed_block",
                    "last_indexed_at",
                    "total_events",
                    "synced_at",
                ],
            )

        events = payload.get("events") or []
        if events:
            event_rows = [
                [
                    _safe_str(row.get("market_id")),
                    _safe_u64(row.get("block_number")),
                    _safe_u64(row.get("block_timestamp")),
                    _safe_str(row.get("tx_hash")),
                    _safe_i32(row.get("log_index")),
                    _safe_str(row.get("event_name")),
                    _safe_str(row.get("contract_address")),
                    _safe_str(row.get("data")),
                    synced_at,
                ]
                for row in events
            ]
            client.insert(
                "sim_events",
                event_rows,
                column_names=[
                    "market_id",
                    "block_number",
                    "block_timestamp",
                    "tx_hash",
                    "log_index",
                    "event_name",
                    "contract_address",
                    "data",
                    "synced_at",
                ],
            )

        block_state = payload.get("block_state")
        if block_state:
            client.insert(
                "sim_block_states",
                [
                    [
                        _safe_str(block_state.get("market_id")),
                        _safe_u64(block_state.get("block_number")),
                        _safe_u64(block_state.get("block_timestamp")),
                        _safe_str(block_state.get("normalization_factor")),
                        _safe_str(block_state.get("total_debt")),
                        _safe_str(block_state.get("index_price")),
                        _safe_str(block_state.get("mark_price")),
                        _safe_str(block_state.get("liquidity")),
                        _safe_str(block_state.get("token0_balance")),
                        _safe_str(block_state.get("token1_balance")),
                        _safe_str(block_state.get("swap_volume")),
                        _safe_i32(block_state.get("swap_count")),
                        synced_at,
                    ]
                ],
                column_names=[
                    "market_id",
                    "block_number",
                    "block_timestamp",
                    "normalization_factor",
                    "total_debt",
                    "index_price",
                    "mark_price",
                    "liquidity",
                    "token0_balance",
                    "token1_balance",
                    "swap_volume",
                    "swap_count",
                    "synced_at",
                ],
            )

        candles = payload.get("candles") or []
        if candles:
            candle_rows = [
                [
                    _safe_str(row.get("market_id")),
                    _safe_str(row.get("resolution")),
                    _safe_u64(row.get("bucket")),
                    _safe_str(row.get("index_open")),
                    _safe_str(row.get("index_high")),
                    _safe_str(row.get("index_low")),
                    _safe_str(row.get("index_close")),
                    _safe_str(row.get("mark_open")),
                    _safe_str(row.get("mark_high")),
                    _safe_str(row.get("mark_low")),
                    _safe_str(row.get("mark_close")),
                    _safe_str(row.get("volume_usd")),
                    _safe_i32(row.get("swap_count")),
                    synced_at,
                ]
                for row in candles
            ]
            client.insert(
                "sim_candles",
                candle_rows,
                column_names=[
                    "market_id",
                    "resolution",
                    "bucket",
                    "index_open",
                    "index_high",
                    "index_low",
                    "index_close",
                    "mark_open",
                    "mark_high",
                    "mark_low",
                    "mark_close",
                    "volume_usd",
                    "swap_count",
                    "synced_at",
                ],
            )
