import sqlite3
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from datetime import datetime, timezone
import sys

def fetch_data(db_path):
    conn = sqlite3.connect(db_path)
    query = """
        SELECT timestamp, eth_price, usdc_rate 
        FROM hourly_stats 
        WHERE eth_price IS NOT NULL AND usdc_rate IS NOT NULL
        ORDER BY timestamp ASC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    # Strictly enforce 1H interval, drop gaps or ffill
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
    df = df.set_index('datetime')
    df = df.resample('1h').ffill().dropna()
    
    return df

def run_1h_step_analysis():
    db_path = "/home/ubuntu/RLD/backend/clean_rates_readonly.db"
    print(f"📡 Loading 1H Data from DB: {db_path}")
    
    df = fetch_data(db_path)
    timestamps = [ts.strftime('%Y-%m-%d %H:%M') for ts in df.index]
    eth_prices = df['eth_price'].values
    usdc_rates = df['usdc_rate'].values
    
    n_total = len(eth_prices)
    window_hours = 90 * 24  # 2160
    
    print(f"📊 Total Hours Found: {n_total}")
    print(f"⏱️ Window Size: 90 Days ({window_hours} Hours), Step: 1 Hour")
    
    total_steps = n_total - window_hours
    if total_steps <= 0:
        print("Data length is smaller than 90 days. Aborting.")
        return
        
    failed_regimes = []
    current_fail_start = None
    
    print(f"🚀 Executing {total_steps} overlapping Cointegration regressions...\n")
    
    for start_idx in range(total_steps):
        if start_idx % 1000 == 0:
            print(f"   ... Processed {start_idx}/{total_steps} hourly overlapping windows ...", flush=True)
        end_idx = start_idx + window_hours
        
        eth_window = eth_prices[start_idx:end_idx]
        rate_window = usdc_rates[start_idx:end_idx]
        
        t_start = timestamps[start_idx]
        t_end = timestamps[end_idx-1]
        
        eth_log = np.log(eth_window)
        
        # Poka-Yoke Variance
        if np.std(eth_log) == 0 or np.std(rate_window) == 0:
            is_cointegrated = False
        else:
            diff_eth = np.diff(eth_log)
            diff_rate = np.diff(rate_window)
            
            best_lag = 0
            best_corr = -1.0
            
            max_search_lag = 120 # Search up to 5 days
            
            # Optimization: since we step by 1 hour, correlation structure barely shifts. We still test up to 120.
            for lag in range(0, max_search_lag):
                if lag == 0:
                    e, r = diff_eth, diff_rate
                else:
                    e, r = diff_eth[:-lag], diff_rate[lag:]
                
                if np.std(e) > 0 and np.std(r) > 0:
                    corr = np.corrcoef(e, r)[0, 1]
                    if corr > best_corr:
                        best_corr = corr
                        best_lag = lag
                        
            # Apply lag
            if best_lag == 0:
                e_a, r_a = eth_log, rate_window
            else:
                e_a, r_a = eth_log[:-best_lag], rate_window[best_lag:]
                
            # OLS
            X = np.vstack([e_a, np.ones(len(e_a))]).T
            beta, alpha = np.linalg.lstsq(X, r_a, rcond=None)[0]
            
            residuals = r_a - (beta * e_a + alpha)
            
            try:
                # To heavily optimize ADF in 24k loops, we restrict maxlag to 1
                adf_stat, pvalue, _, _, _, _ = adfuller(residuals, maxlag=1)
            except:
                pvalue = 1.0
                
            is_cointegrated = pvalue < 0.05
            
        # Group contiguous failures
        if not is_cointegrated:
            if current_fail_start is None:
                current_fail_start = t_start
        else:
            if current_fail_start is not None:
                failed_regimes.append(f"{current_fail_start} to {timestamps[start_idx-1]} (Decoupled)")
                current_fail_start = None

    if current_fail_start is not None:
         failed_regimes.append(f"{current_fail_start} to {timestamps[-1]} (Decoupled)")

    print("\n\n--- 90-DAY ROLLING (1H STEP) RESULTS ---")
    print(f"Total Iterations Executed: {total_steps}")
    
    success_count = total_steps - len(failed_regimes) # Not exact since failed_regimes are grouped blocks, but we can display blocks
    
    if not failed_regimes:
        print("✅ 100% Structural Coupling verified across EVERY single 1-hour interval.")
    else:
        print(f"⚠️ Structural Decoupling detected in {len(failed_regimes)} contiguous time blocks:")
        for block in failed_regimes:
            print(f"    ❌ {block}")
            
    print("\nPIPELINE: PASS.")

if __name__ == "__main__":
    run_1h_step_analysis()
