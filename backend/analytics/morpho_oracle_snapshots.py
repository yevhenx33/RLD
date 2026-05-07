"""Morpho Blue oracle snapshot helpers.

These helpers support markets whose collateral side cannot be reconstructed from
feed update events. The backfill samples unique Morpho oracle contracts via
IOracle.price() at historical block tags and stores the raw 1e36-scaled price.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
import time
from dataclasses import dataclass
from typing import Iterable

import requests

try:
    from eth_abi import decode as abi_decode
    from eth_abi import encode as abi_encode
except Exception:  # pragma: no cover - optional in lightweight local test envs
    abi_decode = None
    abi_encode = None


log = logging.getLogger("indexer.morpho_oracles")

MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"
AGGREGATE3_SELECTOR = "0x82ad56cb"
MORPHO_ORACLE_PRICE_SELECTOR = "0xa035b1fe"
MORPHO_ORACLE_PRICE_SCALE = 10**36
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class OracleCallResult:
    oracle: str
    block_number: int
    timestamp: dt.datetime
    price_raw: str
    status: str
    error: str = ""


def normalize_address(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).lower()
    return value if value.startswith("0x") else "0x" + value


def is_nonzero_address(value: str | None) -> bool:
    normalized = normalize_address(value)
    return bool(normalized) and normalized != ZERO_ADDRESS


def collateral_value_usd_from_oracle(
    collateral_assets_raw: int,
    oracle_price_raw: int | float | str,
    loan_decimals: int,
    loan_price_usd: float,
) -> float | None:
    """Return collateral USD from Morpho IOracle.price() raw output.

    Morpho oracles quote one collateral asset in loan-token units, scaled by
    1e36. Multiplying raw collateral assets by this price yields raw loan-token
    units after division by 1e36.
    """
    try:
        collateral_raw = int(collateral_assets_raw or 0)
        price_raw = float(oracle_price_raw)
        loan_decimals = int(loan_decimals)
        loan_price = float(loan_price_usd)
    except (TypeError, ValueError, OverflowError):
        return None
    if collateral_raw <= 0 or price_raw <= 0 or loan_price <= 0:
        return 0.0
    loan_units = (collateral_raw * price_raw) / MORPHO_ORACLE_PRICE_SCALE
    collateral_usd = (loan_units / (10 ** loan_decimals)) * loan_price
    if not math.isfinite(collateral_usd) or collateral_usd < 0:
        return None
    return float(collateral_usd)


def ensure_morpho_oracle_snapshot_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_oracle_snapshots (
            oracle String,
            block_number UInt64,
            timestamp DateTime,
            price_raw String,
            status LowCardinality(String),
            error String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (oracle, timestamp, block_number)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS morpho_oracle_backfill_runs (
            run_id String,
            started_at DateTime,
            finished_at DateTime,
            start_timestamp DateTime,
            end_timestamp DateTime,
            oracle_count UInt32,
            hour_count UInt32,
            ok_count UInt64,
            error_count UInt64,
            dry_run UInt8,
            details String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY run_id
        """
    )


def _chunked(items: list, size: int) -> Iterable[list]:
    for start in range(0, len(items), max(1, size)):
        yield items[start:start + max(1, size)]


def _sanitize_error(message: object) -> str:
    text = str(message or "")
    if " for url:" in text:
        text = text.split(" for url:", 1)[0]
    return text[:500]



def _parse_price_result(raw: object) -> tuple[str, str, str]:
    if raw is None or raw == "0x" or raw == b"":
        return "0", "NO_RESULT", "empty result"
    try:
        if isinstance(raw, bytes):
            price_raw = str(int.from_bytes(raw[-32:], "big"))
        else:
            price_raw = str(int(str(raw), 16))
    except (TypeError, ValueError):
        return "0", "BAD_RESULT", _sanitize_error(raw)
    return price_raw, "OK" if int(price_raw) > 0 else "ZERO_PRICE", ""


