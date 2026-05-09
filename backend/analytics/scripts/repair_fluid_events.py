"""Repair and validate Fluid raw event completeness against Ethereum RPC."""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Any

import requests

from analytics.fluid_full_coverage import ETHEREUM_CHAIN_ID, ensure_fluid_full_coverage_tables
from analytics.processor import SimulatedLog
from analytics.protocols import FLUID_MARKET
from analytics.sources.fluid import (
    FLUID_GENESIS_BLOCK,
    FLUID_LIQUIDITY,
    TOPIC_LOG_OPERATE,
    TOPIC_LOG_UPDATE_EXCHANGE_PRICES,
    FluidSource,
)

EVENT_TOPICS = (TOPIC_LOG_OPERATE, TOPIC_LOG_UPDATE_EXCHANGE_PRICES)
EVENT_NAMES = {
    TOPIC_LOG_OPERATE: "LogOperate",
    TOPIC_LOG_UPDATE_EXCHANGE_PRICES: "LogUpdateExchangePrices",
}
CONFIRMATION_BLOCKS = 3
MASK_64 = (1 << 64) - 1


@dataclass(frozen=True)
class RepairResult:
    from_block: int
    to_block: int
    rpc_logs: int
    db_logs: int
    missing_logs: int
    inserted_logs: int
    earliest_missing_block: int | None
    dry_run: bool


def _hex_block(block_number: int) -> str:
    return hex(int(block_number))


def _int_hex(value: str | int | None) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _rpc_url(args) -> str:
    url = (getattr(args, "rpc_url", None) or os.getenv("MAINNET_RPC_URL") or os.getenv("ETH_RPC_URL") or "").strip()
    if not url:
        raise RuntimeError("MAINNET_RPC_URL or --rpc-url is required for Fluid RPC repair")
    return url


def _rpc_call(rpc_url: str, method: str, params: list[Any], *, timeout: int = 60, retries: int = 2) -> Any:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                rpc_url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if "error" in payload:
                raise RuntimeError(str(payload["error"]))
            return payload["result"]
        except Exception as exc:  # pragma: no cover - exercised in integration runs
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(2.0 * (attempt + 1), 5.0))
    raise RuntimeError(f"RPC {method} failed: {last_error}")


def _current_confirmed_block(rpc_url: str, *, timeout: int, retries: int) -> int:
    head = _int_hex(_rpc_call(rpc_url, "eth_blockNumber", [], timeout=timeout, retries=retries))
    return max(0, head - CONFIRMATION_BLOCKS)


def _block_timestamp(rpc_url: str, block_number: int, cache: dict[int, dt.datetime], *, timeout: int, retries: int) -> dt.datetime:
    block_number = int(block_number)
    if block_number not in cache:
        block = _rpc_call(rpc_url, "eth_getBlockByNumber", [_hex_block(block_number), False], timeout=timeout, retries=retries)
        if not block:
            raise RuntimeError(f"RPC returned no block for {block_number}")
        cache[block_number] = dt.datetime.fromtimestamp(_int_hex(block.get("timestamp")), tz=dt.UTC).replace(tzinfo=None)
    return cache[block_number]


def _topic(value: str | None) -> str:
    return str(value or "").lower()


def _log_key(log: dict[str, Any]) -> tuple[int, int, str, str]:
    topics = log.get("topics") or []
    return (
        _int_hex(log.get("blockNumber")),
        _int_hex(log.get("logIndex")),
        _topic(topics[0] if topics else ""),
        _topic(log.get("transactionHash")),
    )


def _row_key(row) -> tuple[int, int, str, str]:
    return (int(row[0]), int(row[1]), _topic(row[2]), _topic(row[3]))


def _topic_token(log: dict[str, Any]) -> str:
    topics = log.get("topics") or []
    topic0 = _topic(topics[0] if topics else "")
    token_topic = topics[2] if topic0 == TOPIC_LOG_OPERATE and len(topics) > 2 else topics[1] if len(topics) > 1 else ""
    return "0x" + str(token_topic).removeprefix("0x")[-40:].lower()


def _event_name(topic0: str) -> str:
    return EVENT_NAMES.get(_topic(topic0), "")


