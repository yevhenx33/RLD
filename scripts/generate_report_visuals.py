import math
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np

def generate_visual_report():
    sns.set_theme(style="darkgrid", palette="rocket")
    
    apys = [0.10, 0.25, 0.50, 1.00]
    initial_hfs = np.linspace(1.01, 1.50, 100)
    
    collateral = 10000.0
    lt = 0.85
    
    plt.figure(figsize=(10, 6))
    
    for apy in apys:
        days = []
        for hf in initial_hfs:
            debt = (collateral * lt) / hf
            r = math.log(1 + apy)
            req_ratio = (collateral * lt) / debt
            exact_days = (365.0 * math.log(req_ratio)) / r if (req_ratio > 0 and r > 0) else 0
            days.append(exact_days)
            
        plt.plot(initial_hfs, days, label=f"{int(apy*100)}% APY", linewidth=2)
        
    # Draw danger zones
    plt.axhline(y=30, color='r', linestyle='--', alpha=0.7, label='30-Day Danger Zone')
    plt.axhline(y=7, color='maroon', linestyle=':', alpha=0.9, label='7-Day Critical Zone')
    
    plt.fill_between(initial_hfs, 0, 30, color='red', alpha=0.05)
    
    plt.title("Time to Ruin (Liquidation) vs. Initial Health Factor\n(Under Accelerated Interest Accrual)", fontsize=14, fontweight='bold')
    plt.xlabel("Initial Health Factor", fontsize=12)
    plt.ylabel("Days until HF < 1.0", fontsize=12)
    plt.ylim(0, 180)
    plt.xlim(1.01, 1.50)
    plt.legend(loc='upper left')
    plt.tight_layout()
    
    out_path = "/home/ubuntu/.gemini/antigravity/brain/0cca6f44-041d-4640-93a7-887c2916c872/artifacts/liquidation_curve.png"
    plt.savefig(out_path, dpi=300, bbox_inches='tight')
    print(f"Saved figure to {out_path}")

if __name__ == "__main__":
    generate_visual_report()
