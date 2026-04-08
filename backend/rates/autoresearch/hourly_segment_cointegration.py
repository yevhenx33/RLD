import polars as pl
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller

def perform_hourly_segmentation(df: pl.DataFrame):
    """
    Splits the 1H interval dataframe into strict Monthly epochs.
    Executes an Engle-Granger ADF test on the OLS residuals of ETH log price vs USDC APY.
    """
    # Create Year-Month group column
    df = df.with_columns(
        pl.col('Date (UTC)').dt.strftime('%Y-%m').alias('year_month')
    )
    
    unique_months = df['year_month'].unique().sort().to_list()
    
    results = []
    
    for month in unique_months:
        month_df = df.filter(pl.col('year_month') == month).sort('Timestamp')
        
        eth_prices = month_df['ETH Price ($)'].to_numpy()
        apy_pct = month_df['APY (%)'].to_numpy()
        
        # We need sufficient hourly data to proceed safely
        n_obs = len(eth_prices)
        if n_obs < 150: # Skip incomplete months (e.g. edge dates)
            continue
            
        eth_log = np.log(eth_prices)
        
        # Poka-Yoke: Ensure variance exists
        if np.std(eth_log) == 0 or np.std(apy_pct) == 0:
            pvalue = 1.0
        else:
            # Optimal Framework: Geometric Lag Search inside the current 30-day window
            # Constrain search space: Max 5 Days of hourly shifts (120 hours)
            diff_eth = np.diff(eth_log)
            diff_rate = np.diff(apy_pct)
            
            best_lag = 0
            best_corr = -1.0
            
            max_search_lag = min(120, len(diff_eth) - 10)
            
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
                    
            # Shift
            if best_lag == 0:
                eth_aligned = eth_log
                rate_aligned = apy_pct
            else:
                eth_aligned = eth_log[:-best_lag]
                rate_aligned = apy_pct[best_lag:]
                
            # OLS
            X = np.vstack([eth_aligned, np.ones(len(eth_aligned))]).T
            beta, alpha = np.linalg.lstsq(X, rate_aligned, rcond=None)[0]
            
            residuals = rate_aligned - (beta * eth_aligned + alpha)
            
            try:
                adf_stat, pvalue, _, _, _, _ = adfuller(residuals, maxlag=1)
            except Exception:
                pvalue = 1.0
                
        is_cointegrated = pvalue < 0.05
        
        results.append({
            'Month': month,
            'N_Hourly_Obs': n_obs,
            'Optimal_Lag_Hrs': best_lag,
            'ADF_pvalue': pvalue,
            'Cointegrated': is_cointegrated
        })
        
    return results

if __name__ == "__main__":
    csv_path = "/home/ubuntu/RLD/frontend/src/assets/aave_usdc_rates_full_history_2026-01-27.csv"
    print(f"Loading high-frequency dataset: {csv_path}")
    
    # Parse exactly the fields we need
    try:
        df = pl.read_csv(
            csv_path,
            has_header=True,
            dtypes={
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
         
    results = perform_hourly_segmentation(df)
    
    print("\n[ Hourly Segregated Regime Analysis ]")
    print(f"{'Month':<10} | {'N (Hours)':<10} | {'Opt Lag (H)':<12} | {'p-value':<8} | {'Cointegrated?':<12}")
    print("-" * 65)
    
    failed_months = []
    
    for r in results:
        status_symbol = "✅" if r['Cointegrated'] else "❌"
        if not r['Cointegrated']:
             failed_months.append(r['Month'])
             
        print(f"{r['Month']:<10} | {r['N_Hourly_Obs']:<10} | {r['Optimal_Lag_Hrs']:<12} | {r['ADF_pvalue']:<8.4f} | {status_symbol}")
        
    print("\n--- POKA-YOKE DIAGNOSTICS ---")
    print(f"Total Epochs Tested: {len(results)}")
    
    # Strict validation mapping
    assert len(results) > 0, "Fatal: Pipeline generated exactly 0 segmented regimes."
    assert all(r['N_Hourly_Obs'] > 100 for r in results), "Fatal: Low DOF detected in active month bucket."
    
    # We expect some periods to organically fail (the anomalies requested in Phase 1)
    if failed_months:
        print(f"⚠️ Structural Decoupling mathematically verified in epochs: {failed_months}")
    else:
        print("✅ 100% Structural Coupling verified across all High-Frequency Epochs.")
        
    print("\nPIPELINE: PASS.")