def _fetch_rpc_logs(rpc_url: str, from_block: int, to_block: int, *, timeout: int, retries: int) -> list[dict[str, Any]]:
    if to_block < from_block:
        return []
    logs = _rpc_call(
        rpc_url,
        "eth_getLogs",
        [
            {
                "fromBlock": _hex_block(from_block),
                "toBlock": _hex_block(to_block),
                "address": FLUID_LIQUIDITY,
                "topics": [[TOPIC_LOG_OPERATE, TOPIC_LOG_UPDATE_EXCHANGE_PRICES]],
            }
        ],
        timeout=timeout,
        retries=retries,
    )
    return list(logs or [])


def _fetch_rpc_logs_resilient(rpc_url: str, from_block: int, to_block: int, *, timeout: int, retries: int) -> list[dict[str, Any]]:
    try:
        return _fetch_rpc_logs(rpc_url, from_block, to_block, timeout=timeout, retries=retries)
    except RuntimeError:
        if int(from_block) >= int(to_block):
            raise
        mid = (int(from_block) + int(to_block)) // 2
        left = _fetch_rpc_logs_resilient(rpc_url, from_block, mid, timeout=timeout, retries=retries)
        right = _fetch_rpc_logs_resilient(rpc_url, mid + 1, to_block, timeout=timeout, retries=retries)
        return left + right


def _existing_keys(ch, from_block: int, to_block: int) -> set[tuple[int, int, str, str]]:
    rows = ch.query(
        """
        SELECT block_number, log_index, lower(topic0), lower(tx_hash)
        FROM fluid_events
        WHERE block_number >= %(from_block)s AND block_number <= %(to_block)s
          AND lower(contract) = %(contract)s
          AND lower(topic0) IN (%(operate)s, %(exchange)s)
        """,
        parameters={
            "from_block": int(from_block),
            "to_block": int(to_block),
            "contract": FLUID_LIQUIDITY.lower(),
            "operate": TOPIC_LOG_OPERATE,
            "exchange": TOPIC_LOG_UPDATE_EXCHANGE_PRICES,
        },
    ).result_rows
    return {_row_key(row) for row in rows}

def _insert_missing_logs(ch, rpc_url: str, logs: list[dict[str, Any]], block_cache: dict[int, dt.datetime], *, timeout: int, retries: int) -> int:
    rows = []
    for log in logs:
        topics = list(log.get("topics") or [])
        block_number = _int_hex(log.get("blockNumber"))
        rows.append(
            [
                block_number,
                _block_timestamp(rpc_url, block_number, block_cache, timeout=timeout, retries=retries),
                _topic(log.get("transactionHash")),
                _int_hex(log.get("logIndex")),
                _topic(log.get("address")),
                _event_name(topics[0] if topics else ""),
                _topic(topics[0] if len(topics) > 0 else ""),
                _topic(topics[1] if len(topics) > 1 else "") or None,
                _topic(topics[2] if len(topics) > 2 else "") or None,
                _topic(topics[3] if len(topics) > 3 else "") or None,
                str(log.get("data") or "0x"),
            ]
        )
    if not rows:
        return 0
    ch.insert(
        "fluid_events",
        rows,
        column_names=[
            "block_number",
            "block_timestamp",
            "tx_hash",
            "log_index",
            "contract",
            "event_name",
            "topic0",
            "topic1",
            "topic2",
            "topic3",
            "data",
        ],
    )
    return len(rows)


def _scan_missing_logs(ch, rpc_url: str, from_block: int, to_block: int, *, batch_blocks: int, timeout: int, retries: int, dry_run: bool) -> RepairResult:
    if batch_blocks <= 0:
        raise ValueError("batch_blocks must be positive")
    block_cache: dict[int, dt.datetime] = {}
    rpc_total = 0
    db_total = 0
    missing_total = 0
    inserted_total = 0
    earliest_missing: int | None = None
    current = int(from_block)
    while current <= int(to_block):
        end = min(current + int(batch_blocks) - 1, int(to_block))
        rpc_logs = _fetch_rpc_logs_resilient(rpc_url, current, end, timeout=timeout, retries=retries)
        existing = _existing_keys(ch, current, end)
        rpc_total += len(rpc_logs)
        db_total += len(existing)
        missing = [log for log in rpc_logs if _log_key(log) not in existing]
        if missing:
            block_min = min(_int_hex(log.get("blockNumber")) for log in missing)
            earliest_missing = block_min if earliest_missing is None else min(earliest_missing, block_min)
        missing_total += len(missing)
        if missing and not dry_run:
            inserted_total += _insert_missing_logs(ch, rpc_url, missing, block_cache, timeout=timeout, retries=retries)
        current = end + 1
    return RepairResult(
        from_block=int(from_block),
        to_block=int(to_block),
        rpc_logs=rpc_total,
        db_logs=db_total,
        missing_logs=missing_total,
        inserted_logs=inserted_total,
        earliest_missing_block=earliest_missing,
        dry_run=bool(dry_run),
    )


