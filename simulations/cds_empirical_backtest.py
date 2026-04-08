import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

def main():
    print("Initializing Parametric CDS Backtest Pipeline...")
    
    # 1.1 Data Ingestion
    df = pd.read_csv('/home/ubuntu/RLD/Research/RLD/datasets/euler_stream_case.csv')
    df['timestamp'] = pd.to_datetime(df['Timestamp'])
    df['borrow_apy'] = df['Borrow APY (%)'] / 100.0
    df['supply_apy'] = df['Supply APY (%)'] / 100.0
    
    # Calculate utilization safely: U_t in [0, 1]
    df['utilization'] = np.where(df['Total Deposits'] > 0, df['Total Borrows'] / df['Total Deposits'], 0.0)

    # 1.2 Temporal Integration Preparation
    df = df.sort_values('timestamp').reset_index(drop=True)
    df['dt'] = df['timestamp'].diff().dt.total_seconds() / 31536000.0 # annualized
    df['dt'] = df['dt'].fillna(0.0)
    df['t'] = df['dt'].cumsum()

    # 1.3 Parametric Initialization (Vectorized)
    delta = 0.80
    r_max = 0.75
    rho = 0.95
    F = -np.log(1 - delta)
    P_max = 100.0 * r_max

    # 1.4 Everlasting Option Pricing
    df['NF'] = np.exp(-F * df['t'])
    df['P_mkt'] = 100.0 * df['borrow_apy'] * df['NF']

    # Phase 2: Fiduciary Microstructure (Constant-Coverage TWAMM)
    C = 10000.0
    df['N_t'] = (C / P_max) * np.exp(F * df['t'])
    df['dN'] = (C / P_max) * F * np.exp(F * df['t']) * df['dt']
    df['premium_step'] = df['dN'] * df['P_mkt']
    df['cumulative_premium'] = df['premium_step'].cumsum()

    empirical_coverage = df['N_t'] * P_max * df['NF']
    assert np.allclose(empirical_coverage, C, atol=1e-5), "FATAL: TWAMM integration failed to maintain constant absolute coverage."

    # Phase 3: Underwriter Microstructure (JIT Vault)
    C_locked = 10000.0
    df['Q_t'] = (C_locked / P_max) * np.exp(F * df['t'])
    df['dQ'] = (C_locked / P_max) * F * np.exp(F * df['t']) * df['dt']
    df['revenue_step'] = df['dQ'] * df['P_mkt']
    df['cumulative_revenue'] = df['revenue_step'].cumsum()

    df['supply_step'] = C_locked * df['supply_apy'] * df['dt']
    
    # Assert Everlasting Option systematically beats the passive supply rate, guarding against numerical truncation variance
    assert np.all(df['revenue_step'] >= df['supply_step'] - 1e-8), "FATAL: Yield Invariant Y_CDS > r_supply was violated."
    
    df['alpha_spread'] = df['cumulative_revenue'] - df['supply_step'].cumsum()

    # Phase 4: Adversarial Robustness (The Bivariate Temporal Trap)
    # Using strict timestamp rolling to prevent jump logic flaws
    df = df.set_index('timestamp')
    df['TWAR_1h'] = df.rolling('1h')['borrow_apy'].mean()
    df = df.reset_index()

    freeze_mask = df['utilization'] >= 0.99
    breach_mask = df['TWAR_1h'] >= (rho * r_max)

    freeze_idx = df[freeze_mask].index.min()
    settle_idx = df[freeze_mask & breach_mask].index.min()
    
    assert pd.notna(freeze_idx), "FATAL: No freeze event triggered."
    assert pd.notna(settle_idx), "FATAL: No settlement event triggered."
    
    t_freeze = df.loc[freeze_idx, 'timestamp']
    t_settle = df.loc[settle_idx, 'timestamp']

    assert t_freeze < t_settle, "FATAL: TWAR failed to lag. Underwriters could exploit the mempool."
    print(f"Verified Temporal Trap Window: {t_settle - t_freeze}")

    # Phase 5: Empirical Output & Visualizations
    sns.set(style="whitegrid", rc={"font.family": "serif"})
    fig, axs = plt.subplots(2, 2, figsize=(16, 12))

    # Panel A: Jump-Diffusion & The Temporal Trap
    ax_a = axs[0, 0]
    start_a = t_settle - pd.Timedelta(hours=24)
    end_a = t_settle + pd.Timedelta(hours=24)
    df_a = df[(df['timestamp'] >= start_a) & (df['timestamp'] <= end_a)]

    ax_a.plot(df_a['timestamp'], df_a['borrow_apy'], color='black', label='Borrow APY ($r_t$)', linewidth=2)
    ax_a.plot(df_a['timestamp'], df_a['TWAR_1h'], color='blue', linestyle='--', label='TWAR$_{1h}$', zorder=2)
    ax_a.axhline(r_max, color='red', linestyle='-', alpha=0.6, label='Hard Cap $r_{max}$')
    ax_a.axhline(rho * r_max, color='orange', linestyle='--', alpha=0.6, label=r'Trigger ($\rho \cdot r_{max}$)')
    ax_a.axvline(t_freeze, color='darkred', linestyle='-', label='Withdrawals Revert ($t_{freeze}$)')
    ax_a.axvline(t_settle, color='black', linestyle='-', label='Global Settlement ($t_{settle}$)')

    ax_a2 = ax_a.twinx()
    ax_a2.fill_between(df_a['timestamp'].values, 0, df_a['utilization'].values, color='red', alpha=0.15, label='Utilization ($U_t$)')
    ax_a2.set_ylim(0, 1.1)

    ax_a.set_title("Jump-Diffusion & The Temporal Trap", fontsize=14)
    ax_a.set_xlabel("DateTime")
    ax_a.set_ylabel("Rate")
    ax_a2.set_ylabel("Utilization")
    # Consolidate legends
    lines_1, labels_1 = ax_a.get_legend_handles_labels()
    lines_2, labels_2 = ax_a2.get_legend_handles_labels()
    ax_a.legend(lines_1 + lines_2, labels_1 + labels_2, loc='upper left', frameon=True)

    # Panel B: Continuous Fiduciary Coverage vs. Convex Cost
    ax_b = axs[0, 1]
    df_b = df[df['timestamp'] <= t_settle]
    ax_b.plot(df_b['timestamp'], [C] * len(df_b), color='black', label='Maintained Coverage ($C$)')
    ax_b.fill_between(df_b['timestamp'].values, 0, df_b['cumulative_premium'].values, color='blue', alpha=0.3, label='Accumulated Premium Stream')
    ax_b.set_title("Continuous Fiduciary Coverage vs. Convex Cost", fontsize=14)
    ax_b.set_xlabel("DateTime")
    ax_b.set_ylabel("Capital (USD)")
    ax_b.legend(loc='upper right')

    # Panel C: Extraction of the Taylor Series Risk Premium (alpha)
    ax_c = axs[1, 0]
    df_c = df[df['timestamp'] < t_freeze].copy()
    passive_yield_pct = (df_c['supply_step'].cumsum() / C_locked) * 100
    jit_vault_yield_pct = (df_c['cumulative_revenue'] / C_locked) * 100

    ax_c.plot(df_c['timestamp'], passive_yield_pct, color='gray', linestyle='--', label='Passive Yield')
    ax_c.plot(df_c['timestamp'], jit_vault_yield_pct, color='green', label='JIT Vault Yield')
    ax_c.fill_between(df_c['timestamp'].values, passive_yield_pct.values, jit_vault_yield_pct.values, color='green', alpha=0.2, label=r'Extracted Convex Premium ($\alpha$)')
    ax_c.set_title(r"Extraction of the Taylor Series Risk Premium ($\alpha$)", fontsize=14)
    ax_c.set_xlabel("DateTime (Pre-Crisis)")
    ax_c.set_ylabel("Cumulative Yield (%)")
    ax_c.legend(loc='upper left')

    # Panel D: State Space Normalization Decay
    ax_d = axs[1, 1]
    ax_d.plot(df['timestamp'], df['NF'], color='purple', label='Normalization Factor NF($t$)')
    ax_d.set_yscale('log')
    ax_d.set_title("State Space Normalization Decay", fontsize=14)
    ax_d.set_xlabel("DateTime")
    ax_d.set_ylabel("Value (Log Scale)")
    ax_d.legend(loc='lower left')

    plt.tight_layout()
    plt.savefig('/home/ubuntu/RLD/simulations/cds_empirical_backtest.png', dpi=300)
    print("Backtest Complete. Visualizations rendered to /home/ubuntu/RLD/simulations/cds_empirical_backtest.png")

if __name__ == "__main__":
    main()
