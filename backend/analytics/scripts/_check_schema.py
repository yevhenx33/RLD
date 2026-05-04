import clickhouse_connect
ch = clickhouse_connect.get_client()

print('=== DATA SCHEMA ===')
res = ch.query('DESCRIBE TABLE unified_timeseries')
for row in res.result_rows:
    print(f'- {row[0]} ({row[1]})')

print('\n=== LATEST SNAPSHOT (Sample: WETH) ===')
q = '''
SELECT timestamp, symbol, supply_usd, borrow_usd, supply_apy, borrow_apy, price_usd
FROM unified_timeseries 
WHERE protocol='AAVE_MARKET' AND symbol='WETH' 
ORDER BY timestamp DESC 
LIMIT 1
'''
sample = ch.query(q).result_rows
if sample:
    t, sym, sup, bor, s_apy, b_apy, price = sample[0]
    print(f'Symbol:     {sym}')
    print(f'Timestamp:  {t}')
    print(f'Supply TVL: ${sup:,.2f}')
    print(f'Borrow TVL: ${bor:,.2f}')
    print(f'Supply APY: {s_apy*100:.2f}%')
    print(f'Borrow APY: {b_apy*100:.2f}%')
    print(f'Asset Price:${price:,.2f}')
