import datetime as dt
import math
import sys
import types
import unittest
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "hypersync" not in sys.modules:
    hypersync_stub = types.ModuleType("hypersync")
    hypersync_stub.LogSelection = lambda **kwargs: kwargs
    sys.modules["hypersync"] = hypersync_stub

from analytics.sources.euler import (  # noqa: E402
    EULER_MARKET,
    EVENT_MAP,
    RAY,
    TOPIC_BORROW,
    TOPIC_EVAULT_CREATED,
    TOPIC_GOV_SET_CAPS,
    TOPIC_GOV_SET_CONFIG_FLAGS,
    TOPIC_GOV_SET_INTEREST_FEE,
    TOPIC_GOV_SET_INTEREST_RATE_MODEL,
    TOPIC_GOV_SET_LTV,
    TOPIC_VAULT_STATUS,
    EulerSource,
    EulerVaultMetadata,
    interest_fee_ratio,
    spy_to_apy,
)


USDC = "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
VAULT = "1111111111111111111111111111111111111111"
UNVERIFIED_VAULT = "2222222222222222222222222222222222222222"


class FakeLog:
    def __init__(self, address, topic0, data, topics=None, block=100, log_index=0):
        self.block_number = block
        self.transaction_hash = "0xhash"
        self.log_index = log_index
        self.address = address
        self.topics = topics or [topic0]
        self.data = data


class FakeQueryResult:
    def __init__(self, rows=None):
        self.result_rows = rows or []


class FakeClickHouse:
    def __init__(self):
        self.inserted = {}
        self.commands = []

    def command(self, query):
        self.commands.append(query)
        return 0

    def query(self, query):
        if "SELECT DISTINCT feed FROM chainlink_prices" in query:
            return FakeQueryResult([("USDC / USD",)])
        return FakeQueryResult([])

    def query_df(self, query):
        if "FROM chainlink_prices" in query:
            return pd.DataFrame(
                [
                    {"ts": pd.Timestamp("2026-01-01T12:00:00"), "feed": "USDC / USD", "price": 1.0},
                ]
            )
        return pd.DataFrame()

    def insert(self, table, rows, column_names=None):
        self.inserted.setdefault(table, []).extend(rows)

    def insert_df(self, table, df):
        self.inserted.setdefault(table, []).append(df.copy())


def word(value: int) -> str:
    return f"{value:064x}"


def address_topic(address: str) -> str:
    return "0x" + "0" * 24 + address.lower().removeprefix("0x")


def address_word(address: str) -> str:
    return "0" * 24 + address.lower().removeprefix("0x")


def data_words(*values: str | int) -> str:
    encoded = []
    for value in values:
        encoded.append(word(value) if isinstance(value, int) else value)
    return "0x" + "".join(encoded)


