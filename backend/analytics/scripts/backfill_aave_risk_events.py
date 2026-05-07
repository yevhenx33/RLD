#!/usr/bin/env python3
"""Backfill Aave V3 risk-configuration events and seed serving state."""

import argparse
import asyncio
import datetime
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

import clickhouse_connect
import hypersync

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analytics.aave_constants import (  # noqa: E402
    AAVE_TOPIC_COLLATERAL_CONFIGURATION_CHANGED,
    AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED_UINT256,
    AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED,
    AAVE_V3_DEPLOY_BLOCK,
    AAVE_V3_POOL,
    AAVE_V3_POOL_CONFIGURATOR,
)
from analytics.base import insert_rows_batched  # noqa: E402
from analytics.collector import BLOCK_FIELDS, LOG_FIELDS, build_block_ts_map, require_envio_token  # noqa: E402
from analytics.config import apply_env_from_config  # noqa: E402
from analytics.sources.aave_v3 import AaveV3Source  # noqa: E402

apply_env_from_config()

log = logging.getLogger("aave-risk-backfill")

RISK_TOPICS = [
    AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED,
    AAVE_TOPIC_COLLATERAL_CONFIGURATION_CHANGED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED,
    AAVE_TOPIC_EMODE_CATEGORY_ADDED_UINT256,
    AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED,
]
EVENT_NAMES = {
    AAVE_TOPIC_RESERVE_CONFIGURATION_CHANGED: "ReserveConfigurationChanged",
    AAVE_TOPIC_COLLATERAL_CONFIGURATION_CHANGED: "CollateralConfigurationChanged",
    AAVE_TOPIC_EMODE_CATEGORY_ADDED: "EModeCategoryAdded",
    AAVE_TOPIC_EMODE_CATEGORY_ADDED_UINT256: "EModeCategoryAdded",
    AAVE_TOPIC_EMODE_ASSET_CATEGORY_CHANGED: "EModeAssetCategoryChanged",
}
RAW_COLUMNS = [
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
]
API_LATEST_COLUMNS = [
    "protocol",
    "entity_id",
    "symbol",
    "target_id",
    "timestamp",
    "supply_usd",
    "borrow_usd",
    "supply_apy",
    "borrow_apy",
    "utilization",
    "price_usd",
    "ltv",
    "liquidation_threshold",
    "liquidation_penalty",
    "e_mode_category",
    "e_mode_ltv",
    "e_mode_liquidation_threshold",
    "e_mode_liquidation_penalty",
    "e_mode_label",
]


@dataclass
class RawAaveLog:
    block_number: int
    topics: list[str]
    data: str


def _clickhouse_client():
    settings = {}
    if os.getenv("CLICKHOUSE_ASYNC_INSERT", "true").strip().lower() in {"1", "true", "yes"}:
        settings["async_insert"] = 1
        wait = os.getenv("CLICKHOUSE_WAIT_FOR_ASYNC_INSERT", "true").strip().lower()
        settings["wait_for_async_insert"] = 1 if wait in {"1", "true", "yes"} else 0
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        settings=settings,
        connect_timeout=int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "5")),
        send_receive_timeout=int(os.getenv("CLICKHOUSE_SEND_RECEIVE_TIMEOUT", "120")),
        query_retries=int(os.getenv("CLICKHOUSE_QUERY_RETRIES", "1")),
        autogenerate_session_id=os.getenv("CLICKHOUSE_AUTOGENERATE_SESSION_ID", "false").strip().lower()
        in {"1", "true", "yes"},
    )


def _hypersync_client() -> hypersync.HypersyncClient:
    return hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=require_envio_token(),
        )
    )


async def collect_risk_events(ch, from_block: int, to_block: Optional[int], batch_size: int, dry_run: bool) -> int:
    hs_client = _hypersync_client()
    head_block = int(to_block) if to_block is not None else int(await hs_client.get_height()) - 3
    if head_block < from_block:
        log.info("No collection needed: from_block=%s head_block=%s", from_block, head_block)
        return 0

    total = 0
    log_selection = hypersync.LogSelection(
        address=[AAVE_V3_POOL, AAVE_V3_POOL_CONFIGURATOR],
        topics=[RISK_TOPICS],
    )
    current_start = from_block
    while current_start <= head_block:
        current_end = min(current_start + batch_size - 1, head_block)
        mempool_logs = []
        mempool_blocks = []
        cursor = current_start
        while cursor <= current_end:
            query = hypersync.Query(
                from_block=cursor,
                to_block=current_end,
                logs=[log_selection],
                field_selection=hypersync.FieldSelection(
                    log=LOG_FIELDS,
                    block=BLOCK_FIELDS,
                ),
            )
            res = await hs_client.get(query)
            mempool_logs.extend(res.data.logs)
            mempool_blocks.extend(res.data.blocks)
            if int(res.next_block) <= cursor:
                break
            cursor = int(res.next_block)

        rows = []
        block_ts_map = build_block_ts_map(mempool_blocks)
        for entry in mempool_logs:
            topics = entry.topics or []
            if not topics or topics[0] not in EVENT_NAMES:
                continue
            ts = block_ts_map.get(entry.block_number, datetime.datetime.now(datetime.UTC))
            rows.append(
                [
                    int(entry.block_number),
                    ts.replace(tzinfo=None),
                    entry.transaction_hash or "",
                    int(entry.log_index or 0),
                    (entry.address or "").lower(),
                    EVENT_NAMES[topics[0]],
                    topics[0],
                    topics[1] if len(topics) > 1 else None,
                    topics[2] if len(topics) > 2 else None,
                    topics[3] if len(topics) > 3 else None,
                    entry.data or "",
                ]
            )

        if rows and not dry_run:
            insert_rows_batched(ch, "aave_events", rows, RAW_COLUMNS)
        total += len(rows)
        log.info("Collected %s Aave risk logs in blocks %s -> %s", len(rows), current_start, current_end)
        current_start = current_end + 1

    return total


