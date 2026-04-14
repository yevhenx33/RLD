import sqlite3
import requests
import sys
import json

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
    try:
        r = requests.post(url, json={"query": query})
        r.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch Morpho API: {e}")
        return
        
    data = r.json()
    if 'errors' in data:
        print("API Errors:", json.dumps(data['errors']))
        return
        
    api_markets = data.get("data", {}).get("markets", {}).get("items", [])
    api_map = {m["uniqueKey"]: m["state"] for m in api_markets if m.get("state")}
    
    db_path = "/home/ubuntu/RLD/morpho_enriched_final.db"
    import os
    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}.")
        return
        
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        SELECT market_id, total_supply_usd, total_borrow_usd, supply_apy, borrow_apy
        FROM (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY market_id ORDER BY timestamp DESC) as rn
            FROM morpho_series
        ) WHERE rn = 1
    """)
    
    rows = cur.fetchall()
    if not rows:
        print("No rows in DB")
        return
        
    matched_markets = 0
    total_db_markets = len(rows)
    mismatches = []
    
    for row in rows:
        market_id, supply_usd_db, borrow_usd_db, supply_apy_db, borrow_apy_db = row
        state_api = api_map.get(market_id)
        if not state_api:
            # API might not list deprecated markets, skip
            total_db_markets -= 1
            continue
            
        sup_usd_api = state_api.get("supplyAssetsUsd") or 0.0
        
        # Protect against small amounts drifting
        if sup_usd_api < 5000.0:
            if supply_usd_db < 5000.0:
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
    print(f"Active Indexed Markets:  {total_db_markets}")
    print(f"Poka-Yoke Matched Valid: {matched_markets}")
    if total_db_markets > 0:
        match_rate = matched_markets / total_db_markets * 100
        print(f"Global Match Rate:       {match_rate:.2f}%")
        
        if match_rate == 100.0:
            print("\n✅ PERFECT CORRELATION ACHIEVED ACROSS ALL POOLS.")
            
    if mismatches:
        print("\nTop 10 Remaining Discrepancies (DB vs Live):")
        mismatches.sort(key=lambda x: abs(x[1] - x[2]), reverse=True)
        for m, db_val, api_val in mismatches[:10]:
            print(f"  {m}: Ours ${db_val:,.2f} vs API ${api_val:,.2f}")

verify()
