import os
import sys
import types

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# forward_fill_hourly lives in a module that also imports hypersync for other
# code paths. Stub it here so this regression test can run in lightweight envs.
if "hypersync" not in sys.modules:
    sys.modules["hypersync"] = types.SimpleNamespace(LogSelection=object)

from indexer.base import forward_fill_hourly


class FakeClickHouseClient:
    def query_df(self, _query: str) -> pd.DataFrame:
        # Empty historical seed is enough to validate gap compounding math.
        return pd.DataFrame()


def main() -> None:
    source = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-01-01 00:00:00", "2026-01-01 02:00:00"]),
            "entity_id": ["0xentity", "0xentity"],
            "symbol": ["USDC", "USDC"],
            "protocol": ["AAVE_MARKET", "AAVE_MARKET"],
            "target_id": ["", ""],
            "supply_usd": [100.0, 100.0],
            "borrow_usd": [50.0, 50.0],
            # 8760 means exactly +100% synthetic annualized growth per hour here:
            # 1 + (apy / 8760) => 2x on one filled gap row.
            "supply_apy": [8760.0, 8760.0],
            "borrow_apy": [8760.0, 8760.0],
            "utilization": [0.5, 0.5],
            "price_usd": [1.0, 1.0],
        }
    )

    result = forward_fill_hourly(
        source, FakeClickHouseClient(), "AAVE_MARKET", compound=True
    ).sort_values("timestamp")

    gap_row = result[result["timestamp"] == pd.Timestamp("2026-01-01 01:00:00")]
    assert not gap_row.empty, "Expected one synthetic gap row at 01:00."
    assert abs(float(gap_row["supply_usd"].iloc[0]) - 200.0) < 1e-9
    assert abs(float(gap_row["borrow_usd"].iloc[0]) - 100.0) < 1e-9
    print("forward_fill_hourly compound regression test passed")


if __name__ == "__main__":
    main()
