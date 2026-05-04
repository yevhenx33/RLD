import asyncio
import asyncpg

async def main():
    conn = await asyncpg.connect("postgresql://postgres:postgres@127.0.0.1:5433/rld_data")
    
    # 1. Pipeline Stats
    stats = await conn.fetchrow("SELECT COUNT(DISTINCT block_number) as blocks, MIN(timestamp) as min_ts, MAX(timestamp) as max_ts FROM aave_hourly_state")
    print(f"Total Hourly Blocks Indexed: {stats['blocks']:,}")
    print(f"Time Range: {stats['min_ts']} to {stats['max_ts']}")
    
    # 2. Latest Snapshot Data
    print("\nSAMPLE LATEST ASSETS DATA (Top 5 by Supply):")
    rows = await conn.fetch("""
        SELECT symbol, supplied_usd, borrowed_usd, supply_rate, borrow_rate, utilization_rate, price_usd, block_number 
        FROM aave_hourly_state 
        WHERE block_number = (SELECT MAX(block_number) FROM aave_hourly_state) 
        ORDER BY supplied_usd DESC 
        LIMIT 5
    """)
    for r in rows:
        sup_m = r["supplied_usd"] / 1e6
        bor_m = r["borrowed_usd"] / 1e6
        print(f"{r['symbol']:<6}: Supply=${sup_m:,.1f}M | Borrow=${bor_m:,.1f}M | Util={r['utilization_rate']:.2%} | SR={r['supply_rate']:.2%} | Price=${r['price_usd']:,.2f}")
        
    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
