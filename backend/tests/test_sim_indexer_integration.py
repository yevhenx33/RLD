import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from web3 import Web3


ROOT = Path(__file__).resolve().parents[2]
INDEXER_DIR = ROOT / "backend" / "indexers"
sys.path.insert(0, str(INDEXER_DIR))

import indexer  # noqa: E402

_graphql_spec = importlib.util.spec_from_file_location(
    "sim_indexer_graphql",
    INDEXER_DIR / "api" / "graphql.py",
)
gql = importlib.util.module_from_spec(_graphql_spec)
assert _graphql_spec.loader is not None
sys.modules[_graphql_spec.name] = gql
_graphql_spec.loader.exec_module(gql)


TEST_DSN_ENV = "SIM_INDEXER_TEST_DATABASE_URL"
MARKET_ID = "0x" + "11" * 32
POOL_ID = "0x" + "22" * 32
OWNER = "0x0000000000000000000000000000000000000abc"
BROKER = "0x0000000000000000000000000000000000000b0b"
BROKER_ROUTER = "0x0000000000000000000000000000000000000a11"
DEPOSIT_ADAPTER = "0x0000000000000000000000000000000000000a12"


def _dsn() -> str:
    dsn = os.getenv(TEST_DSN_ENV)
    if not dsn:
        pytest.skip(f"{TEST_DSN_ENV} is not set")
    return dsn


def _run(coro):
    return asyncio.run(coro)


async def _connect_seeded():
    conn = await asyncpg.connect(_dsn())
    schema_sql = (INDEXER_DIR / "schema.sql").read_text()
    await conn.execute("DROP SCHEMA IF EXISTS public CASCADE")
    await conn.execute("CREATE SCHEMA public")
    await conn.execute(schema_sql)
    await _seed_market(conn)
    return conn


async def _seed_market(conn):
    await conn.execute(
        """
        INSERT INTO markets (
            market_id, deploy_block, deploy_timestamp,
            broker_factory, mock_oracle, twamm_hook, ghost_router,
            twap_engine, twap_engine_lens, swap_router, bond_factory,
            broker_executor, v4_quoter, broker_router, deposit_adapter,
            v4_position_manager, v4_state_view, pool_manager,
            collateral_token, collateral_symbol, position_token, position_symbol,
            wausdc, wausdc_symbol, wrlp, wrlp_symbol,
            pool_id, pool_fee, tick_spacing,
            min_col_ratio, maintenance_margin, liq_close_factor,
            funding_period_sec, debt_cap, created_at
        ) VALUES (
            $1, 100, 1700000000,
            $2, $3, $4, $5,
            $6, $7, $8, $9,
            $10, $11, $12, $13,
            $14, $15, $16,
            $17, 'waUSDC', $18, 'wRLP',
            $17, 'waUSDC', $18, 'wRLP',
            $19, 500, 5,
            '1500000000000000000', '1250000000000000000', '500000000000000000',
            2592000, '1000000000000', NOW()
        )
        """,
        MARKET_ID,
        "0x0000000000000000000000000000000000000100",
        "0x0000000000000000000000000000000000000101",
        "0x0000000000000000000000000000000000000102",
        "0x0000000000000000000000000000000000000103",
        "0x0000000000000000000000000000000000000104",
        "0x0000000000000000000000000000000000000105",
        "0x0000000000000000000000000000000000000106",
        "0x0000000000000000000000000000000000000107",
        "0x0000000000000000000000000000000000000108",
        "0x0000000000000000000000000000000000000109",
        BROKER_ROUTER,
        DEPOSIT_ADAPTER,
        "0x0000000000000000000000000000000000000110",
        "0x0000000000000000000000000000000000000111",
        "0x0000000000000000000000000000000000000112",
        "0x0000000000000000000000000000000000000113",
        "0x0000000000000000000000000000000000000114",
        POOL_ID,
    )
    await conn.execute(
        """
        INSERT INTO brokers (address, market_id, owner, created_block, created_tx)
        VALUES ($1, $2, $3, 101, $4)
        """,
        BROKER,
        MARKET_ID,
        OWNER,
        "0x" + "aa" * 32,
    )


def _topic_address(address: str) -> str:
    return "0x" + address.lower()[2:].zfill(64)