def seed_risk_state(ch) -> tuple[int, int]:
    source = AaveV3Source()
    source.get_cursor(ch)
    rows = ch.query(
        """
        SELECT block_number, topic0, topic1, topic2, topic3, data
        FROM aave_events
        WHERE event_name IN (
            'ReserveConfigurationChanged',
            'CollateralConfigurationChanged',
            'EModeCategoryAdded',
            'EModeAssetCategoryChanged'
        )
        ORDER BY block_number, log_index
        """
    ).result_rows
    for block_number, topic0, topic1, topic2, topic3, data in rows:
        topics = [str(topic0)]
        topics.extend(str(topic) for topic in (topic1, topic2, topic3) if topic)
        source.decode(RawAaveLog(block_number=int(block_number), topics=topics, data=str(data)), {})

    reserve_rows = [
        [
            entity_id,
            state.ltv,
            state.liquidation_threshold,
            state.liquidation_penalty,
            state.e_mode_category,
        ]
        for entity_id, state in source._reserves.items()
        if state.ltv > 0 or state.liquidation_threshold > 0 or state.e_mode_category > 0
    ]
    emode_rows = [
        [
            category_id,
            category.ltv,
            category.liquidation_threshold,
            category.liquidation_penalty,
            category.price_source,
            category.label,
        ]
        for category_id, category in source._emode_categories.items()
    ]
    insert_rows_batched(
        ch,
        "aave_reserve_risk_state",
        reserve_rows,
        ["entity_id", "ltv", "liquidation_threshold", "liquidation_penalty", "e_mode_category"],
    )
    insert_rows_batched(
        ch,
        "aave_emode_categories",
        emode_rows,
        ["category_id", "ltv", "liquidation_threshold", "liquidation_penalty", "price_source", "label"],
    )
    return len(reserve_rows), len(emode_rows)


def refresh_api_latest(ch) -> int:
    columns_sql = ", ".join(API_LATEST_COLUMNS)
    risk_select = """
        ifNull(r.ltv, 0) AS ltv,
        ifNull(r.liquidation_threshold, 0) AS liquidation_threshold,
        ifNull(r.liquidation_penalty, 0) AS liquidation_penalty,
        toUInt8(ifNull(r.e_mode_category, 0)) AS e_mode_category,
        ifNull(c.ltv, 0) AS e_mode_ltv,
        ifNull(c.liquidation_threshold, 0) AS e_mode_liquidation_threshold,
        ifNull(c.liquidation_penalty, 0) AS e_mode_liquidation_penalty,
        ifNull(c.label, '') AS e_mode_label
    """
    ch.command(
        f"""
        INSERT INTO api_market_latest ({columns_sql})
        SELECT
            l.protocol,
            l.entity_id,
            l.symbol,
            l.target_id,
            l.timestamp,
            l.supply_usd,
            l.borrow_usd,
            l.supply_apy,
            l.borrow_apy,
            l.utilization,
            l.price_usd,
            {risk_select}
        FROM (SELECT * FROM api_market_latest FINAL) AS l
        LEFT JOIN (SELECT * FROM aave_reserve_risk_state FINAL) AS r ON r.entity_id = l.entity_id
        LEFT JOIN (SELECT * FROM aave_emode_categories FINAL) AS c ON c.category_id = r.e_mode_category
        WHERE l.protocol = 'AAVE_MARKET'
        """
    )
    value = ch.command(
        """
        SELECT count()
        FROM api_market_latest FINAL
        WHERE protocol = 'AAVE_MARKET'
          AND (ltv > 0 OR liquidation_threshold > 0 OR e_mode_category > 0)
        """
    )
    return int(value or 0)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Aave V3 reserve config and E-Mode events")
    parser.add_argument("--from-block", type=int, default=AAVE_V3_DEPLOY_BLOCK)
    parser.add_argument("--to-block", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=500_000)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-collect", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ch = _clickhouse_client()
    try:
        source = AaveV3Source()
        source.get_cursor(ch)
        collected = 0
        if not args.skip_collect:
            collected = await collect_risk_events(ch, args.from_block, args.to_block, args.batch_size, args.dry_run)
        if args.dry_run:
            log.info("Dry run complete: discovered %s Aave risk logs", collected)
            return
        reserve_count, emode_count = seed_risk_state(ch)
        latest_count = refresh_api_latest(ch)
        log.info(
            "Backfill complete: collected=%s reserve_state=%s emode_categories=%s latest_markets_with_risk=%s",
            collected,
            reserve_count,
            emode_count,
            latest_count,
        )
    finally:
        try:
            ch.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
