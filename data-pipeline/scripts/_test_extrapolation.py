import pandas as pd
from datetime import datetime
import clickhouse_connect
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.base import forward_fill_hourly

def main():
    ch = clickhouse_connect.get_client()
    
    # Fake an empty DataFrame that only contains an unrelated token (WETH) at a current time
    # This simulates a batch where SNX had ZERO events, but the batch is processing up to 2026-04-14
    df = pd.DataFrame({
        "timestamp": [pd.to_datetime("2026-04-14 10:00:00")],
        "entity_id": ["0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"],
        "symbol": ["WETH"],
        "protocol": ["AAVE_MARKET"],
        "target_id": [""],
        "supply_usd": [1000.0],
        "borrow_usd": [500.0],
        "supply_apy": [0.05],
        "borrow_apy": [0.10],
        "utilization": [0.5],
        "price_usd": [3000.0]
    })
    
    # We expect forward_fill_hourly to internally fetch last_known for ALL entities, find SNX stopped in 2025,
    # append SNX to the entity list, and mathematically compound SNX's balances up to 2026-04-14 10:00:00
    
    result = forward_fill_hourly(df, ch, "AAVE_MARKET")
    
    # Print out tail of SNX
    snx = result[result["symbol"] == "SNX"].sort_values("timestamp")
    if not snx.empty:
        print("✅ POKA-YOKE PASS: SNX was successfully extrapolated.")
        print(f"Total SNX Extrapolated Hours: {len(snx)}")
        print("First Row:")
        print(snx.iloc[0][['timestamp', 'supply_usd', 'supply_apy']])
        print("Last Row:")
        print(snx.iloc[-1][['timestamp', 'supply_usd', 'supply_apy']])
    else:
        print("❌ FAIL: SNX was not extrapolated.")
        
if __name__ == "__main__":
    main()
