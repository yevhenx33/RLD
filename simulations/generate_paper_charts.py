"""
Generates the specific mathematical visualizations required by the academic paper.
"""
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
    'lines.linewidth': 1.0,
})

OUTPUT_DIR = '/home/ubuntu/.gemini/antigravity/brain/caccad6e-ddc4-4180-9149-eb50f8230c58/artifacts'

# ==============================================================================
# Plot 1: Yield Convexity (Theorem 4.1)
# ==============================================================================
u = np.linspace(0, 1, 1000)

target_u = 0.80
r_base = 0.04
r_max = 1.00
reserve_factor = 0.10

# Standard kinked IRM
rt = np.piecewise(u, 
    [u <= target_u, u > target_u], 
    [lambda x: r_base * (x / target_u), 
     lambda x: r_base + (r_max - r_base) * ((x - target_u) / (1 - target_u))]
)

r_supply = u * rt * (1 - reserve_factor)

F = -np.log(1 - target_u)  # ~1.6094
y_cds = F * rt

plt.figure(figsize=(7.0, 3.5))
plt.plot(u, y_cds * 100, label=r'CDS Premium Yield ($Y_{CDS} = F \cdot r_t$)', color='#2c3e50', linewidth=1.5)
plt.plot(u, r_supply * 100, label=r'Passive Supply Yield ($r_{supply}$)', color='#e74c3c', linewidth=1.2, linestyle='--')

plt.fill_between(u, r_supply*100, y_cds*100, color='#3498db', alpha=0.15, label=r'Risk Premium ($\alpha$)')

plt.axvline(x=target_u, color='gray', linestyle=':', label=r'Target Utilization ($\delta = 0.8$)')

plt.title('Theorem: Convex Risk Premium Pricing', fontweight='bold', pad=10)
plt.xlabel('Pool Utilization ($U_t$)')
plt.ylabel('Annualized Yield (%)')
plt.xlim(0, 1)
plt.ylim(0, max(y_cds*100) * 1.05)
plt.legend(loc='upper left')
plt.grid(True, linestyle=':', alpha=0.4, color='gray')
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/yield_convexity.png", dpi=300)
plt.close()

# ==============================================================================
# Plot 2: State Decay & Liability
# ==============================================================================
t = np.linspace(0, 1, 365) # 1 year
capital = 100000.0 # $100k
initial_tokens = capital / 100.0

nf = np.exp(-F * t)
tokens_active = initial_tokens * nf
max_liability = tokens_active * 100.0 # At r_max = 100%

plt.figure(figsize=(7.0, 3.5))

plt.plot(t * 365, max_liability, label=r'Max Liability ($L_t = C \cdot e^{-F \cdot t}$)', color='#d35400', linewidth=1.5)
plt.axhline(y=capital, color='#27ae60', linestyle='--', linewidth=1.2, label='Locked Escrow (Absolute Solvency Bound)')

plt.fill_between(t * 365, max_liability, capital, color='#2ecc71', alpha=0.2, label='Excess Collateral (Dead Capital)')

plt.title('Everlasting Option: Continuous State Decay vs Escrow', fontweight='bold', pad=10)
plt.xlabel('Days Elapsed')
plt.ylabel('Capital Obligation (USD)')
plt.xlim(0, 365)
plt.ylim(0, capital * 1.1)

# Format y axis as currency
plt.gca().yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'${x/1000:,.0f}k'))

plt.legend(loc='center right')
plt.grid(True, linestyle=':', alpha=0.4, color='gray')
plt.tight_layout()
plt.savefig(f"{OUTPUT_DIR}/liability_decay.png", dpi=300)
plt.close()

print(f"Generated academic plots at {OUTPUT_DIR}")
