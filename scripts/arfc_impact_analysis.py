import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from typing import List, Dict

def calculate_days_to_liquidation(collateral: float, total_debt: float, usdc_debt: float, lt: float, apy: float) -> float:
    """
    Solves for the exact day HF crosses 1.0, strictly applying the APY ONLY to the USDC portion of the debt.
    Equation: Collateral * LT = Non_USDC_Debt + USDC_Debt * e^(r * t)
    """
    if total_debt <= 0 or collateral <= 0 or lt <= 0:
        return np.inf
        
    current_hf = (collateral * lt) / total_debt
    if current_hf < 1.0:
        return 0.0
        
    if usdc_debt <= 0:
        return np.inf
        
    non_usdc_debt = total_debt - usdc_debt
    threshold_debt = collateral * lt
    
    # If non_usdc_debt alone exceeds the liquidation threshold, they are already dead.
    if threshold_debt <= non_usdc_debt:
        return 0.0
        
    required_usdc_growth_ratio = (threshold_debt - non_usdc_debt) / usdc_debt
    
    if required_usdc_growth_ratio < 1.0:
        return 0.0
        
    r = math.log(1 + apy)
    if r <= 0:
        return np.inf
        
    return (365.0 * math.log(required_usdc_growth_ratio)) / r

def execute_analysis(csv_path: str, output_img_path: str, output_md_path: str):
    df = pd.read_csv(csv_path)
    
    # Poka-Yoke: Filter out dust. Only analyze material systemic risk.
    material_df = df[df['usdc_debt_events_usd'] >= 10000].copy()
    
    scenarios = {
        'Status Quo (14%)': 0.14,
        'Interim (40%)': 0.40,
        'Target (50%)': 0.50
    }
    
    for label, apy in scenarios.items():
        col_name = f"days_{int(apy*100)}"
        material_df[col_name] = material_df.apply(
            lambda x: calculate_days_to_liquidation(
                x['collateral_usd'], x['total_debt_usd'], x['usdc_debt_events_usd'], x['liquidation_threshold'], apy
            ), axis=1
        )
        
    # --- Generate Visuals ---
    sns.set_theme(style="darkgrid", palette="muted")
    plt.figure(figsize=(12, 7))
    
    time_horizons = np.arange(0, 61, 1) # 0 to 60 days
    
    for label, apy in scenarios.items():
        col_name = f"days_{int(apy*100)}"
        cumulative_liq = []
        for d in time_horizons:
            # Sum USDC debt liquidated by day d
            vol = material_df[material_df[col_name] <= d]['usdc_debt_events_usd'].sum()
            cumulative_liq.append(vol)
        
        plt.plot(time_horizons, cumulative_liq, label=f"{label} APY", linewidth=3)
        
    plt.axvline(x=7, color='maroon', linestyle='--', alpha=0.5, label='7-Day Critical')
    plt.axvline(x=30, color='red', linestyle='--', alpha=0.5, label='30-Day Danger')
    
    plt.title("Systemic Cumulative USDC Liquidated Over Time (> $10k Debt Only)\nARFC Parameter Shocks", fontsize=16, fontweight='bold')
    plt.xlabel("Days Passed", fontsize=12)
    plt.ylabel("Cumulative USDC Liquidated ($)", fontsize=12)
    
    # Format y-axis as millions
    formatter = plt.FuncFormatter(lambda x, pos: f'${x*1e-6:.1f}M')
    plt.gca().yaxis.set_major_formatter(formatter)
    
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_img_path, dpi=300)
    plt.close()
    
    # --- Generate Markdown Report ---
    with open(output_md_path, "w") as f:
        f.write("# ARFC Impact Analysis: USDC Interest Rate Shock\n\n")
        f.write("## 1. Systemic Exposure Overview\n")
        f.write("By isolating accounts with $>\\$10,000$ in USDC debt, we filtered out retail dust to focus exclusively on systemic liquidity risks.\n\n")
        
        f.write("### Cumulative Liquidated Volume ($)\n")
        f.write("| Horizon | Status Quo (14%) | Interim (40%) | Target (50%) |\n")
        f.write("|---------|------------------|---------------|--------------|\n")
        
        for d in [7, 14, 30, 60]:
            sq = material_df[material_df['days_14'] <= d]['usdc_debt_events_usd'].sum()
            interim = material_df[material_df['days_40'] <= d]['usdc_debt_events_usd'].sum()
            target = material_df[material_df['days_50'] <= d]['usdc_debt_events_usd'].sum()
            f.write(f"| **{d} Days** | ${sq:,.2f} | ${interim:,.2f} | ${target:,.2f} |\n")
            
        f.write("\n## 2. Visual At-Risk Curve\n")
        f.write(f"![Cumulative Impact]({output_img_path})\n\n")
        
        f.write("## 3. Top 5 Whale Accounts at Immediate Risk (< 14 Days @ 50% APY)\n")
        whales = material_df[(material_df['days_50'] > 0) & (material_df['days_50'] <= 14)].sort_values('usdc_debt_events_usd', ascending=False).head(5)
        f.write("| Address | USDC Debt | Initial HF | Days until Liq (50%) |\n")
        f.write("|---------|-----------|------------|----------------------|\n")
        for _, w in whales.iterrows():
            f.write(f"| `{w['address']}` | ${w['usdc_debt_events_usd']:,.2f} | {w['hf']:.4f} | {w['days_50']:.1f} |\n")
            
        f.write("\n> [!CAUTION]\n> The above accounts will mathematically require collateral top-ups or debt repayments. If they are rate-insensitive (as per the ARFC hypothesis), they will sit inert until liquidation triggers.\n")

if __name__ == "__main__":
    # POKA-YOKE Verification
    test_lt = 0.85
    test_col = 10000.0
    test_tot_debt = 8000.0
    test_usdc = 8000.0
    
    # HF = 8500 / 8000 = 1.0625
    # Required usdc ratio = 8500 / 8000 = 1.0625
    # @ 50% APY, r = ln(1.5) = 0.4054
    # t = 365 * ln(1.0625) / 0.4054 = ~54.5 days
    d_50 = calculate_days_to_liquidation(test_col, test_tot_debt, test_usdc, test_lt, 0.50)
    assert 54.0 < d_50 < 55.0, f"Math logic failure: Expected ~54.5, got {d_50}"
    
    # Pure non-USDC debt failure
    d_non = calculate_days_to_liquidation(test_col, 9000.0, 0.0, test_lt, 0.50) # HF = 8500/9000 < 1
    assert d_non == 0.0, "Failed to catch pre-liquidated non-USDC state"
    
    print("[✔] Poka-Yoke Math Boundaries Verified.")
    
    execute_analysis(
        "/home/ubuntu/RLD/scripts/artifacts/usdc_hf_sorted_envio_reconstruction_2026-04-23.csv",
        "/home/ubuntu/.gemini/antigravity/brain/0cca6f44-041d-4640-93a7-887c2916c872/artifacts/usdc_arfc_cumulative_impact.png",
        "/home/ubuntu/.gemini/antigravity/brain/0cca6f44-041d-4640-93a7-887c2916c872/artifacts/usdc_arfc_impact_report.md"
    )
    print("[✔] Full dataset processed and artifacts exported deterministically.")
