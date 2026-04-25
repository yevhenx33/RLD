import clickhouse_connect
import pandas as pd
import numpy as np
from statsmodels.tsa.stattools import coint
import warnings
import sys
warnings.filterwarnings('ignore')

try:
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

    best_coint = None
    best_coint_pval = 1.0

    best_corr = None
    max_corr = -1.0

    for window_size in [30, 45, 60, 90]:
        for i in range(len(df) - window_size):
            subset = df.iloc[i:i+window_size]
            
            if subset['eth_price'].std() < 1e-5 or subset['borrow_rate'].std() < 1e-5:
                continue
                
            score, pval, _ = coint(subset['eth_price'], subset['borrow_rate'])
            corr = subset['eth_price'].corr(subset['borrow_rate'])
            
            if pval < best_coint_pval:
                best_coint_pval = pval
                best_coint = (window_size, subset['date'].min(), subset['date'].max(), pval, corr)
                
            if pval < 0.05 and corr > max_corr:
                max_corr = corr
                best_corr = (window_size, subset['date'].min(), subset['date'].max(), pval, corr)

    with open('coint_result.txt', 'w') as f:
        f.write("Absolute lowest p-value (Math strongest cointegration):\n")
        if best_coint:
            f.write(f"Window: {best_coint[0]} days ({best_coint[1].strftime('%Y-%m-%d')} to {best_coint[2].strftime('%Y-%m-%d')})\n")
            f.write(f"p-value: {best_coint[3]:.8f}\n")
            f.write(f"Correlation: {best_coint[4]:.4f}\n\n")
        
        f.write("Highest correlation where p-value < 0.05 (Tightest linear coupling):\n")
        if best_corr:
            f.write(f"Window: {best_corr[0]} days ({best_corr[1].strftime('%Y-%m-%d')} to {best_corr[2].strftime('%Y-%m-%d')})\n")
            f.write(f"p-value: {best_corr[3]:.8f}\n")
            f.write(f"Correlation: {best_corr[4]:.4f}\n")
except Exception as e:
    with open('coint_result.txt', 'w') as f:
        f.write(f"ERROR: {str(e)}")