def _format_ts(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _delete_if_exists(ch, table: str, where_clause: str) -> None:
    try:
        ch.command(f"DELETE FROM {table} WHERE {where_clause}")
    except Exception:
        # Older deployments may not have every serving aggregate table yet.
        return


def replay_fluid_from_raw(ch, *, from_block: int, to_block: int, batch_blocks: int = 50_000, dry_run: bool = False) -> dict[str, Any]:
    """Replay Fluid raw logs through FluidSource.decode using raw events as truth."""
    if from_block <= 0 or to_block < from_block:
        raise ValueError("invalid replay block range")
    if batch_blocks <= 0:
        raise ValueError("batch_blocks must be positive")

    source = FluidSource()
    source._ensure_tables(ch)
    range_info = ch.query(
        """
        SELECT min(block_timestamp), max(block_timestamp), count()
        FROM fluid_events FINAL
        WHERE block_number >= %(from_block)s AND block_number <= %(to_block)s
          AND lower(contract) = %(contract)s
          AND lower(topic0) IN (%(operate)s, %(exchange)s)
        """,
        parameters={
            "from_block": int(from_block),
            "to_block": int(to_block),
            "contract": FLUID_LIQUIDITY.lower(),
            "operate": TOPIC_LOG_OPERATE,
            "exchange": TOPIC_LOG_UPDATE_EXCHANGE_PRICES,
        },
    ).result_rows[0]
    if not range_info or not range_info[2]:
        return {"fromBlock": from_block, "toBlock": to_block, "rawRows": 0, "decodedRows": 0, "mergedRows": 0, "dryRun": dry_run}

    min_ts = range_info[0].replace(minute=0, second=0, microsecond=0)
    max_ts = range_info[1].replace(minute=0, second=0, microsecond=0)
    if dry_run:
        return {
            "fromBlock": from_block,
            "toBlock": to_block,
            "rawRows": int(range_info[2]),
            "cleanupStart": _format_ts(min_ts),
            "cleanupEnd": _format_ts(max_ts),
            "decodedRows": 0,
            "mergedRows": 0,
            "dryRun": True,
        }

    start = _format_ts(min_ts)
    end = _format_ts(max_ts)
    _delete_if_exists(ch, "fluid_reserve_metrics", f"timestamp >= '{start}' AND timestamp <= '{end}'")
    _delete_if_exists(ch, "fluid_timeseries", f"protocol = '{FLUID_MARKET}' AND timestamp >= '{start}' AND timestamp <= '{end}'")
    _delete_if_exists(ch, "market_timeseries", f"protocol = '{FLUID_MARKET}' AND timestamp >= '{start}' AND timestamp <= '{end}'")
    _delete_if_exists(ch, "api_market_latest", f"protocol = '{FLUID_MARKET}'")
    _delete_if_exists(ch, "api_market_timeseries_hourly_agg", f"protocol = '{FLUID_MARKET}' AND ts >= '{start}' AND ts <= '{end}'")
    _delete_if_exists(ch, "api_protocol_tvl_entity_weekly_agg", f"protocol IN ('FLUID', '{FLUID_MARKET}') AND day >= toStartOfWeek(toDateTime('{start}')) AND day <= toStartOfWeek(toDateTime('{end}'))")

    decoded_total = 0
    merged_total = 0
    raw_total = 0
    replay_start = max(int(FLUID_GENESIS_BLOCK), 0)
    current = replay_start
    while current <= int(to_block):
        batch_end = min(current + int(batch_blocks) - 1, int(to_block))
        rows = ch.query(
            f"""
            SELECT block_number, block_timestamp, tx_hash, log_index, contract,
                   event_name, topic0, topic1, topic2, topic3, data
            FROM fluid_events FINAL
            WHERE block_number >= {current} AND block_number <= {batch_end}
              AND lower(contract) = '{FLUID_LIQUIDITY.lower()}'
              AND lower(topic0) IN ('{TOPIC_LOG_OPERATE}', '{TOPIC_LOG_UPDATE_EXCHANGE_PRICES}')
            ORDER BY block_number ASC, log_index ASC
            """
        ).result_rows
        raw_total += len(rows)
        decoded_rows = []
        block_ts_map = {row[0]: row[1] for row in rows}
        for row in rows:
            log_entry = SimulatedLog(row)
            decoded = source.decode(log_entry, block_ts_map)
            if decoded and int(row[0]) >= int(from_block):
                decoded_rows.append(decoded)
        if batch_end < int(from_block):
            source._touched_tokens.clear()
        if decoded_rows:
            decoded_total += len(decoded_rows)
            merged_total += source.merge(ch, decoded_rows)
        current = batch_end + 1

    source._touched_tokens = set(source._states.keys())
    source._persist_state(ch)
    source._persist_oracle_support(ch)

    return {
        "fromBlock": from_block,
        "toBlock": to_block,
        "rawRows": raw_total,
        "cleanupStart": start,
        "cleanupEnd": end,
        "decodedRows": decoded_total,
        "mergedRows": merged_total,
        "stateRows": len(source._states),
        "dryRun": False,
    }


def _bigmath(packed: int) -> int:
    return (packed >> 8) << (packed & 0xFF)


def _decode_operate_amounts(log: dict[str, Any], decimals: int) -> tuple[float, float] | None:
    raw = str(log.get("data") or "").removeprefix("0x")
    if len(raw) < 384:
        return None
    data = bytes.fromhex(raw)
    w4 = int.from_bytes(data[128:160], "big")
    w5 = int.from_bytes(data[160:192], "big")
    sup_int = _bigmath(w4 & MASK_64)
    sup_free = _bigmath((w4 >> 64) & MASK_64)
    bor_int = _bigmath((w4 >> 128) & MASK_64)
    bor_free = _bigmath((w4 >> 192) & MASK_64)
    sup_ep = (w5 >> 91) & MASK_64 or int(1e12)
    bor_ep = (w5 >> 155) & MASK_64 or int(1e12)
    scale = float(10 ** int(decimals))
    return ((sup_int * sup_ep / 1e12 + sup_free) / scale, (bor_int * bor_ep / 1e12 + bor_free) / scale)


def _relative_diff(left: float, right: float) -> float:
    return abs(float(left or 0.0) - float(right or 0.0)) / max(abs(float(left or 0.0)), abs(float(right or 0.0)), 1.0)


def validate_rpc(ch, args) -> dict[str, Any]:
    rpc_url = _rpc_url(args)
    timeout = int(getattr(args, "http_timeout_sec", 60))
    retries = int(getattr(args, "retries", 2))
    from_block = int(args.from_block)
    to_block = int(args.to_block) if int(args.to_block or 0) > 0 else _current_confirmed_block(rpc_url, timeout=timeout, retries=retries)
    if from_block <= 0:
        recent_blocks = int(getattr(args, "recent_blocks", 500))
        from_block = max(FLUID_GENESIS_BLOCK, to_block - recent_blocks + 1)

    started_at = dt.datetime.utcnow().replace(microsecond=0)
    batch_blocks = int(getattr(args, "batch_blocks", 200))
    if batch_blocks <= 0:
        raise ValueError("batch_blocks must be positive")
    rpc_logs = []
    current = from_block
    while current <= to_block:
        end = min(current + batch_blocks - 1, to_block)
        rpc_logs.extend(_fetch_rpc_logs_resilient(rpc_url, current, end, timeout=timeout, retries=retries))
        current = end + 1
    existing = _existing_keys(ch, from_block, to_block)
    rpc_keys = {_log_key(log) for log in rpc_logs}
    missing = [log for log in rpc_logs if _log_key(log) not in existing]
    extra = sorted(existing - rpc_keys)

    states = {
        str(row[0]).lower(): row
        for row in ch.query(
            """
            SELECT lower(token), symbol, decimals, total_supply_tokens, total_borrow_tokens, last_event_block
            FROM fluid_reserve_state FINAL
            """
        ).result_rows
    }
    rpc_latest: dict[str, dict[str, Any]] = {}
    for log in rpc_logs:
        token = _topic_token(log)
        key = (_int_hex(log.get("blockNumber")), _int_hex(log.get("logIndex")))
        old = rpc_latest.get(token)
        if old is None or key > (_int_hex(old.get("blockNumber")), _int_hex(old.get("logIndex"))):
            rpc_latest[token] = log

    latest_mismatches = []
    max_supply_diff = 0.0
    max_borrow_diff = 0.0
    for token, log in rpc_latest.items():
        state = states.get(token)
        if not state:
            latest_mismatches.append({"token": token, "symbol": "UNKNOWN", "reason": "missing reserve state"})
            continue
        rpc_block = _int_hex(log.get("blockNumber"))
        if int(state[5] or 0) < rpc_block:
            item = {"token": token, "symbol": str(state[1]), "rpcBlock": rpc_block, "dbBlock": int(state[5] or 0)}
            if _topic((log.get("topics") or [""])[0]) == TOPIC_LOG_OPERATE:
                decoded = _decode_operate_amounts(log, int(state[2] or 18))
                if decoded:
                    supply_diff = _relative_diff(decoded[0], float(state[3] or 0.0))
                    borrow_diff = _relative_diff(decoded[1], float(state[4] or 0.0))
                    max_supply_diff = max(max_supply_diff, supply_diff)
                    max_borrow_diff = max(max_borrow_diff, borrow_diff)
                    item["relativeSupplyDiff"] = supply_diff
                    item["relativeBorrowDiff"] = borrow_diff
            latest_mismatches.append(item)

    finished_at = dt.datetime.utcnow().replace(microsecond=0)
    mismatch_count = len(missing) + len(extra) + len(latest_mismatches)
    status = "OK" if mismatch_count == 0 else "DRIFT"
    details = {
        "missingSamples": [list(_log_key(log)) + [_topic_token(log)] for log in missing[:20]],
        "extraSamples": [list(item) for item in extra[:20]],
        "latestMismatches": latest_mismatches[:50],
        "rpcTopics": dict(Counter(_topic((log.get("topics") or [""])[0]) for log in rpc_logs)),
    }
    ensure_fluid_full_coverage_tables(ch)
    ch.insert(
        "fluid_rpc_validation_runs",
        [[
            str(uuid.uuid4()),
            ETHEREUM_CHAIN_ID,
            FLUID_MARKET,
            started_at,
            finished_at,
            len(rpc_logs),
            mismatch_count,
            max_supply_diff,
            max_borrow_diff,
            status,
            json.dumps(details, sort_keys=True),
        ]],
        column_names=[
            "run_id",
            "chain_id",
            "target",
            "started_at",
            "finished_at",
            "checked_count",
            "mismatch_count",
            "max_relative_supply_diff",
            "max_relative_borrow_diff",
            "status",
            "details",
        ],
    )
    return {
        "status": status,
        "fromBlock": from_block,
        "toBlock": to_block,
        "rpcLogs": len(rpc_logs),
        "dbLogs": len(existing),
        "missingLogs": len(missing),
        "extraLogs": len(extra),
        "latestMismatches": latest_mismatches,
        "maxRelativeSupplyDiff": max_supply_diff,
        "maxRelativeBorrowDiff": max_borrow_diff,
    }


def run_repair(args, ch) -> int:
    rpc_url = _rpc_url(args)
    timeout = int(getattr(args, "http_timeout_sec", 60))
    retries = int(getattr(args, "retries", 2))
    to_block = int(args.to_block) if int(args.to_block or 0) > 0 else _current_confirmed_block(rpc_url, timeout=timeout, retries=retries)
    source = FluidSource()
    source._ensure_tables(ch)
    result = _scan_missing_logs(
        ch,
        rpc_url,
        int(args.from_block),
        to_block,
        batch_blocks=int(args.batch_blocks),
        timeout=timeout,
        retries=retries,
        dry_run=bool(args.dry_run),
    )
    payload = result.__dict__.copy()
    if getattr(args, "replay", False) and (result.earliest_missing_block is not None or getattr(args, "force_replay", False)):
        replay_from = int(getattr(args, "replay_from_block", 0) or result.earliest_missing_block or args.from_block)
        payload["replay"] = replay_fluid_from_raw(
            ch,
            from_block=replay_from,
            to_block=to_block,
            batch_blocks=int(getattr(args, "replay_batch_blocks", 50_000)),
            dry_run=bool(args.dry_run),
        )
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return 0


def run_validate(args, ch) -> int:
    payload = validate_rpc(ch, args)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    if getattr(args, "fail_on_drift", False) and payload["status"] != "OK":
        return 1
    return 0
