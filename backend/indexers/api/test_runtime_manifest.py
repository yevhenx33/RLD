import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
INDEXERS_DIR = API_DIR.parent
while str(API_DIR) in sys.path:
    sys.path.remove(str(API_DIR))
sys.path.insert(0, str(INDEXERS_DIR))


def _decorator(value=None, **_kwargs):
    if callable(value):
        return value
    return lambda wrapped: wrapped


def _install_import_stubs():
    strawberry = types.ModuleType("strawberry")
    strawberry.type = _decorator
    strawberry.field = _decorator

    class Schema:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    strawberry.Schema = Schema

    strawberry_fastapi = types.ModuleType("strawberry.fastapi")

    class GraphQLRouter:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    strawberry_fastapi.GraphQLRouter = GraphQLRouter

    strawberry_scalars = types.ModuleType("strawberry.scalars")
    strawberry_scalars.JSON = dict

    fastapi = types.ModuleType("fastapi")

    class FastAPI:
        def __init__(self, *args, **kwargs):
            self.routes = []

        def add_middleware(self, *args, **kwargs):
            return None

        def include_router(self, *args, **kwargs):
            return None

        def get(self, *_args, **_kwargs):
            return _decorator

        def post(self, *_args, **_kwargs):
            return _decorator

    class Request:
        client = None

    fastapi.FastAPI = FastAPI
    fastapi.Header = lambda default=None, **_kwargs: default
    fastapi.Query = lambda default=None, **_kwargs: default
    fastapi.Request = Request

    fastapi_responses = types.ModuleType("fastapi.responses")

    class JSONResponse(dict):
        def __init__(self, content, status_code=200):
            super().__init__(content)
            self.content = content
            self.status_code = status_code

    fastapi_responses.JSONResponse = JSONResponse

    fastapi_middleware = types.ModuleType("fastapi.middleware")
    fastapi_cors = types.ModuleType("fastapi.middleware.cors")

    class CORSMiddleware:
        pass

    fastapi_cors.CORSMiddleware = CORSMiddleware

    asyncpg = types.ModuleType("asyncpg")

    class Record(dict):
        pass

    asyncpg.Record = Record
    asyncpg.Connection = object
    asyncpg.Pool = object

    db = types.ModuleType("db")

    async def get_pool(*_args, **_kwargs):
        return None

    db.get_pool = get_pool

    sys.modules.setdefault("strawberry", strawberry)
    sys.modules.setdefault("strawberry.fastapi", strawberry_fastapi)
    sys.modules.setdefault("strawberry.scalars", strawberry_scalars)
    sys.modules.setdefault("fastapi", fastapi)
    sys.modules.setdefault("fastapi.responses", fastapi_responses)
    sys.modules.setdefault("fastapi.middleware", fastapi_middleware)
    sys.modules.setdefault("fastapi.middleware.cors", fastapi_cors)
    sys.modules.setdefault("asyncpg", asyncpg)
    sys.modules.setdefault("db", db)


_install_import_stubs()

spec = importlib.util.spec_from_file_location("rld_graphql", API_DIR / "graphql.py")
graphql = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = graphql
spec.loader.exec_module(graphql)


def deploy_cfg():
    return {
        "rld_core": "0x" + "01" * 20,
        "ghost_router": "0x" + "02" * 20,
        "broker_router": "0x" + "03" * 20,
        "broker_executor": "0x" + "04" * 20,
        "bond_factory": "0x" + "05" * 20,
        "twap_engine": "0x" + "06" * 20,
        "twap_engine_lens": "0x" + "07" * 20,
        "pool_manager": "0x" + "08" * 20,
        "v4_quoter": "0x" + "09" * 20,
        "v4_position_manager": "0x" + "0a" * 20,
        "permit2": "0x" + "0b" * 20,
        "chain_id": 31337,
        "markets": {
            "perp": {
                "type": "perp",
                "market_id": "0xperp",
                "pool_id": "0xpool-perp",
                "position_token": "0x" + "10" * 20,
                "collateral_token": "0x" + "11" * 20,
                "token0": "0x" + "10" * 20,
                "token1": "0x" + "11" * 20,
                "broker_factory": "0x" + "12" * 20,
                "broker_router": "0x" + "03" * 20,
                "bond_factory": "0x" + "05" * 20,
                "zero_for_one_long": False,
            },
            "cds": {
                "type": "cds",
                "market_id": "0xcds",
                "pool_id": "0xpool-cds",
                "position_token": "0x" + "20" * 20,
                "collateral_token": "0x" + "21" * 20,
                "token0": "0x" + "21" * 20,
                "token1": "0x" + "20" * 20,
                "broker_factory": "0x" + "22" * 20,
                "broker_router": "0x" + "33" * 20,
                "cds_coverage_factory": "0x" + "23" * 20,
                "funding_model": "0x" + "24" * 20,
                "settlement_module": "0x" + "25" * 20,
                "zero_for_one_long": True,
            },
        },
    }


