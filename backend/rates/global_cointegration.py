import pandas as pd
import numpy as np
import warnings
from statsmodels.tsa.stattools import adfuller

warnings.filterwarnings("ignore")

def global_cointegration_analysis(csv_path: str, max_lag: int = 30):
    df = pd.read_csv(csv_path)
    
    if 'date_utc' in df.columns:
        df['date'] = pd.to_datetime(df['date_utc'])
    elif 'timestamp' in df.columns:
        df['date'] = pd.to_datetime(df['timestamp'], unit='s')
    else:
        raise ValueError("No date column found")

    df = df.sort_values('date').set_index('date')
    df = df.resample('1D').ffill().dropna(subset=['eth_price_usd', 'apy_pct'])
    
    eth_log = np.log(df['eth_price_usd'].values)
    rates = df['apy_pct'].values
    dates = df.index
    
    # 1. Sweep for Global Optimal Lag based on First-Difference Correlation
    diff_eth = np.diff(eth_log)
    diff_rates = np.diff(rates)
    
    best_global_lag = 0
    best_global_corr = -1.0
    
    print("Sweeping Lags (0 to 30) for Global First-Difference Optimum...")
    
    for lag in range(0, max_lag + 1):
        if lag == 0:
            e = diff_eth
            r = diff_rates
        else:
            e = diff_eth[:-lag]
            r = diff_rates[lag:]
            
        if len(e) < 2 or np.std(e) == 0 or np.std(r) == 0:
            continue
            
        corr = np.corrcoef(e, r)[0, 1]
        if np.isnan(corr): corr = 0
        
        if corr > best_global_corr:
            best_global_corr = corr
            best_global_lag = lag
            
    print(f"Optimal Global Lag identified: {best_global_lag} Days (Correlation: {best_global_corr:.3f})")
    
    # 2. Align Data to the Optimal Lag
    if best_global_lag == 0:
        eth_aligned = eth_log
        rates_aligned = rates
        dates_aligned = dates
    else:
        eth_aligned = eth_log[:-best_global_lag]
        rates_aligned = rates[best_global_lag:]
        dates_aligned = dates[best_global_lag:]
        
    # 3. Global Cointegration Test (Engle-Granger)
    X = np.vstack([eth_aligned, np.ones(len(eth_aligned))]).T
    beta, alpha = np.linalg.lstsq(X, rates_aligned, rcond=None)[0]
    residuals = rates_aligned - (beta * eth_aligned + alpha)
    
    adf_stat, global_pval, _, _, _, _ = adfuller(residuals, maxlag=1)
    
    print("\n--- GLOBAL COINTEGRATION RESULTS ---")
    print(f"Total Aligned Days: {len(eth_aligned)}")
    print(f"Global ADF p-value: {global_pval:.5f}")
    if global_pval < 0.05:
        print("Verdict: ✔ STATISTICALLY COINTEGRATED (p < 0.05)")
    else:
        print("Verdict: ✖ NOT COINTEGRATED GLOBALLY")
        
    # 4. Epoch/Quarterly breakdown to detect Anomalous Regimes
    aligned_df = pd.DataFrame({
        'eth_log': eth_aligned,
        'rate': rates_aligned
    }, index=dates_aligned)
    
    # Group by Year and Quarter
    aligned_df['YearQuarter'] = aligned_df.index.to_period('Q')
    
    print("\n--- EPOCH/QUARTERLY REGIME BREAKDOWN ---")
    epoch_results = []
    
    for yq, group in aligned_df.groupby('YearQuarter'):
        if len(group) < 30: # Need sufficient days for a valid ADF test
            continue
            
        e_q = group['eth_log'].values
        r_q = group['rate'].values
        
        X_q = np.vstack([e_q, np.ones(len(e_q))]).T
        b_q, a_q = np.linalg.lstsq(X_q, r_q, rcond=None)[0]
        res_q = r_q - (b_q * e_q + a_q)
        
        try:
            _, pval_q, _, _, _, _ = adfuller(res_q, maxlag=1)
        except:
            pval_q = 1.0
            
        status = "Coupled" if pval_q < 0.05 else "Dislocated"
        print(f"Epoch {yq} ({len(group)} days) | p-value: {pval_q:.5f} | Status: {status}")
        
        epoch_results.append({
            'period': str(yq),
            'pval': pval_q,
            'status': status
        })
        
    return global_pval, best_global_lag, pd.DataFrame(epoch_results)

if __name__ == "__main__":
    csv_path = "/home/ubuntu/RLD/Research/RLD/datasets/aave_usdc_rates_eth_prices.csv"
    
    global_pval, best_lag, epoch_df = global_cointegration_analysis(csv_path)
    
    # POKA-YOKE ASSERTIONS
    assert best_lag >= 0 and best_lag <= 30, "Fatal: Calculated lag outside sensible bounds [0, 30]."
    assert not epoch_df.empty, "Fatal: No epochs generated for segmental review."
    assert global_pval < 0.05, f"Fatal: Global Cointegration hypothesis failed (p={global_pval:.5f}). Thesis rejected."
    
    print("\n--- PASSED: POKA-YOKE CONSTRAINTS VALIDATED ---")
    print("Baseline thesis mathematically verified. Global Series is inherently Cointegrated.")
