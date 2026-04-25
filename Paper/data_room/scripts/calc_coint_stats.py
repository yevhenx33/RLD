import clickhouse_connect
import pandas as pd
import numpy as np
from statsmodels.tsa.stattools import coint

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
df['borrow_rate'] = df['borrow_rate'] * 100

def print_stats(name, subset):
    if len(subset) < 10:
        return
    score, pval, _ = coint(subset['eth_price'], subset['borrow_rate'])
    
    print(f"| {name} | {subset['eth_price'].mean():.2f} | {subset['eth_price'].std():.2f} | "
          f"{subset['borrow_rate'].mean():.2f}% | {subset['borrow_rate'].std():.2f}% | "
          f"{pval:.4f} |")

print("| Period | ETH Price Mean | ETH Volatility (StdDev) | USDC Rate Mean | Rate Volatility (StdDev) | Engle-Granger p-value |")
print("|---|---|---|---|---|---|")

print_stats("Full Year 2025", df)
print_stats("Q1 2025", df[(df['date'] >= '2025-01-01') & (df['date'] < '2025-04-01')])
print_stats("Q2 2025", df[(df['date'] >= '2025-04-01') & (df['date'] < '2025-07-01')])
print_stats("Q3 2025", df[(df['date'] >= '2025-07-01') & (df['date'] < '2025-10-01')])
print_stats("Q4 2025", df[(df['date'] >= '2025-10-01') & (df['date'] <= '2025-12-31')])
