import matplotlib.pyplot as plt
import numpy as np

# Import regimes from our simulation script
from ou_simulation_v6 import regimes

def generate_grid_charts():
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
        "figure.figsize": (15, 8)
    })

    fig, axs = plt.subplots(2, 3)

    # Indices: 1: Strong Bull, 2: Strong Bear, 3: Chaotic Vol
    regime_indices = [1, 2, 3]
    titles_top = ["Rising (10 → 25%)", "Falling (10 → 2%)", "Volatile (Mean 10%)"]
    titles_bot = ["Yield Distribution (Rising)", "Yield Distribution (Falling)", "Yield Distribution (Volatile)"]
    
    # Matching colors from user's image
    colors_paths = ['#228B22', '#E74C3C', '#2980B9'] # Forest Green, Alizarin Red, Belize Blue
    colors_box = ['#5cb85c', '#ff6666', '#6666ff']
    
    for i, idx in enumerate(regime_indices):
        r = regimes[idx]
        
        # TOP ROW: Paths
        ax_top = axs[0, i]
        paths = r['paths']
        # plot 25 paths to avoid excessive density while maintaining chaotic look
        for p in range(25):
            ax_top.plot(paths[p, :] * 100, color=colors_paths[i], alpha=0.3, linewidth=0.8)
        
        ax_top.axhline(10.0, color='black', linestyle='--')
        ax_top.set_title(titles_top[i], fontsize=11)
        
        # Set specific y-limits to match the reference visualization exactly
        if i == 0 or i == 2:
            ax_top.set_ylim(0, 45)
        else:
            ax_top.set_ylim(0, 20)
            
        ax_top.set_ylabel("Rate (%)")
        
        # BOTTOM ROW: Box plots
        ax_bot = axs[1, i]
        vanilla = r['vanilla_array'] * 100
        baseline = r['baseline_array'] * 100
        
        box_data = [vanilla, baseline]
        box_labels = ["Floating", "Syn Bond"]
        
        # Create boxplot
        bplot = ax_bot.boxplot(box_data, labels=box_labels, patch_artist=True, widths=0.4, medianprops=dict(color="black", linewidth=1.5))
        
        for patch in bplot['boxes']:
            patch.set_facecolor(colors_box[i])
            patch.set_alpha(0.8)
            
        ax_bot.axhline(10.0, color='black', linestyle='--')
        ax_bot.set_title(titles_bot[i], fontsize=11)
        ax_bot.set_ylabel("Annual Yield (%)")

    plt.tight_layout(pad=2.0)
    plt.savefig("../assets/monte_carlo_regimes.png", dpi=300, bbox_inches='tight')
    print("Saved monte_carlo_regimes.png with LaTeX styling.")

if __name__ == "__main__":
    generate_grid_charts()
