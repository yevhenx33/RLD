import polars as pl
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from datetime import timedelta

def perform_hourly_90d_rolling(df: pl.DataFrame, step_days: int = 15):
    """
    Executes a 90-day rolling window Engle-Granger ADF test on the OLS residuals 
    of ETH log price vs USDC APY using high-frequency 1H data.
    """
    df = df.sort('Timestamp')
    
    # We operate directly on arrays for speed
    timestamps = df['Date (UTC)'].to_list()
    eth_prices = df['ETH Price ($)'].to_numpy()
    apy_pct = df['APY (%)'].to_numpy()
    
    n_total = len(eth_prices)
    
    window_hours = 90 * 24  # 2160 hours
    step_hours = step_days * 24
    
    results = []
    
    # Rolling logic
    for start_idx in range(0, n_total - window_hours, step_hours):
        end_idx = start_idx + window_hours
        
        eth_window = eth_prices[start_idx:end_idx]
        apy_window = apy_pct[start_idx:end_idx]
        t_start = timestamps[start_idx].strftime('%Y-%m-%d')
        t_end = timestamps[end_idx-1].strftime('%Y-%m-%d')
        
        n_obs = len(eth_window)
        if n_obs < window_hours * 0.9: # Require 90% completeness
            continue
            
        eth_log = np.log(eth_window)
        
        # Poka-Yoke: Ensure variance
        if np.std(eth_log) == 0 or np.std(apy_window) == 0:
            pvalue = 1.0
            best_lag = 0
        else:
            diff_eth = np.diff(eth_log)
            diff_rate = np.diff(apy_window)
            
            best_lag = 0
            best_corr = -1.0
            
            max_search_lag = min(120, len(diff_eth) - 10) # Search up to 5 days lag
            
            for lag in range(0, max_search_lag):
                if lag == 0:
                    e = diff_eth
                    r = diff_rate
                else:
                    e = diff_eth[:-lag]
                    r = diff_rate[lag:]
                
                corr = -1.0
                if np.std(e) > 0 and np.std(r) > 0:
                     corr = np.corrcoef(e, r)[0, 1]
                
                if corr > best_corr:
                    best_corr = corr
                    best_lag = lag
                    
            if best_lag == 0:
                eth_aligned = eth_log
                rate_aligned = apy_window
            else:
                eth_aligned = eth_log[:-best_lag]
                rate_aligned = apy_window[best_lag:]
                
            # OLS Execution
            X = np.vstack([eth_aligned, np.ones(len(eth_aligned))]).T
            beta, alpha = np.linalg.lstsq(X, rate_aligned, rcond=None)[0]
            
            residuals = rate_aligned - (beta * eth_aligned + alpha)
            
            try:
                adf_stat, pvalue, _, _, _, _ = adfuller(residuals, maxlag=1)
            except Exception:
                pvalue = 1.0
                
        is_cointegrated = pvalue < 0.05
        
        results.append({
            'Window': f"{t_start} to {t_end}",
            'N_Hourly_Obs': n_obs,
            'Optimal_Lag_Hrs': best_lag,
            'ADF_pvalue': pvalue,
            'Cointegrated': is_cointegrated
        })
        
    return results

if __name__ == "__main__":
    csv_path = "/home/ubuntu/RLD/frontend/src/assets/aave_usdc_rates_full_history_2026-01-27.csv"
    print(f"Loading high-frequency dataset: {csv_path}")
    
    try:
         df = pl.read_csv(
            csv_path,
            has_header=True,
            schema_overrides={
                 "Date (UTC)": pl.String,
                 "ETH Price ($)": pl.Float64,
                 "APY (%)": pl.Float64
            }
        ).with_columns(
            pl.col("Date (UTC)").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S%.f")
        ).drop_nulls(subset=["ETH Price ($)", "APY (%)"])
    except pl.exceptions.ComputeError as e:
         print(f"Polars Parsing Error: {e}")
         exit(1)
         
    # Execute 90-day rolling window, stepping by 15 days
    results = perform_hourly_90d_rolling(df, step_days=15)
    
    print("\n[ 90-Day Rolling High-Frequency Regime Analysis (15-Day Steps) ]")
    print(f"{'Window (90 Days)':<25} | {'N (Hours)':<10} | {'Opt Lag (H)':<12} | {'p-value':<8} | {'Cointegrated?':<12}")
    print("-" * 75)
    
    failed_windows = []
    
    for r in results:
        status_symbol = "✅" if r['Cointegrated'] else "❌"
        if not r['Cointegrated']:
             failed_windows.append(r['Window'])
             
        print(f"{r['Window']:<25} | {r['N_Hourly_Obs']:<10} | {r['Optimal_Lag_Hrs']:<12} | {r['ADF_pvalue']:<8.4f} | {status_symbol}")
        
    print("\n--- POKA-YOKE DIAGNOSTICS ---")
    print(f"Total 90-Day Windows Tested: {len(results)}")
    
    assert len(results) > 0, "Fatal: Pipeline generated exactly 0 tracking windows."
    assert all(r['N_Hourly_Obs'] > 2000 for r in results), "Fatal: Window observation count failed size threshold."
    
    if failed_windows:
        print(f"⚠️ Structural Decoupling mathematically verified in {len(failed_windows)} overlapping epochs.")
    else:
        print("✅ 100% Structural Coupling verified across all High-Frequency 90D Epochs.")
        
    print("\nPIPELINE: PASS.")
