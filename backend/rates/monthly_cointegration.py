import pandas as pd
import numpy as np
import warnings
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

def monthly_cointegration_analysis(csv_path: str):
    df = pd.read_csv(csv_path)
    
    if 'date_utc' in df.columns:
        df['date'] = pd.to_datetime(df['date_utc'])
    elif 'timestamp' in df.columns:
        df['date'] = pd.to_datetime(df['timestamp'], unit='s')
    else:
        raise ValueError("No date column found")

    df = df.sort_values('date').set_index('date')
    
    # -------------------------------------------------------------
    # PRE-PROCESS: Ensure base daily completeness
    # -------------------------------------------------------------
    df_daily = df.resample('1D').ffill().dropna(subset=['eth_price_usd', 'apy_pct'])
    
    # -------------------------------------------------------------
    # PASS 1: GLOBAL MACRO SMOOTHING (Monthly Resample)
    # -------------------------------------------------------------
    # Resample to month-end frequency ('ME') to average out intraday/intramonth noise.
    df_monthly = df_daily[['eth_price_usd', 'apy_pct']].resample('ME').mean()
    
    eth_log_m = np.log(df_monthly['eth_price_usd'].values)
    rates_m = df_monthly['apy_pct'].values
    
    print("--- PASS 1: MACRO SMOOTHED GLOBAL TEST (Monthly Resolution) ---")
    print(f"Data Points (N): {len(eth_log_m)}")
    
    X_m = np.vstack([eth_log_m, np.ones(len(eth_log_m))]).T
    b_m, a_m = np.linalg.lstsq(X_m, rates_m, rcond=None)[0]
    res_m = rates_m - (b_m * eth_log_m + a_m)
    
    try:
        # Note: ADF power drops significantly for N < 40. Keep maxlag small.
        _, pval_m, _, _, _, _ = adfuller(res_m, maxlag=1)
    except:
        pval_m = 1.0
        
    print(f"Monthly Global p-value: {pval_m:.5f}")
    if pval_m < 0.05:
        print("Verdict: ✔ STATISTICALLY COINTEGRATED ON MACRO TIMEFRAME (p < 0.05)")
    else:
        print("Verdict: ✖ NOT COINTEGRATED OR FALSE NEGATIVE DUE TO LOW DOF")

    # -------------------------------------------------------------
    # PASS 2: MICRO SEGMENTS (Monthly grouping of Daily Data)
    # -------------------------------------------------------------
    print("\n--- PASS 2: MICRO REGIME BREAKDOWN (Monthly Segments of Daily Data) ---")
    
    df_daily['YearMonth'] = df_daily.index.to_period('M')
    epoch_results = []
    
    for ym, group in df_daily.groupby('YearMonth'):
        if len(group) < 20: 
            continue # Skip incomplete months
            
        e_q = np.log(group['eth_price_usd'].values)
        r_q = group['apy_pct'].values
        
        X_q = np.vstack([e_q, np.ones(len(e_q))]).T
        b_q, a_q = np.linalg.lstsq(X_q, r_q, rcond=None)[0]
        res_q = r_q - (b_q * e_q + a_q)
        
        try:
            _, pval_q, _, _, _, _ = adfuller(res_q, maxlag=1)
        except:
            pval_q = 1.0
            
        status = "Coupled" if pval_q < 0.05 else "Dislocated"
        
        # Highlighting the specific failing months (like 08/2025 finding from before)
        out = f"Month {ym} ({len(group)} days) | p-value: {pval_q:.5f} | Status: {status}"
        if status == "Dislocated":
             out = f"⚠️ {out}"
        print(out)
        
        epoch_results.append({
            'period': str(ym),
            'pval': pval_q,
            'status': status
        })
        
    return pval_m, pd.DataFrame(epoch_results)

if __name__ == "__main__":
    csv_path = "/home/ubuntu/RLD/Research/RLD/datasets/aave_usdc_rates_eth_prices.csv"
    
    global_pval, epoch_df = monthly_cointegration_analysis(csv_path)
    
    # 1. POKA-YOKE ASSERTIONS
    assert not epoch_df.empty, "Fatal: No monthly epochs generated."
    # We assert that the breakdown caught at least one valid coupled month
    assert len(epoch_df[epoch_df['status'] == 'Coupled']) > 0, "Fatal: Failed to identify any valid historical coupling."
    
    print("\n--- PASSED: POKA-YOKE CONSTRAINTS VALIDATED ---")
    print(f"Evaluated {len(epoch_df)} discrete Monthly Regimes.")
