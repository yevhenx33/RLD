import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns

# Import regimes from our simulation script
from ou_simulation_v6 import regimes

def generate_latex_charts():
    # Standardize classic LaTeX style
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["DejaVu Serif", "Computer Modern Roman", "Times New Roman"],
        "mathtext.fontset": "cm",
        "axes.labelsize": 12,
        "font.size": 12,
        "legend.fontsize": 10,
        "xtick.labelsize": 11,
        "ytick.labelsize": 11,
        "axes.grid": True,
        "grid.linestyle": ":",
        "grid.alpha": 0.6,
        "figure.figsize": (9, 6)
    })
    
    # Data extraction
    names = [r['name'] for r in regimes]
    vanilla_means = [r['vanilla_mean'] * 100 for r in regimes]
    baseline_means = [r['baseline_mean'] * 100 for r in regimes]
    
    x = np.arange(len(names))
    width = 0.35
    
    fig, ax = plt.subplots()
    rects1 = ax.bar(x - width/2, vanilla_means, width, label='Unhedged Yield', color='#e74c3c', alpha=0.9)
    rects2 = ax.bar(x + width/2, baseline_means, width, label='Baseline Hedged Yield', color='#34495e', alpha=0.9)
    
    ax.axhline(10.0, color='black', linestyle='--', alpha=0.8, label='Target Fixed Yield (10%)')

    ax.set_ylabel('Annualized Yield (%)')
    ax.set_title('Variance Reduction: Unhedged vs. Hedged Yield across Market Regimes', fontweight='bold', pad=15)
    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.legend(loc='lower right')
    
    # Adding data labels
    def autolabel(rects):
        for rect in rects:
            height = rect.get_height()
            ax.annotate(f'{height:.2f}%',
                        xy=(rect.get_x() + rect.get_width() / 2, height),
                        xytext=(0, 3),  # 3 points vertical offset
                        textcoords="offset points",
                        ha='center', va='bottom', fontsize=9)

    autolabel(rects1)
    autolabel(rects2)

    fig.tight_layout()
    plt.savefig("../assets/ou_yield_comparison.png", dpi=300, bbox_inches='tight')
    print("Saved ou_yield_comparison.png with LaTeX styling.")

if __name__ == "__main__":
    generate_latex_charts()