def market_payload(market_id, pool_id):
    return {
        "marketId": market_id,
        "poolId": pool_id,
        "collateral": {"symbol": "waUSDC", "address": "0x" + "aa" * 20},
        "position_token": {"symbol": "wRLP", "address": "0x" + "bb" * 20},
        "brokerFactory": "0x" + "cc" * 20,
        "infrastructure": {
            "brokerRouter": "0x" + "03" * 20,
            "brokerExecutor": "0x" + "04" * 20,
            "bondFactory": "0x" + "05" * 20,
            "ghostRouter": "0x" + "02" * 20,
            "twapEngine": "0x" + "06" * 20,
            "twapEngineLens": "0x" + "07" * 20,
            "poolManager": "0x" + "08" * 20,
            "v4Quoter": "0x" + "09" * 20,
            "v4PositionManager": "0x" + "0a" * 20,
        },
        "poolFee": 500,
        "tickSpacing": 5,
    }


class RuntimeManifestTests(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("INDEXER_READY_MAX_LAG_BLOCKS", None)
        os.environ.pop("INDEXER_PUBLIC_RPC_URL", None)
        os.environ.pop("INDEXER_PUBLIC_FAUCET_URL", None)

    def test_manifest_includes_perp_and_cds_markets(self):
        os.environ["INDEXER_PUBLIC_RPC_URL"] = "https://rpc.example.test/"
        os.environ["INDEXER_PUBLIC_FAUCET_URL"] = "https://faucet.example.test/"
        manifest = graphql._build_runtime_manifest(
            deploy_cfg(),
            [
                market_payload("0xperp", "0xpool-perp"),
                market_payload("0xcds", "0xpool-cds"),
            ],
            indexer_block=98,
            chain_block=100,
            chain_id=31337,
        )

        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["chainId"], 31337)
        self.assertEqual(manifest["rpcUrl"], "https://rpc.example.test")
        self.assertEqual(manifest["faucetUrl"], "https://faucet.example.test")
        self.assertTrue(manifest["readiness"]["ready"])
        self.assertEqual(manifest["globalContracts"]["ghostRouter"], "0x" + "02" * 20)
        self.assertEqual(manifest["contracts"]["brokerRouter"], "0x" + "03" * 20)
        self.assertEqual(set(manifest["markets"].keys()), {"perp", "cds"})
        self.assertEqual(manifest["markets"]["cds"]["brokerRouter"], "0x" + "33" * 20)
        self.assertEqual(manifest["markets"]["cds"]["contracts"]["brokerRouter"], "0x" + "33" * 20)
        self.assertEqual(manifest["markets"]["cds"]["execution"]["brokerRouter"], "0x" + "33" * 20)
        self.assertTrue(manifest["markets"]["cds"]["execution"]["buyPositionZeroForOne"])
        self.assertTrue(manifest["markets"]["cds"]["twamm"]["buyPositionZeroForOne"])
        self.assertEqual(manifest["markets"]["cds"]["twamm"]["marketId"], "0xpool-cds")
        self.assertEqual(manifest["markets"]["perp"]["twamm"]["buyPositionZeroForOne"], False)
        self.assertEqual(manifest["markets"]["cds"]["pool"]["key"]["currency0"], "0x" + "21" * 20)
        self.assertEqual(manifest["markets"]["cds"]["cdsCoverageFactory"], "0x" + "23" * 20)
        self.assertTrue(manifest["markets"]["cds"]["featureFlags"]["cdsCoverage"])

    def test_manifest_rejects_missing_required_deployment_fields(self):
        cfg = deploy_cfg()
        del cfg["rld_core"]
        with self.assertRaisesRegex(ValueError, "rld_core"):
            graphql._build_runtime_manifest(
                cfg,
                [market_payload("0xperp", "0xpool-perp")],
                indexer_block=1,
                chain_block=1,
                chain_id=31337,
            )

    def test_readiness_reports_indexer_lag_and_missing_markets(self):
        os.environ["INDEXER_READY_MAX_LAG_BLOCKS"] = "12"
        manifest = graphql._build_runtime_manifest(
            deploy_cfg(),
            [market_payload("0xperp", "0xpool-perp")],
            indexer_block=90,
            chain_block=120,
            chain_id=31337,
        )

        self.assertFalse(manifest["readiness"]["ready"])
        self.assertEqual(manifest["readiness"]["indexerLagBlocks"], 30)
        self.assertIn("indexer_lag", manifest["readiness"]["reasons"])
        self.assertIn("missing_cds_market", manifest["readiness"]["reasons"])


if __name__ == "__main__":
    unittest.main()
