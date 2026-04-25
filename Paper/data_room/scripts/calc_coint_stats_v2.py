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

def print_stats(name, subset, full_df):
    if len(subset) < 10:
        return
    
    # Simple correlation
    corr = subset['eth_price'].corr(subset['borrow_rate'])
    
    # 7D MA Correlation
    subset_ma = subset.copy()
    subset_ma['eth_ma'] = subset_ma['eth_price'].rolling(7).mean()
    subset_ma['rate_ma'] = subset_ma['borrow_rate'].rolling(7).mean()
    corr_7d_ma = subset_ma['eth_ma'].corr(subset_ma['rate_ma'])
    
    # Lag correlations: we shift the rate backwards (so rate trails price)
    # Actually, rate is reactive. So a price move happens, then rate moves later.
    # We want to correlate current rate with PAST price. 
    # That means we shift price forward (or shift rate backward).
    # Let's shift rate backward by 7 days.
    df_lag = full_df.copy()
    df_lag['rate_lag7'] = df_lag['borrow_rate'].shift(-7)
    df_lag['rate_lag14'] = df_lag['borrow_rate'].shift(-14)
    
    # Now filter by subset dates
    mask = (df_lag['date'] >= subset['date'].min()) & (df_lag['date'] <= subset['date'].max())
    subset_lag = df_lag[mask].copy()
    
    corr_lag7 = subset_lag['eth_price'].corr(subset_lag['rate_lag7'])
    corr_lag14 = subset_lag['eth_price'].corr(subset_lag['rate_lag14'])
    
    score, pval, _ = coint(subset['eth_price'], subset['borrow_rate'])
    
    print(f"| {name} | {corr:.3f} | {corr_7d_ma:.3f} | {corr_lag7:.3f} | {corr_lag14:.3f} | {pval:.4f} |")

print("| **Period** | **Correlation** | **Correlation (7D MA)** | **Lag-7 Corr** | **Lag-14 Corr** | **Coint p-value** |")
print("|---|---|---|---|---|---|")

print_stats("Full Year 2025", df, df)
print_stats("Q1 2025", df[(df['date'] >= '2025-01-01') & (df['date'] < '2025-04-01')], df)
print_stats("Q2 2025", df[(df['date'] >= '2025-04-01') & (df['date'] < '2025-07-01')], df)
print_stats("Q3 2025", df[(df['date'] >= '2025-07-01') & (df['date'] < '2025-10-01')], df)
print_stats("Q4 2025", df[(df['date'] >= '2025-10-01') & (df['date'] <= '2025-12-31')], df)