class EulerSourceTests(unittest.TestCase):
    def test_event_mapping_uses_official_evault_events(self):
        self.assertEqual(EVENT_MAP[TOPIC_EVAULT_CREATED], "EVaultCreated")
        self.assertEqual(EVENT_MAP[TOPIC_VAULT_STATUS], "VaultStatus")
        self.assertEqual(EVENT_MAP[TOPIC_BORROW], "Borrow")
        self.assertEqual(EVENT_MAP[TOPIC_GOV_SET_INTEREST_FEE], "GovSetInterestFee")
        self.assertEqual(EVENT_MAP[TOPIC_GOV_SET_LTV], "GovSetLTV")

    def test_evault_created_registers_factory_vault_metadata(self):
        source = EulerSource()
        ts = dt.datetime(2026, 1, 1, 12)
        log = FakeLog(
            "0x" + VAULT,
            TOPIC_EVAULT_CREATED,
            data_words(address_word("33" * 20)),
            topics=[TOPIC_EVAULT_CREATED, address_topic("44" * 20), address_topic(USDC)],
        )

        decoded = source.decode(log, {100: ts})
        meta = source._registry["0x" + VAULT]

        self.assertEqual(decoded["kind"], "registry")
        self.assertEqual(meta.asset_symbol, "USDC")
        self.assertEqual(meta.asset_decimals, 6)
        self.assertFalse(meta.verified)
        self.assertEqual(meta.created_block, 100)

    def test_vault_status_is_authoritative_state(self):
        source = EulerSource()
        ts = dt.datetime(2026, 1, 1, 12)
        log = FakeLog(
            "0x" + VAULT,
            TOPIC_VAULT_STATUS,
            data_words(1_000, 400, 7, 600, 10**27, 123456, 1_767_267_200),
        )

        decoded = source.decode(log, {100: ts})
        state = source._states["0x" + VAULT]

        self.assertEqual(decoded["kind"], "snapshot")
        self.assertEqual(state.total_shares, 1_000)
        self.assertEqual(state.total_borrows, 400)
        self.assertEqual(state.accumulated_fees, 7)
        self.assertEqual(state.cash, 600)
        self.assertEqual(state.interest_accumulator, 10**27)
        self.assertEqual(state.interest_rate, 123456)

    def test_governance_params_and_ltv_are_persisted(self):
        source = EulerSource()
        ts = dt.datetime(2026, 1, 1, 12)
        rows = [
            source.decode(
                FakeLog("0x" + VAULT, TOPIC_GOV_SET_INTEREST_RATE_MODEL, data_words(address_word("55" * 20)), block=101, log_index=1),
                {101: ts},
            ),
            source.decode(
                FakeLog("0x" + VAULT, TOPIC_GOV_SET_CAPS, data_words(10_000, 20_000), block=102, log_index=2),
                {102: ts},
            ),
            source.decode(
                FakeLog("0x" + VAULT, TOPIC_GOV_SET_CONFIG_FLAGS, data_words(7), block=103, log_index=3),
                {103: ts},
            ),
            source.decode(
                FakeLog(
                    "0x" + VAULT,
                    TOPIC_GOV_SET_LTV,
                    data_words(8_000, 8_500, 9_000, 1_800_000_000, 86_400),
                    topics=[TOPIC_GOV_SET_LTV, address_topic("66" * 20)],
                    block=104,
                    log_index=4,
                ),
                {104: ts},
            ),
        ]
        ch = FakeClickHouse()

        source.merge(ch, rows)

        params = ch.inserted["euler_vault_market_params"][-1]
        self.assertEqual(params[0], "0x" + VAULT)
        self.assertEqual(params[5], "0x" + "55" * 20)
        self.assertEqual(params[6], "10000")
        self.assertEqual(params[7], "20000")
        self.assertEqual(params[8], 7)

        ltv = ch.inserted["euler_vault_ltv_config"][0]
        self.assertEqual(ltv[0], "0x" + VAULT)
        self.assertEqual(ltv[1], "0x" + "66" * 20)
        self.assertEqual(ltv[6], 8_000)
        self.assertEqual(ltv[7], 8_500)
        self.assertEqual(ltv[8], 9_000)
        self.assertEqual(ltv[9], 1_800_000_000)
        self.assertEqual(ltv[10], 86_400)

    def test_spy_to_apy_and_fee_conversion(self):
        rate = int((0.05 / (365.2425 * 24 * 60 * 60)) * RAY)
        self.assertAlmostEqual(spy_to_apy(rate), math.expm1(0.05), places=6)
        self.assertEqual(interest_fee_ratio(1000), 0.1)
        self.assertEqual(interest_fee_ratio(10_001), min(10_001 / 1e18, 1.0))

    def test_strict_chainlink_support_requires_verified_and_priced(self):
        source = EulerSource()
        source._available_feeds = {"USDC / USD"}
        verified = EulerVaultMetadata(vault_address="0x" + VAULT, asset_address="0x" + USDC, asset_symbol="USDC", asset_decimals=6, verified=True)
        unverified = EulerVaultMetadata(vault_address="0x" + UNVERIFIED_VAULT, asset_address="0x" + USDC, asset_symbol="USDC", asset_decimals=6, verified=False)
        unsupported = EulerVaultMetadata(vault_address="0x" + "33" * 20, asset_address="0x" + "44" * 20, asset_symbol="UNKNOWN", asset_decimals=18, verified=True)

        self.assertEqual(source._support_for(verified)[0], "CHAINLINK_SUPPORTED")
        self.assertEqual(source._support_for(unverified)[0], "UNVERIFIED")
        self.assertEqual(source._support_for(unsupported)[0], "UNSUPPORTED_ORACLE")

    def test_replay_serves_only_verified_priced_vaults(self):
        source = EulerSource()
        source._registry = {
            "0x" + VAULT: EulerVaultMetadata(
                vault_address="0x" + VAULT,
                asset_address="0x" + USDC,
                asset_symbol="USDC",
                asset_decimals=6,
                verified=True,
            ),
            "0x" + UNVERIFIED_VAULT: EulerVaultMetadata(
                vault_address="0x" + UNVERIFIED_VAULT,
                asset_address="0x" + USDC,
                asset_symbol="USDC",
                asset_decimals=6,
                verified=False,
            ),
        }
        ts = dt.datetime(2026, 1, 1, 12)
        rows = [
            source.decode(
                FakeLog("0x" + VAULT, TOPIC_VAULT_STATUS, data_words(1_000, 400_000_000, 0, 600_000_000, 10**27, 0, 1), log_index=1),
                {100: ts},
            ),
            source.decode(
                FakeLog("0x" + UNVERIFIED_VAULT, TOPIC_VAULT_STATUS, data_words(1_000, 400_000_000, 0, 600_000_000, 10**27, 0, 1), log_index=2),
                {100: ts},
            ),
        ]
        ch = FakeClickHouse()

        written = source.merge(ch, rows)

        self.assertEqual(written, 1)
        euler_frames = ch.inserted["euler_timeseries"]
        served = pd.concat(euler_frames, ignore_index=True)
        self.assertEqual(served["protocol"].unique().tolist(), [EULER_MARKET])
        self.assertEqual(served["entity_id"].unique().tolist(), ["0x" + VAULT])
        self.assertEqual(served["target_id"].unique().tolist(), ["0x" + USDC])
        self.assertAlmostEqual(float(served["supply_usd"].iloc[0]), 1000.0)
        self.assertAlmostEqual(float(served["borrow_usd"].iloc[0]), 400.0)

    def test_replay_uses_each_vault_status_snapshot_for_history(self):
        source = EulerSource()
        source._registry = {
            "0x" + VAULT: EulerVaultMetadata(
                vault_address="0x" + VAULT,
                asset_address="0x" + USDC,
                asset_symbol="USDC",
                asset_decimals=6,
                verified=True,
            ),
        }
        rows = [
            source.decode(
                FakeLog(
                    "0x" + VAULT,
                    TOPIC_VAULT_STATUS,
                    data_words(1_000, 400_000_000, 0, 600_000_000, 10**27, 0, 1),
                    block=100,
                    log_index=1,
                ),
                {100: dt.datetime(2026, 1, 1, 12)},
            ),
            source.decode(
                FakeLog(
                    "0x" + VAULT,
                    TOPIC_VAULT_STATUS,
                    data_words(2_000, 800_000_000, 0, 1_200_000_000, 10**27, 0, 1),
                    block=200,
                    log_index=1,
                ),
                {200: dt.datetime(2026, 1, 1, 13)},
            ),
        ]
        ch = FakeClickHouse()

        written = source.merge(ch, rows)

        self.assertEqual(written, 2)
        served = pd.concat(ch.inserted["euler_timeseries"], ignore_index=True)
        served = served.sort_values("timestamp").reset_index(drop=True)
        self.assertAlmostEqual(float(served["supply_usd"].iloc[0]), 1000.0)
        self.assertAlmostEqual(float(served["borrow_usd"].iloc[0]), 400.0)
        self.assertAlmostEqual(float(served["supply_usd"].iloc[-1]), 2000.0)
        self.assertAlmostEqual(float(served["borrow_usd"].iloc[-1]), 800.0)

    def test_replay_keeps_zero_vault_status_as_terminal_anchor(self):
        source = EulerSource()
        source._registry = {
            "0x" + VAULT: EulerVaultMetadata(
                vault_address="0x" + VAULT,
                asset_address="0x" + USDC,
                asset_symbol="USDC",
                asset_decimals=6,
                verified=True,
            ),
        }
        rows = [
            source.decode(
                FakeLog(
                    "0x" + VAULT,
                    TOPIC_VAULT_STATUS,
                    data_words(1_000, 400_000_000, 0, 600_000_000, 10**27, 0, 1),
                    block=100,
                    log_index=1,
                ),
                {100: dt.datetime(2026, 1, 1, 12)},
            ),
            source.decode(
                FakeLog(
                    "0x" + VAULT,
                    TOPIC_VAULT_STATUS,
                    data_words(0, 0, 0, 0, 10**27, 0, 1),
                    block=200,
                    log_index=1,
                ),
                {200: dt.datetime(2026, 1, 1, 13)},
            ),
        ]
        ch = FakeClickHouse()

        written = source.merge(ch, rows)

        self.assertEqual(written, 2)
        served = pd.concat(ch.inserted["euler_timeseries"], ignore_index=True)
        served = served.sort_values("timestamp").reset_index(drop=True)
        self.assertAlmostEqual(float(served["supply_usd"].iloc[0]), 1000.0)
        self.assertAlmostEqual(float(served["borrow_usd"].iloc[0]), 400.0)
        self.assertAlmostEqual(float(served["supply_usd"].iloc[-1]), 0.0)
        self.assertAlmostEqual(float(served["borrow_usd"].iloc[-1]), 0.0)


if __name__ == "__main__":
    unittest.main()
