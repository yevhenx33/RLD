import clickhouse_connect
ch = clickhouse_connect.get_client()

query = '''
    SELECT 
        argMax(symbol, timestamp) AS symbol,
        argMax(supply_usd, timestamp) AS supply_usd,
        argMax(borrow_usd, timestamp) AS borrow_usd
    FROM unified_timeseries
    WHERE protocol='AAVE_MARKET' AND symbol IN ('WETH', 'USDC', 'weETH')
    GROUP BY entity_id
'''
res = ch.query(query)
for r in res.result_rows:
    print(r)
