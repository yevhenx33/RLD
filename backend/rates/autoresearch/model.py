import numpy as np

def run_optimal_model(eth_log: np.ndarray, apy_pct: np.ndarray) -> tuple[np.ndarray, int]:
    """
    Agent Modified Model.
    
    Iteration 5: Vectorized Global Engle-Granger Sweep with Dynamic Lag Shift.
    Discovered Strategy: Search for the absolute first-difference Pearson max over lag space {0..30}, 
    then shift the raw array and map pure ordinary least squares (OLS) linear progression.
    
    Returns:
        residuals (np.ndarray): The error vectors to feed into the ADF evaluation.
        num_params (int): Total independent parameters configured to calculate score penalties.
    """
    diff_eth = np.diff(eth_log)
    diff_rate = np.diff(apy_pct)
    
    best_lag = 0
    best_corr = -1.0
    
    # 1. First-Difference Optimal Lag Pipeline
    for lag in range(0, 31):
        if lag == 0:
            e = diff_eth
            r = diff_rate
        else:
            e = diff_eth[:-lag]
            r = diff_rate[lag:]
            
        if len(e) < 2 or np.std(e) == 0 or np.std(r) == 0:
            continue
            
        corr = np.corrcoef(e, r)[0, 1]
        
        if corr > best_corr:
            best_corr = corr
            best_lag = lag
            
    # 2. Alignment using the optimal shifting bounds
    if best_lag == 0:
        eth_aligned = eth_log
        rate_aligned = apy_pct
    else:
        eth_aligned = eth_log[:-best_lag]
        rate_aligned = apy_pct[best_lag:]
            
    # 3. Formally optimized Structural OLS Equation
    # Calculates Rate(t+L) = Beta * ETH(t) + Alpha
    X = np.vstack([eth_aligned, np.ones(len(eth_aligned))]).T
    beta, alpha = np.linalg.lstsq(X, rate_aligned, rcond=None)[0]
    
    # Generate OLS Residuals sequence
    residuals = rate_aligned - (beta * eth_aligned + alpha)
    
    # Complexity: 1 (Lag search component) + 2 (Beta, Alpha OLS weights) = 3 Parameters
    num_params = 3 
    
    return residuals, num_params
