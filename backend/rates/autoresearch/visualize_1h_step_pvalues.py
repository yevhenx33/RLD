import sqlite3
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from multiprocessing import Pool, cpu_count
import matplotlib.pyplot as plt
import os
import sys

plt.style.use('dark_background')

# Pass global arrays directly to avoid IPC copying overhead 
# for workers, but Python fork makes it cheap.
global_eth_prices = None
global_usdc_rates = None

def init_worker(eth_arr, rate_arr):
    global global_eth_prices, global_usdc_rates
    global_eth_prices = eth_arr
    global_usdc_rates = rate_arr

def compute_window(args):
    start_idx, window_hours, ts = args
    end_idx = start_idx + window_hours
    
    eth_window = global_eth_prices[start_idx:end_idx]
    rate_window = global_usdc_rates[start_idx:end_idx]
    
    eth_log = np.log(eth_window)
    
    if np.std(eth_log) == 0 or np.std(rate_window) == 0:
        return (ts, 1.0)
        
    diff_eth = np.diff(eth_log)
    diff_rate = np.diff(rate_window)
    
    best_lag = 0
    best_corr = -1.0
    
    # Constrict search to 48 Hours for massive parallel speed optimization
    max_search_lag = 48 
    
    for lag in range(max_search_lag):
        if lag == 0:
            e, r = diff_eth, diff_rate
        else:
            e, r = diff_eth[:-lag], diff_rate[lag:]
            
        std_e = np.std(e)
        std_r = np.std(r)
        
        if std_e > 0 and std_r > 0:
            corr = np.corrcoef(e, r)[0, 1]
            if corr > best_corr:
                best_corr = corr
                best_lag = lag
                
    if best_lag == 0:
        e_a, r_a = eth_log, rate_window
    else:
        e_a, r_a = eth_log[:-best_lag], rate_window[best_lag:]
        
    X = np.vstack([e_a, np.ones(len(e_a))]).T
    beta, alpha = np.linalg.lstsq(X, r_a, rcond=None)[0]
    
    residuals = r_a - (beta * e_a + alpha)
    
    try:
        pvalue = adfuller(residuals, maxlag=1)[1]
    except:
        pvalue = 1.0
        
    return (ts, pvalue)


def generate_visuals():
    db_path = "/home/ubuntu/RLD/backend/clean_rates_readonly.db"
    print(f"📡 Loading 1H Data from DB: {db_path}...")
    
    conn = sqlite3.connect(db_path)
    query = """
        SELECT timestamp, eth_price, usdc_rate 
        FROM hourly_stats 
        WHERE eth_price IS NOT NULL AND usdc_rate IS NOT NULL
        ORDER BY timestamp ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
    df = df.set_index('datetime').resample('1h').ffill().dropna()
    
    timestamps = list(df.index)
    eth_prices = df['eth_price'].values
    usdc_rates = df['usdc_rate'].values
    
    n_total = len(eth_prices)
    window_hours = 90 * 24  # 2160 hours
    total_steps = n_total - window_hours
    
    print(f"🚀 Multiprocessing {total_steps} overlapping regressions...")
    
    tasks = [
        (i, window_hours, timestamps[i + window_hours - 1]) 
        for i in range(total_steps)
    ]
    
    # Execute across CPU pool
    cores = cpu_count()
    print(f"Allocating {cores} Python worker pools...")
    
    with Pool(cores, initializer=init_worker, initargs=(eth_prices, usdc_rates)) as pool:
        results = pool.map(compute_window, tasks, chunksize=500)
    
    print("✅ Regressions Complete! Generating Visuals...")
    
    # Build timeline dataframe
    df_results = pd.DataFrame(results, columns=['Timestamp', 'p_value'])
    df_results.set_index('Timestamp', inplace=True)
    df_results = df_results.sort_index()
    
    # Output to CSV for archive
    csv_out = "/home/ubuntu/.gemini/antigravity/brain/111bf96c-cbbc-44dc-abea-9821b77a260e/artifacts/p_values_1h_history.csv"
    df_results.to_csv(csv_out)
    
    # ── Visualize Series Chart ──
    fig, ax = plt.subplots(figsize=(16, 7))
    
    # Standard line plot
    ax.plot(df_results.index, df_results['p_value'], color='#00F0FF', lw=1.5, alpha=0.85, label='Engle-Granger P-Value')
    
    # Plot Significance Boundary
    ax.axhline(y=0.05, color='#FF2A55', linestyle='--', linewidth=2, label='Statistical Cointegration Threshold (p=0.05)')
    ax.fill_between(df_results.index, 0, 0.05, color='#00F0FF', alpha=0.1)
    
    # Style logic
    ax.set_title("1H High-Frequency ADF Cointegration P-Value (Rolling 90-Day Window)", fontsize=18, pad=20, weight='bold', color='white')
    ax.set_ylabel("P-Value", fontsize=14, color='#A0AAB4')
    ax.set_xlabel("Timeline (UTC)", fontsize=14, color='#A0AAB4')
    ax.grid(axis='y', color='#2C313C', linestyle='-', linewidth=0.5, alpha=0.5)
    
    # Hide top and right borders
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color('#2C313C')
    ax.spines['bottom'].set_color('#2C313C')
    
    ax.legend(loc='upper right', fontsize=12, frameon=False)
    ax.set_ylim(0, max(0.20, df_results['p_value'].max() + 0.05))
    
    img_path = "/home/ubuntu/.gemini/antigravity/brain/111bf96c-cbbc-44dc-abea-9821b77a260e/artifacts/pvalue_series_chart.png"
    plt.tight_layout()
    plt.savefig(img_path, dpi=250, facecolor='#0D1117', transparent=False)
    print(f"🎨 Saved Premium Chart -> {img_path}")

if __name__ == "__main__":
    generate_visuals()