def _multicall_oracle_prices(
    rpc_url: str,
    batch: list[str],
    block_number: int,
    timestamp: dt.datetime,
    timeout_sec: int,
) -> list[OracleCallResult] | None:
    if not batch or abi_encode is None or abi_decode is None:
        return None
    calls = [
        (oracle, True, bytes.fromhex(MORPHO_ORACLE_PRICE_SELECTOR[2:]))
        for oracle in batch
    ]
    calldata = AGGREGATE3_SELECTOR + abi_encode(["(address,bool,bytes)[]"], [calls]).hex()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": MULTICALL3_ADDRESS, "data": calldata}, hex(int(block_number))],
    }
    try:
        response = requests.post(rpc_url, json=payload, timeout=timeout_sec)
        response.raise_for_status()
        item = response.json()
    except Exception as exc:  # pragma: no cover - network dependent
        log.warning("Multicall3 oracle snapshot failed at block %s: %s", block_number, _sanitize_error(exc))
        return None
    if item.get("error"):
        log.warning("Multicall3 oracle snapshot reverted at block %s: %s", block_number, _sanitize_error(item.get("error")))
        return None
    raw_result = item.get("result")
    if not raw_result or raw_result == "0x":
        return None
    try:
        decoded = abi_decode(["(bool,bytes)[]"], bytes.fromhex(raw_result[2:]))[0]
    except Exception as exc:
        log.warning("Multicall3 oracle snapshot decode failed at block %s: %s", block_number, _sanitize_error(exc))
        return None
    results: list[OracleCallResult] = []
    for oracle, call_result in zip(batch, decoded):
        success, return_data = call_result
        if not success:
            results.append(OracleCallResult(oracle, block_number, timestamp, "0", "REVERT", "execution reverted"))
            continue
        price_raw, status, error = _parse_price_result(return_data)
        results.append(OracleCallResult(oracle, block_number, timestamp, price_raw, status, error))
    return results


def batch_call_oracle_prices(
    rpc_url: str,
    oracles: list[str],
    block_number: int,
    timestamp: dt.datetime,
    *,
    batch_size: int = 100,
    timeout_sec: int = 120,
    retries: int = 2,
    sleep_sec: float = 0.25,
) -> list[OracleCallResult]:
    """Call IOracle.price() for many oracle contracts with JSON-RPC batching."""
    if not rpc_url:
        raise RuntimeError("MAINNET_RPC_URL is required for Morpho oracle snapshots")
    normalized = [normalize_address(oracle) for oracle in oracles if is_nonzero_address(oracle)]
    results: list[OracleCallResult] = []
    block_tag = hex(int(block_number))

    for batch in _chunked(normalized, batch_size):
        if len(batch) > 1:
            multicall_results = _multicall_oracle_prices(rpc_url, batch, block_number, timestamp, timeout_sec)
            if multicall_results is not None:
                results.extend(multicall_results)
                continue

        payload = [
            {
                "jsonrpc": "2.0",
                "id": idx,
                "method": "eth_call",
                "params": [{"to": oracle, "data": MORPHO_ORACLE_PRICE_SELECTOR}, block_tag],
            }
            for idx, oracle in enumerate(batch)
        ]
        response_payload = None
        last_error = ""
        for attempt in range(retries + 1):
            try:
                response = requests.post(rpc_url, json=payload, timeout=timeout_sec)
                response.raise_for_status()
                response_payload = response.json()
                break
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = _sanitize_error(exc)
                if attempt < retries:
                    time.sleep(sleep_sec * (attempt + 1))
        if response_payload is None:
            if len(batch) > 1:
                results.extend(
                    batch_call_oracle_prices(
                        rpc_url,
                        batch,
                        block_number,
                        timestamp,
                        batch_size=1,
                        timeout_sec=timeout_sec,
                        retries=retries,
                        sleep_sec=sleep_sec,
                    )
                )
            else:
                for oracle in batch:
                    results.append(OracleCallResult(oracle, block_number, timestamp, "0", "RPC_ERROR", last_error))
            continue
        if isinstance(response_payload, dict):
            response_payload = [response_payload]
        by_id = {item.get("id"): item for item in response_payload if isinstance(item, dict)}
        for idx, oracle in enumerate(batch):
            item = by_id.get(idx, {})
            if item.get("error"):
                err = item.get("error") or {}
                results.append(
                    OracleCallResult(oracle, block_number, timestamp, "0", "REVERT", _sanitize_error(err.get("message") or err))
                )
                continue
            raw = item.get("result")
            if not raw or raw == "0x":
                results.append(OracleCallResult(oracle, block_number, timestamp, "0", "NO_RESULT", "empty result"))
                continue
            price_raw, status, error = _parse_price_result(raw)
            results.append(OracleCallResult(oracle, block_number, timestamp, price_raw, status, error))
    return results


def _insert_rows_batched(ch, table: str, rows: list[list], column_names: list[str], batch_size: int = 20_000) -> int:
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start:start + batch_size]
        ch.insert(table, chunk, column_names=column_names)
        written += len(chunk)
    return written


def insert_oracle_snapshot_results(ch, results: list[OracleCallResult]) -> int:
    rows = [
        [r.oracle, r.block_number, r.timestamp, r.price_raw, r.status, r.error]
        for r in results
    ]
    return _insert_rows_batched(
        ch,
        "morpho_oracle_snapshots",
        rows,
        ["oracle", "block_number", "timestamp", "price_raw", "status", "error"],
    )


def default_rpc_url() -> str:
    return os.getenv("MAINNET_RPC_URL", "").strip()
