import datetime as dt
import sys
import types
import unittest
from pathlib import Path

from eth_abi import encode


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hypersync" not in sys.modules:
    hypersync_stub = types.ModuleType("hypersync")
    hypersync_stub.LogSelection = lambda **kwargs: kwargs
    sys.modules["hypersync"] = hypersync_stub

from analytics.scripts.backfill_metamorpho import TOPICS  # noqa: E402
from analytics.sources.metamorpho import MetaMorphoFactorySource  # noqa: E402


def topic_address(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


class FakeLog:
    def __init__(self, data: str):
        self.block_number = 18_900_000
        self.transaction_hash = "0xtx"
        self.log_index = 7
        self.address = "0x1897a8997241c1cd4bd0698647e4eb7213535c24"
        self.topics = [
            TOPICS["CreateMetaMorpho"],
            topic_address("1111111111111111111111111111111111111111"),
            topic_address("2222222222222222222222222222222222222222"),
            topic_address("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
        ]
        self.data = data


class FakeClickHouse:
    def __init__(self):
        self.inserts = []

    def insert(self, table, rows, column_names):
        self.inserts.append((table, rows, column_names))


class MetaMorphoFactorySourceTests(unittest.TestCase):
    def test_factory_create_decodes_registry_and_event_rows(self):
        source = MetaMorphoFactorySource()
        payload = "0x" + encode(
            ["address", "uint256", "string", "string", "bytes32"],
            [
                "0x3333333333333333333333333333333333333333",
                86_400,
                "Test Vault",
                "tVault",
                b"\x44" * 32,
            ],
        ).hex()
        timestamp = dt.datetime(2026, 1, 1, 12)

        decoded = source.decode(FakeLog(payload), {18_900_000: timestamp})

        self.assertEqual(decoded["registry"][0], "0x1111111111111111111111111111111111111111")
        self.assertEqual(decoded["registry"][1], "Test Vault")
        self.assertEqual(decoded["registry"][2], "USDC")
        self.assertEqual(decoded["registry"][4], "0x3333333333333333333333333333333333333333")
        self.assertEqual(decoded["registry"][10], 86_400)
        self.assertEqual(decoded["registry"][11], "factory")
        self.assertEqual(decoded["event"][5], "CreateMetaMorpho")
        self.assertEqual(decoded["event"][6], "0x2222222222222222222222222222222222222222")

    def test_factory_merge_writes_registry_and_event_facts(self):
        source = MetaMorphoFactorySource()
        ch = FakeClickHouse()
        decoded = {
            "registry": [
                "0x1111111111111111111111111111111111111111",
                "Test Vault",
                "USDC",
                "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "0x3333333333333333333333333333333333333333",
                "",
                "",
                "",
                "0",
                "",
                86_400,
                "factory",
                1,
            ],
            "event": [
                18_900_000,
                dt.datetime(2026, 1, 1, 12),
                "0xtx",
                7,
                "0x1111111111111111111111111111111111111111",
                "CreateMetaMorpho",
                "0x2222222222222222222222222222222222222222",
                "0x3333333333333333333333333333333333333333",
                "",
                "",
                "0",
                "0",
                "0x",
            ],
        }

        written = source.merge(ch, [decoded])

        self.assertEqual(written, 2)
        self.assertEqual(ch.inserts[0][0], "metamorpho_vault_registry")
        self.assertEqual(ch.inserts[1][0], "metamorpho_vault_events")


if __name__ == "__main__":
    unittest.main()
