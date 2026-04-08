import pandas as pd
import numpy as np
from statsmodels.tsa.stattools import adfuller

def load_and_preprocess_data(csv_path: str) -> pd.DataFrame:
    """Fixed Data Pipeline. Do not modify."""
    df = pd.read_csv(csv_path)
    
    if 'date_utc' in df.columns:
        df['date'] = pd.to_datetime(df['date_utc'])
    elif 'timestamp' in df.columns:
        df['date'] = pd.to_datetime(df['timestamp'], unit='s')
        
    df = df.sort_values('date').set_index('date')
    
    # Strictly enforce continuous daily grid via forward-fill
    df_clean = df.resample('1D').ffill().dropna(subset=['eth_price_usd', 'apy_pct'])
    
    # We return the natural log of ETH as standard macro practice, but leave APY as is.
    df_clean['eth_log'] = np.log(df_clean['eth_price_usd'])
    return df_clean

def evaluate_fitness(residuals: np.ndarray, num_params: int) -> float:
    """
    Autoresearch Primary Objective Function.
    Calculates the ADF p-value, applying an empirical AIC-style complexity penalty.
    
    Fitness = p_value + (num_params * 0.05)
    If fitness is high, the model is rejected. Lower is better.
    """
    try:
        # We maxlag=1 to ensure the agent doesn't rely purely on massive stochastic lags behind the scenes
        adf_stat, pvalue, _, _, _, _ = adfuller(residuals, maxlag=1)
    except Exception as e:
        # Catastrophic failure in model logic -> massive penalty
        return 100.0

    # Information Criterion-esque penalty. Every extra param costs 0.01 p-value equivalence.
    penalty = num_params * 0.01
    fitness = pvalue + penalty
    
    return float(fitness)
