import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

np.random.seed(42)

# Parameters
N_PRINCIPAL = 100000
TARGET_YIELD = 0.10
K = 100
T_DAYS = 365
dt = 1/365
N_PATHS = 1000

FUNDING_PERIOD_DAYS = 30

def run_paths(mu, theta, sigma):
    r0 = TARGET_YIELD
    paths = np.zeros((N_PATHS, T_DAYS))
    paths[:, 0] = r0
    for t in range(1, T_DAYS):
        dW = np.random.normal(0, np.sqrt(dt), N_PATHS)
        dr = theta * (mu - paths[:, t-1]) * dt + sigma * dW
        paths[:, t] = np.maximum(0.001, paths[:, t-1] + dr)
    return paths

def simulate_discount(paths, discount, return_array=False):
    nf_paths = np.ones((N_PATHS, T_DAYS))
    for t in range(1, T_DAYS):
        index_price = K * paths[:, t]
        mark_price = index_price * nf_paths[:, t-1] * (1 - discount)
        normalized_mark_price = mark_price / nf_paths[:, t-1]
        
        funding_rate = (normalized_mark_price - index_price) / index_price
        exponent = -funding_rate * 1 / FUNDING_PERIOD_DAYS
        
        multiplier = np.exp(exponent)
        nf_paths[:, t] = nf_paths[:, t-1] * multiplier
        
    index_price_paths = K * paths
    mark_price_paths = index_price_paths * nf_paths * (1 - discount)
    
    Q_initial = N_PRINCIPAL / K
    hedged_balance = np.ones(N_PATHS) * (N_PRINCIPAL + (Q_initial * mark_price_paths[:, 0]))
    dQ = Q_initial / T_DAYS
    
    for t in range(T_DAYS):
        yield_earned = hedged_balance * paths[:, t] * dt
        hedged_balance += yield_earned
        
        buyback_cost = dQ * mark_price_paths[:, t]
        hedged_balance -= buyback_cost
        
    swap_fee = 0.0005 * Q_initial * mark_price_paths[:, 0]
    gas_overhead = 150
    hedged_balance -= (swap_fee + gas_overhead)
    
    hedged_effective_yield = (hedged_balance - N_PRINCIPAL) / N_PRINCIPAL
    if return_array:
        return np.mean(hedged_effective_yield), np.std(hedged_effective_yield), hedged_effective_yield
    return np.mean(hedged_effective_yield), np.std(hedged_effective_yield)

def simulate_regime(name, mu, theta, sigma):
    paths = run_paths(mu, theta, sigma)
    
    vanilla_balance = np.ones(N_PATHS) * N_PRINCIPAL
    for t in range(T_DAYS):
        vanilla_balance += vanilla_balance * paths[:, t] * dt
    vanilla_effective_yield = (vanilla_balance - N_PRINCIPAL) / N_PRINCIPAL
    vanilla_mean = np.mean(vanilla_effective_yield)
    vanilla_std = np.std(vanilla_effective_yield)
    
    baseline_mean, baseline_std, baseline_array = simulate_discount(paths, 0.0, return_array=True)
    
    if baseline_mean <= 0.10:
        tolerance = 0.0
        final_yield = baseline_mean
    else:
        low = 0.0
        high = 0.10
        for _ in range(20):
            mid = (low + high) / 2
            mid_yield, _ = simulate_discount(paths, mid)
            if mid_yield > 0.10:
                low = mid
            else:
                high = mid
        tolerance = (low + high) / 2
        final_yield, _ = simulate_discount(paths, tolerance)
        
    annualized_funding_rate = tolerance * (365 / 30)
    
    sharpe_ratio = baseline_mean / baseline_std if baseline_std > 0 else 0
        
    return {
        'name': name,
        'paths': paths,
        'vanilla_array': vanilla_effective_yield,
        'baseline_array': baseline_array,
        'target_mu': mu,
        'sigma': sigma,
        'vanilla_mean': vanilla_mean,
        'vanilla_std': vanilla_std,
        'baseline_mean': baseline_mean,
        'baseline_std': baseline_std,
        'sharpe_ratio': sharpe_ratio,
        'tolerance': tolerance,
        'annualized_funding_rate': annualized_funding_rate,
        'final_yield': final_yield
    }

regimes = [
    simulate_regime("Base (μ=8%)", 0.08, 2.5, 0.15),
    simulate_regime("Strong Bull (μ=25%)", 0.25, 2.5, 0.15),
    simulate_regime("Strong Bear (μ=2%)", 0.02, 2.5, 0.15),
    simulate_regime("Chaotic Vol (σ=0.4)", 0.08, 1.0, 0.40)
]

print("| Market Regime | Volatility $\\sigma$ | Unhedged Yield | Baseline Hedged Yield | Variance | Sharpe Ratio | Max Annualized Funding Drag Tolerance |")
print("|---|---|---|---|---|---|---|")
for r in regimes:
    tol_str = f"-{r['annualized_funding_rate']*100:.2f}%" if r['annualized_funding_rate'] > 0 else "0.00%"
    print(f"| {r['name']} | {r['sigma']:.0%} | {r['vanilla_mean']:.2%} | **{r['baseline_mean']:.2%}** | {r['baseline_std']:.2%} | {r['sharpe_ratio']:.2f} | **{tol_str}** |")
