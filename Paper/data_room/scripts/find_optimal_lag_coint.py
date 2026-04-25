import clickhouse_connect
import pandas as pd
import numpy as np
import warnings
from statsmodels.tsa.stattools import coint
from statsmodels.tools.sm_exceptions import CollinearityWarning

# Suppress warnings from statsmodels when testing edge cases
warnings.simplefilter('ignore', CollinearityWarning)
warnings.filterwarnings("ignore")

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

def find_optimal_coint(name, subset_df, full_df, max_lag=45):
    lags = list(range(max_lag + 1))
    pvals = []
    
    for lag in lags:
        df_lag = full_df.copy()
        df_lag['shifted_rate'] = df_lag['borrow_rate'].shift(-lag)
        
        # Filter back to the subset window
        mask = (df_lag['date'] >= subset_df['date'].min()) & (df_lag['date'] <= subset_df['date'].max())
        subset_lag = df_lag[mask].copy()
        
        # Drop NaNs that occur due to shifting out of bounds
        subset_lag = subset_lag.dropna(subset=['eth_price', 'shifted_rate'])
        
        if len(subset_lag) < 10:
            pvals.append(1.0)
            continue
            
        try:
            score, pval, _ = coint(subset_lag['eth_price'], subset_lag['shifted_rate'])
            pvals.append(pval)
        except:
            pvals.append(1.0)
        
    optimal_lag = np.argmin(pvals)
    min_pval = pvals[optimal_lag]
    
    print(f"[{name}] Optimal Coint Lag: {optimal_lag} days | Min p-value: {min_pval:.4f}")
    
    # Print top 3 lags
    top_3_idx = np.argsort(pvals)[:3]
    top_3 = [(l, pvals[l]) for l in top_3_idx]
    print(f"  Top 3 Lags: {', '.join([f'{l}d (p={p:.4f})' for l, p in top_3])}")

print("=== Optimal Cointegration Lag Analysis (0 to 45 Days) ===")
find_optimal_coint("Full Year 2025", df, df)

q1_df = df[(df['date'] >= '2025-01-01') & (df['date'] < '2025-04-01')]
find_optimal_coint("Q1 2025 (Strong Trend)", q1_df, df)

q2_df = df[(df['date'] >= '2025-04-01') & (df['date'] < '2025-07-01')]
find_optimal_coint("Q2 2025", q2_df, df)

q3_df = df[(df['date'] >= '2025-07-01') & (df['date'] < '2025-10-01')]
find_optimal_coint("Q3 2025", q3_df, df)

q4_df = df[(df['date'] >= '2025-10-01') & (df['date'] <= '2025-12-31')]
find_optimal_coint("Q4 2025 (Crisis)", q4_df, df)
