from backfill_complete import discover_market_params, backfill_market, get_db_vaults, get_db_markets
from backend.morpho.rpc import eth_block_number, eth_get_block

head_block = eth_block_number()
head_ts = int(eth_get_block(head_block)["timestamp"], 16)
m = get_db_markets()[0]

# limit to 7 days
m["created_block"] = max(m.get("created_block", 0) or 0, head_block - 50400)
all_v = get_db_vaults()

cb, active_v = discover_market_params(m["market_id"], all_v, head_block)
m["created_block"] = max(cb, head_block - 50400)

print(f"Testing 7 days of data backfill for {m['market_id']}...")
backfill_market(m, active_v, head_block, head_ts)
print("Finished!")
