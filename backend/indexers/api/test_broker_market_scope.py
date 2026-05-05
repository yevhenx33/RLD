import importlib.util
import sys
import unittest
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
INDEXERS_DIR = API_DIR.parent
while str(API_DIR) in sys.path:
    sys.path.remove(str(API_DIR))
sys.path.insert(0, str(INDEXERS_DIR))

spec = importlib.util.spec_from_file_location("rld_graphql", API_DIR / "graphql.py")
graphql = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = graphql
spec.loader.exec_module(graphql)

OWNER = "0x" + "11" * 20
OTHER_OWNER = "0x" + "22" * 20
MARKET_A = "0xmarket-a"
MARKET_B = "0xmarket-b"
POOL_A = "0xpool-a"
POOL_B = "0xpool-b"
BROKER_A_NEW = "0x" + "aa" * 20
BROKER_A_OLD = "0x" + "bb" * 20
BROKER_B = "0x" + "cc" * 20
OTHER_BROKER = "0x" + "dd" * 20


def broker_topic(address):
    return "0x" + address[2:].zfill(64)


class FakeAcquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return FakeAcquire(self.conn)


class FakeConn:
    def __init__(self):
        self.brokers = [
            self._broker(BROKER_A_NEW, MARKET_A, OWNER, 30),
            self._broker(BROKER_A_OLD, MARKET_A, OWNER, 10, wausdc_balance="10000000000", updated_block=88),
            self._broker(BROKER_B, MARKET_B, OWNER, 20),
            self._broker(OTHER_BROKER, MARKET_A, OTHER_OWNER, 40),
        ]
        self.lp_positions = [
            {"owner": BROKER_A_NEW, "pool_id": POOL_A, "is_burned": False, "mint_block": 3, "token_id": "1", "tick_lower": -10, "tick_upper": 10, "liquidity": "1000000", "is_active": True},
            {"owner": BROKER_A_OLD, "pool_id": POOL_A, "is_burned": False, "mint_block": 2, "token_id": "2", "tick_lower": -10, "tick_upper": 10, "liquidity": "1000000", "is_active": True},
            {"owner": BROKER_A_NEW, "pool_id": POOL_B, "is_burned": False, "mint_block": 4, "token_id": "3", "tick_lower": -10, "tick_upper": 10, "liquidity": "1000000", "is_active": True},
        ]
        self.twamm_orders = [
            {"owner": BROKER_A_NEW, "pool_id": POOL_A, "block_number": 9, "order_id": "order-a", "amount_in": "1000000", "expiration": 100, "start_epoch": 1, "sell_rate": "1", "zero_for_one": True, "nonce": "1", "status": "active", "is_registered": True, "buy_tokens_out": "0", "sell_tokens_refund": "0", "tx_hash": "0xtwam-a"},
            {"owner": BROKER_A_NEW, "pool_id": POOL_B, "block_number": 8, "order_id": "order-b", "amount_in": "1000000", "expiration": 100, "start_epoch": 1, "sell_rate": "1", "zero_for_one": True, "nonce": "1", "status": "active", "is_registered": True, "buy_tokens_out": "0", "sell_tokens_refund": "0", "tx_hash": "0xtwam-b"},
        ]
        self.events = [
            {"market_id": MARKET_A, "event_name": "RouterSwapExecuted", "block_timestamp": 1000, "block_number": 100, "log_index": 2, "tx_hash": "0xmarket-a", "data": {"topics": ["0x0", broker_topic(BROKER_A_NEW), "0x1"], "raw": "0x" + f"{1000000:064x}" + f"{2000000:064x}"}},
            {"market_id": MARKET_B, "event_name": "RouterSwapExecuted", "block_timestamp": 900, "block_number": 90, "log_index": 1, "tx_hash": "0xmarket-b", "data": {"topics": ["0x0", broker_topic(BROKER_A_NEW), "0x1"], "raw": "0x" + f"{3000000:064x}" + f"{4000000:064x}"}},
            {"market_id": MARKET_A, "event_name": "Deposited", "block_timestamp": 800, "block_number": 80, "log_index": 1, "tx_hash": "0xold", "data": {"topics": ["0x0", broker_topic(BROKER_A_OLD)], "raw": "0x" + f"{5000000:064x}" + f"{0:064x}"}},
        ]

    def _broker(self, address, market_id, owner, created_block, wausdc_balance="1000000", updated_block=None):
        return {
            "address": address,
            "market_id": market_id,
            "owner": owner,
            "created_block": created_block,
            "active_lp_token_id": "0",
            "active_twamm_order_id": "",
            "wausdc_balance": wausdc_balance,
            "wrlp_balance": "2000000",
            "debt_principal": "0",
            "is_frozen": False,
            "is_liquidated": False,
            "updated_block": updated_block if updated_block is not None else created_block + 5,
        }

    def _matching_brokers(self, owner, market_id, selected=None):
        rows = [b for b in self.brokers if b["owner"] == owner and b["market_id"] == market_id]
        if selected:
            rows = [b for b in rows if b["address"] == selected]
        return sorted(rows, key=lambda b: (b["created_block"], b["address"]), reverse=True)

    async def fetch(self, sql, *args):
        if "FROM brokers" in sql:
            market_id = args[0]
            rows = [b for b in self.brokers if b["market_id"] == market_id]
            if len(args) > 1:
                rows = [b for b in rows if b["owner"] == args[1]]
            return sorted(rows, key=lambda b: (b["created_block"], b["address"]), reverse=True)
        if "FROM lp_positions" in sql:
            owner, pool_id = args
            return [p for p in self.lp_positions if p["owner"] == owner and p["pool_id"] == pool_id and not p["is_burned"]]
        if "FROM twamm_orders" in sql:
            owner, pool_id = args
            return [o for o in self.twamm_orders if o["owner"] == owner and o["pool_id"] == pool_id]
        if "FROM broker_operators" in sql:
            return []
        if "FROM events" in sql:
            market_id, topic, limit = args
            rows = [e for e in self.events if e["market_id"] == market_id and e["data"]["topics"][1] == topic]
            return sorted(rows, key=lambda e: (e["block_number"], e["log_index"]), reverse=True)[:limit]
        return []

    async def fetchrow(self, sql, *args):
        if "FROM broker_account_index" in sql:
            market_id, owner = args
            brokers = [
                {
                    "address": b["address"],
                    "marketId": b["market_id"],
                    "owner": b["owner"],
                    "createdBlock": b["created_block"],
                    "activeTokenId": b["active_lp_token_id"],
                    "wausdcBalance": b["wausdc_balance"],
                    "wrlpBalance": b["wrlp_balance"],
                    "debtPrincipal": b["debt_principal"],
                    "updatedBlock": b["updated_block"],
                    "isFrozen": b["is_frozen"],
                    "isLiquidated": b["is_liquidated"],
                }
                for b in self._matching_brokers(owner, market_id)
            ]
            return {"brokers": brokers} if brokers else None
        if "FROM markets" in sql:
            market_id = args[0]
            if market_id == MARKET_A:
                return {"pool_id": POOL_A}
            if market_id == MARKET_B:
                return {"pool_id": POOL_B}
            return None
        if "FROM brokers" in sql:
            owner, market_id = args[0], args[1]
            selected = args[2] if len(args) > 2 else None
            rows = self._matching_brokers(owner, market_id, selected)
            return rows[0] if rows else None
        if "FROM block_states" in sql:
            return {"mark_price": 1.0, "index_price": 1.0, "tick": 0, "fee_growth_global0": "0", "fee_growth_global1": "0"}
        return None


class BrokerMarketScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = FakeConn()
        self.original_get_pool = graphql.get_pool

        async def fake_get_pool():
            return FakePool(self.conn)

        graphql.get_pool = fake_get_pool
        self.query = graphql.Query()

    def tearDown(self):
        graphql.get_pool = self.original_get_pool

    async def test_brokers_reads_materialized_owner_market_projection_newest_first(self):
        rows = await self.query.brokers(MARKET_A, owner=OWNER)
        self.assertEqual([b.address for b in rows], [BROKER_A_NEW, BROKER_A_OLD])
        self.assertEqual([b.updated_block for b in rows], [35, 88])

    async def test_broker_projection_returns_independent_balances_per_broker(self):
        rows = await self.query.brokers(MARKET_A, owner=OWNER)
        by_address = {b.address: b for b in rows}
        self.assertEqual(by_address[BROKER_A_NEW].wausdc_balance, "1000000")
        self.assertEqual(by_address[BROKER_A_OLD].wausdc_balance, "10000000000")
        self.assertEqual(by_address[BROKER_A_OLD].updated_block, 88)

    async def test_profile_defaults_to_newest_market_broker_and_filters_nested_rows(self):
        profile = await self.query.broker_profile(OWNER, MARKET_A)
        self.assertEqual(profile["address"], BROKER_A_NEW)
        self.assertEqual([p["poolId"] for p in profile["lpPositions"]], [POOL_A])
        self.assertEqual([o["poolId"] for o in profile["twammOrders"]], [POOL_A])

    async def test_profile_selected_broker_must_belong_to_owner_and_market(self):
        profile = await self.query.broker_profile(OWNER, MARKET_A, broker_address=BROKER_A_OLD)
        self.assertEqual(profile["address"], BROKER_A_OLD)
        missing = await self.query.broker_profile(OWNER, MARKET_A, broker_address=BROKER_B)
        self.assertIsNone(missing)

    async def test_operations_filter_selected_broker_and_market_events(self):
        ops = await self.query.broker_operations(OWNER, MARKET_A, broker_address=BROKER_A_NEW)
        self.assertEqual([op["txHash"] for op in ops], ["0xmarket-a"])

    async def test_no_current_market_broker_returns_empty_profile_and_operations(self):
        profile = await self.query.broker_profile(OTHER_OWNER, MARKET_B)
        ops = await self.query.broker_operations(OTHER_OWNER, MARKET_B)
        self.assertIsNone(profile)
        self.assertEqual(ops, [])


if __name__ == "__main__":
    unittest.main()
