#!/usr/bin/env python3
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

plt.rcParams.update({
    'font.family': 'serif',
    'font.serif': ['Computer Modern Roman', 'CMU Serif', 'DejaVu Serif', 'Times New Roman'],
    'font.size': 9,
    'axes.labelsize': 10,
    'axes.titlesize': 11,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 7.5,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'axes.linewidth': 0.6,
    'grid.linewidth': 0.4,
})

u = np.linspace(0, 1, 500)
target_u = 0.90
r_base = 0.04
r_max = 1.00

# Standard kinked IRM representing Morpho's Adaptive Curve behavior at equilibrium
rt = np.piecewise(u, 
    [u <= target_u, u > target_u], 
    [lambda x: r_base * (x / target_u), 
     lambda x: r_base + (r_max - r_base) * ((x - target_u) / (1 - target_u))]
)

fig, ax = plt.subplots(figsize=(6, 3.5))

ax.plot(u * 100, rt * 100, color='#e74c3c', linewidth=2.5, label='Morpho Borrow APY')

# Emphasize the "Strike Price" (Kink)
ax.axvline(target_u * 100, color='#34495e', linestyle='--', linewidth=1.2, alpha=0.8, label=r'Target Utilization ($\delta = 90\%$)')

# Fill the option payoff area
ax.fill_between(u * 100, r_base * 100, rt * 100, where=(u >= target_u), color='#e74c3c', alpha=0.15, label='Option-like Convexity / "Moneyness"')

# Fill the baseline flat rate
ax.fill_between(u * 100, 0, rt * 100, where=(u <= target_u), color='#95a5a6', alpha=0.1, label='Base Yield (Out of the Money)')

# Annotations pointing out the Option attributes
ax.annotate(
    'Strike Price ($K$)',
    xy=(target_u * 100, 20),
    xytext=(target_u * 100 - 15, 40),
    fontsize=8, ha='right', va='center',
    arrowprops=dict(arrowstyle='->', color='#2c3e50', lw=1.0)
)

ax.annotate(
    'Bounded Payout Cap',
    xy=(100, 100),
    xytext=(100 - 20, 100),
    fontsize=8, ha='right', va='center',
    arrowprops=dict(arrowstyle='->', color='#e74c3c', lw=1.0)
)

ax.set_title('DeFi Lending Rates mapped as Bounded Call Options', fontweight='bold', pad=10)
ax.set_xlabel('Pool Utilization ($U_t$) (%)')
ax.set_ylabel('Borrow Rate ($r_t$) (%)')
ax.set_xlim(0, 100)
ax.set_ylim(0, 105)

ax.grid(True, linestyle=':', alpha=0.4, color='gray')
ax.legend(loc='upper left', framealpha=0.95, edgecolor='#cccccc')

fig.tight_layout()
fig.savefig('../figures/irm_option_convexity.png', bbox_inches='tight', facecolor='white', edgecolor='none')
print("Successfully generated IRM Option curve.")