def _topic_uint(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _data_pair(amount1: int, amount2: int) -> str:
    return "0x" + amount1.to_bytes(32, "big").hex() + amount2.to_bytes(32, "big").hex()


def _router_log(event_sig: str, event_name: str, amount1: int, amount2: int, log_index: int, action: int | None = None):
    topics = [Web3.keccak(text=event_sig).hex(), _topic_address(BROKER)]
    if action is not None:
        topics.append(_topic_uint(action))
    return {
        "address": BROKER_ROUTER,
        "topics": topics,
        "data": _data_pair(amount1, amount2),
        "blockNumber": 200 + log_index,
        "_block_timestamp": 1700000200 + log_index,
        "transactionHash": "0x" + f"{log_index + 1:064x}",
        "logIndex": log_index,
        "_expected_event_name": event_name,
    }


async def _insert_router_event(conn, event_name: str, amount1: int, amount2: int, log_index: int, action: int | None = None):
    data = {
        "raw": _data_pair(amount1, amount2),
        "topics": [_topic_uint(0), _topic_address(BROKER)],
    }
    if action is not None:
        data["topics"].append(_topic_uint(action))
    await conn.execute(
        """
        INSERT INTO events (
            market_id, block_number, block_timestamp, tx_hash, log_index,
            event_name, contract_address, data
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
        """,
        MARKET_ID,
        300 + log_index,
        1700000300 + log_index,
        "0x" + f"{100 + log_index:064x}",
        log_index,
        event_name,
        BROKER_ROUTER,
        json.dumps(data),
    )


def test_address_market_map_includes_router_and_deposit_adapter():
    async def scenario():
        conn = await _connect_seeded()
        try:
            mapping = await indexer.build_address_market_map(conn)
            assert mapping[BROKER_ROUTER] == MARKET_ID
            assert mapping[DEPOSIT_ADAPTER] == MARKET_ID
            assert "0x0000000000000000000000000000000000000000" not in mapping
        finally:
            await conn.close()

    _run(scenario())


def test_dispatch_records_router_events_with_market_id():
    async def scenario():
        conn = await _connect_seeded()
        try:
            address_map = await indexer.build_address_market_map(conn)
            logs = [
                _router_log("SwapExecuted(address,uint8,uint256,uint256)", "RouterSwapExecuted", 100_000_000, 99_000_000, 1, 1),
                _router_log("ShortPositionUpdated(address,uint256,uint256)", "ShortPositionUpdated", 200_000_000, 198_000_000, 2),
                _router_log("ShortPositionClosed(address,uint256,uint256)", "ShortPositionClosed", 50_000_000, 51_000_000, 3),
                _router_log("Deposited(address,uint256,uint256)", "Deposited", 10_000_000, 9_900_000, 4),
            ]
            for log_entry in logs:
                await indexer.dispatch(log_entry, conn, {}, address_map, None)

            rows = await conn.fetch(
                "SELECT event_name, market_id, contract_address, data FROM events ORDER BY log_index"
            )
            assert [row["event_name"] for row in rows] == [log["_expected_event_name"] for log in logs]
            assert all(row["market_id"] == MARKET_ID for row in rows)
            assert all(row["contract_address"] == BROKER_ROUTER for row in rows)
            first_data = rows[0]["data"]
            if isinstance(first_data, str):
                first_data = json.loads(first_data)
            assert first_data["topics"][1] == _topic_address(BROKER)
            assert first_data["topics"][2] == _topic_uint(1)
        finally:
            await conn.close()

    _run(scenario())


def test_graphql_market_info_exposes_router_and_deposit_adapter(monkeypatch):
    async def scenario():
        conn = await _connect_seeded()
        await conn.close()
        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=1)

        async def test_pool():
            return pool

        monkeypatch.setattr(gql, "get_pool", test_pool)
        try:
            payload = await gql.Query().market_info(MARKET_ID)
            assert payload["brokerRouter"] == BROKER_ROUTER
            assert payload["broker_router"] == BROKER_ROUTER
            assert payload["depositAdapter"] == DEPOSIT_ADAPTER
            assert payload["deposit_adapter"] == DEPOSIT_ADAPTER
            assert payload["infrastructure"]["brokerRouter"] == BROKER_ROUTER
            assert payload["infrastructure"]["depositAdapter"] == DEPOSIT_ADAPTER
            assert payload["infrastructure"]["deposit_adapter"] == DEPOSIT_ADAPTER
        finally:
            await pool.close()

    _run(scenario())


def test_graphql_broker_operations_decodes_router_events(monkeypatch):
    async def scenario():
        conn = await _connect_seeded()
        try:
            await _insert_router_event(conn, "RouterSwapExecuted", 100_000_000, 99_000_000, 1, 1)
            await _insert_router_event(conn, "RouterSwapExecuted", 20_000_000, 21_000_000, 2, 2)
            await _insert_router_event(conn, "RouterSwapExecuted", 30_000_000, 31_000_000, 3, 9)
            await _insert_router_event(conn, "ShortPositionUpdated", 200_000_000, 198_000_000, 4)
            await _insert_router_event(conn, "ShortPositionClosed", 50_000_000, 51_000_000, 5)
            await _insert_router_event(conn, "Deposited", 10_000_000, 9_900_000, 6)
        finally:
            await conn.close()

        pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=1)

        async def test_pool():
            return pool

        monkeypatch.setattr(gql, "get_pool", test_pool)
        try:
            ops = await gql.Query().broker_operations(OWNER, limit=10)
            by_type = {op["type"]: op for op in ops}
            assert set(by_type) == {"OPEN_LONG", "CLOSE_LONG", "SWAP", "OPEN_SHORT", "CLOSE_SHORT", "DEPOSIT"}
            assert by_type["OPEN_LONG"]["amount1"] == 100
            assert by_type["OPEN_LONG"]["amount2"] == 99
            assert by_type["CLOSE_LONG"]["amount1"] == 20
            assert by_type["CLOSE_LONG"]["amount2"] == 21
            assert by_type["DEPOSIT"]["amount1"] == 10
            assert by_type["DEPOSIT"]["amount2"] == 9.9
        finally:
            await pool.close()

    _run(scenario())
