import matplotlib.pyplot as plt
import numpy as np

# Import regimes from our simulation script
from ou_simulation_v6 import regimes, N_PRINCIPAL, K, T_DAYS, dt

def generate_ltv_charts():
    # Standardize classic LaTeX style
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["DejaVu Serif", "Computer Modern Roman", "Times New Roman"],
        "mathtext.fontset": "cm",
        "axes.labelsize": 10,
        "font.size": 10,
        "legend.fontsize": 9,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "axes.grid": True,
        "grid.linestyle": ":",
        "grid.alpha": 0.6,
        "figure.figsize": (15, 5)
    })

    fig, axs = plt.subplots(1, 3, sharey=True)
    plt.subplots_adjust(wspace=0.05)

    # Indices: 1: Strong Bull, 2: Strong Bear, 3: Chaotic Vol
    regime_indices = [1, 2, 3]
    titles = ["Strong Bull (Rates $\\rightarrow$ 25%)", "Strong Bear (Rates $\\rightarrow$ 2%)", "Chaotic (Mean 10%)"]
    
    # Matching colors from user's image
    colors = ['#228B22', '#E74C3C', '#2980B9'] # Forest Green, Alizarin Red, Belize Blue
    
    Q_total = N_PRINCIPAL / K
    dQ = Q_total / T_DAYS
    
    for i, idx in enumerate(regime_indices):
        r = regimes[idx]
        paths = r['paths']
        ax = axs[i]
        
        # Calculate all LTV paths first
        ltv_paths_all = []
        for p in range(len(paths)):
            path = paths[p, :]
            
            # Reset state for each path
            hedged_balance = N_PRINCIPAL + Q_total * (K * path[0])
            Q_remaining = Q_total
            
            ltv_path = np.zeros(T_DAYS)
            for t in range(T_DAYS):
                debt_value = Q_remaining * (K * path[t])
                current_ltv = (debt_value / hedged_balance) * 100
                ltv_path[t] = current_ltv
                
                yield_earned = hedged_balance * path[t] * dt
                hedged_balance += yield_earned
                
                buyback_cost = dQ * (K * path[t])
                hedged_balance -= buyback_cost
                Q_remaining -= dQ
                
            ltv_paths_all.append(ltv_path)
            
        ltv_paths_all = np.array(ltv_paths_all)
        
        # Find global max
        max_observed_ltv = np.max(ltv_paths_all)
        max_path_idx = np.unravel_index(np.argmax(ltv_paths_all), ltv_paths_all.shape)[0]
        
        # Plot 50 paths INCLUDING the one with the maximum LTV
        num_paths_to_plot = 50
        paths_to_plot = list(range(min(num_paths_to_plot - 1, len(paths))))
        if max_path_idx not in paths_to_plot:
            paths_to_plot.append(max_path_idx)
            
        for p in paths_to_plot:
            # Highlight the max path slightly if desired, or just plot normally
            ax.plot(ltv_paths_all[p], color=colors[i], alpha=0.4, linewidth=0.8)
                
        # Liquidation Limit Line
        ax.axhline(91.7, color='#E74C3C', linewidth=1.5, label='Liquidation Limit (~91.7%)' if i == 0 else "")
        
        # Text box for max LTV
        textstr = f'Max Observed LTV: {max_observed_ltv:.1f}%'
        props = dict(boxstyle='square,pad=0.4', facecolor='white', alpha=0.8, edgecolor='gray', linewidth=0.5)
        ax.text(0.5, 0.85, textstr, transform=ax.transAxes, fontsize=9,
                verticalalignment='top', horizontalalignment='center', bbox=props)
                
        ax.set_title(titles[i], fontsize=11)
        ax.set_xlabel("Days into Contract")
        ax.set_xlim(-10, 380)
        ax.set_ylim(0, 100)
        
        if i == 0:
            ax.set_ylabel("Loan-to-Value (LTV) %")
            ax.legend(loc='upper right')

    plt.tight_layout()
    plt.savefig("../assets/ou_ltv_stress.png", dpi=300, bbox_inches='tight')
    print("Saved ou_ltv_stress.png with LaTeX styling.")

if __name__ == "__main__":
    generate_ltv_charts()
