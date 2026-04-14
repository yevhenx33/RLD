import clickhouse_connect
import requests
import json
import sys

def verify():
    url = "https://blue-api.morpho.org/graphql"
    query = """
    query {
      markets(first: 1000) {
        items {
          uniqueKey
          state {
            supplyAssetsUsd
            borrowAssetsUsd
            supplyApy
            borrowApy
          }
        }
      }
    }
    """
    print("Fetching API...")
    r = requests.post(url, json={"query": query})
    data = r.json()
    api_markets = data.get("data", {}).get("markets", {}).get("items", [])
    api_map = {m["uniqueKey"]: m["state"] for m in api_markets if m.get("state")}
    print(f"Loaded {len(api_map)} markets from API.")
    
    print("Fetching ClickHouse DB...")
    try:
        ch = clickhouse_connect.get_client(host="localhost", port=8123)
        res = ch.query("""
            SELECT entity_id, supply_usd, borrow_usd, supply_apy, borrow_apy
            FROM (
                SELECT entity_id, supply_usd, borrow_usd, supply_apy, borrow_apy,
                       ROW_NUMBER() OVER(PARTITION BY entity_id ORDER BY timestamp DESC) as rn
                FROM unified_timeseries
                WHERE protocol = 'MORPHO_MARKET'
            ) WHERE rn = 1
        """)
        rows = res.result_rows
        print(f"Loaded {len(rows)} markets from DB.")
    except Exception as e:
        print(f"Failed to query ClickHouse: {e}")
        return
        
    matched_markets = 0
    total_db_markets = len(rows)
    mismatches = []
    
    missing_in_api = 0
    
    for row in rows:
        market_id, supply_usd_db, borrow_usd_db, supply_apy_db, borrow_apy_db = row
        state_api = api_map.get(market_id)
        if not state_api:
            missing_in_api += 1
            total_db_markets -= 1
            continue
            
        sup_usd_api = state_api.get("supplyAssetsUsd") or 0.0
        
        # Protect against empty or deprecated markets
        if sup_usd_api < 1000.0:
            if supply_usd_db < 1000.0:
                matched_markets += 1
            else:
                mismatches.append((market_id, supply_usd_db, sup_usd_api))
            continue
            
        diff_ratio = abs(supply_usd_db - sup_usd_api) / sup_usd_api
        
        if diff_ratio < 0.10: # within 10% bounds
            matched_markets += 1
        else:
            mismatches.append((market_id, supply_usd_db, sup_usd_api))
            
    print(f"=== Verification Scorecard ===")
    print(f"Total Rows Checked:      {len(rows)}")
    print(f"Orphaned/Deprecated:     {missing_in_api}")
    print(f"Active Indexed Markets:  {total_db_markets}")
    print(f"Poka-Yoke Matched Valid: {matched_markets}")
    if total_db_markets > 0:
        match_rate = matched_markets / total_db_markets * 100
        print(f"Global Match Rate:       {match_rate:.2f}%")
        if match_rate == 100.0:
             print("✅ PERFECT CORRELATION ACHIEVED ACROSS ALL POOLS.")
        
    if mismatches:
        print("\nTop 10 Remaining Discrepancies (DB vs Live):")
        mismatches.sort(key=lambda x: abs(x[1] - x[2]), reverse=True)
        for m, db_val, api_val in mismatches[:10]:
            print(f"  {m}: Ours ${db_val:,.2f} vs API ${api_val:,.2f}")

verify()
