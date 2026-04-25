import clickhouse_connect
import pandas as pd
import numpy as np
import statsmodels.tsa.stattools as ts

client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

query_eth = """
SELECT toDate(timestamp) as date,
       argMax(price, block_number) as eth_price
FROM chainlink_prices
WHERE feed='ETH / USD' AND toYear(timestamp) = 2025
GROUP BY date
ORDER BY date
"""
df_eth = client.query_df(query_eth)

query_usdc = """
SELECT toDate(timestamp) as date,
       argMax(borrow_apy, inserted_at) as borrow_rate
FROM aave_timeseries
WHERE protocol='AAVE_MARKET' AND symbol='USDC' AND toYear(timestamp) = 2025
GROUP BY date
ORDER BY date
"""
df_usdc = client.query_df(query_usdc)

df = pd.merge(df_eth, df_usdc, on='date', how='inner')
df['date'] = pd.to_datetime(df['date'])

def get_coint(sub_df):
    if len(sub_df) < 5: return "N/A"
    score, pvalue, _ = ts.coint(sub_df['eth_price'], sub_df['borrow_rate'])
    return pvalue

print("Full Year p-value:", get_coint(df))
print("Q1 p-value:", get_coint(df[(df['date'] >= '2025-01-01') & (df['date'] < '2025-04-01')]))
print("Q2 p-value:", get_coint(df[(df['date'] >= '2025-04-01') & (df['date'] < '2025-07-01')]))
print("Q3 p-value:", get_coint(df[(df['date'] >= '2025-07-01') & (df['date'] < '2025-10-01')]))
print("Q4 p-value:", get_coint(df[(df['date'] >= '2025-10-01') & (df['date'] <= '2025-12-31')]))
