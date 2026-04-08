"""
Diversified Underwriter Portfolio: Regime-Separated Visualization
================================================================
Splits the 30-market portfolio into two regimes:
  1. Steady-State Markets (26): Normal IRM behavior, borrow APY < 50%
  2. Tail-Risk Amplifiers (4): Markets that hit extreme rate caps (deUSD, WETH, PAXG, sdeUSD)
  
This separation provides publication-quality visuals where steady-state
dynamics are not visually dominated by outlier jump-diffusion events.
"""
import sqlite3
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns

DB_PATH = '/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db'
OUTPUT_DIR = '/home/ubuntu/RLD/simulations'

DELTA = 0.80
F = -np.log(1 - DELTA)
C_PER_MARKET = 10_000.0
TOP_N = 30

TAIL_RISK_IDS = {
    '0xbd1ad3b9',  # deUSD
    '0x7dde86a1',  # WETH
    '0x8eaf7b29',  # PAXG
    '0x0f956344',  # sdeUSD
}

def load_top_markets(conn):
    q = '''
    SELECT mp.market_id, mp.collateral_symbol, COUNT(*) as n
    FROM market_params mp
    JOIN market_snapshots ms ON mp.market_id = ms.market_id
    WHERE mp.loan_symbol = 'USDC' AND ms.total_supply_assets > 0
      AND ms.borrow_apy > 0 AND mp.collateral_symbol IS NOT NULL
    GROUP BY mp.market_id HAVING n > 500
    ORDER BY n DESC LIMIT ?
    '''
    return conn.execute(q, (TOP_N,)).fetchall()

def load_series(conn, mid):
    q = '''SELECT timestamp, borrow_apy, supply_apy, utilization
           FROM market_snapshots WHERE market_id = ? AND total_supply_assets > 0
           ORDER BY timestamp'''
    df = pd.read_sql_query(q, conn, params=(mid,))
    df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='s', utc=True)
    df = df.set_index('timestamp').sort_index()
    for c in ['borrow_apy','supply_apy','utilization']:
        df[c] = df[c].astype(np.float64)
    return df

def compute_bt(df):
    dt = df.index.to_series().diff().dt.total_seconds().fillna(0).values / 31_536_000.0
    b = df['borrow_apy'].values
    s = df['supply_apy'].values
    cds = np.cumsum(C_PER_MARKET * F * b * dt)
    sup = np.cumsum(C_PER_MARKET * s * dt)
    ty = np.sum(dt)
    return {
        'cds_cum': cds, 'sup_cum': sup,
        'cds_total': cds[-1], 'sup_total': sup[-1],
        'alpha': cds[-1] - sup[-1], 'years': ty,
        'cds_apy': (cds[-1] / C_PER_MARKET) / ty if ty > 0 else 0,
        'sup_apy': (sup[-1] / C_PER_MARKET) / ty if ty > 0 else 0,
    }

def main():
    conn = sqlite3.connect(DB_PATH)
    markets = load_top_markets(conn)

    steady, tail = [], []
    for mid, coll, n in markets:
        df = load_series(conn, mid)
        bt = compute_bt(df)
        bt['mid'] = mid; bt['coll'] = coll; bt['ts'] = df.index
        if mid[:10] in TAIL_RISK_IDS:
            tail.append(bt)
        else:
            steady.append(bt)
    conn.close()

    # ── Visualization ─────────────────────────────────────────────────
    sns.set(style="whitegrid", rc={"font.family": "serif", "axes.facecolor": "#fafafa"})
    fig, axs = plt.subplots(2, 2, figsize=(18, 14))
    fig.suptitle("Diversified CDS Underwriter Portfolio: 30 Morpho USDC Markets", fontsize=16, fontweight='bold', y=0.98)

    # Panel A: Steady-State Scatter (26 markets, < 50% borrow APY regime)
    ax = axs[0, 0]
    ss_cds = [b['cds_apy']*100 for b in steady]
    ss_sup = [b['sup_apy']*100 for b in steady]
    ss_alp = [b['alpha'] for b in steady]
    ss_col = [b['coll'] for b in steady]

    scatter = ax.scatter(ss_sup, ss_cds, c=ss_alp, s=90, cmap='Greens', edgecolors='black', linewidths=0.5, zorder=5, vmin=0)
    for i, (sx, cy, c) in enumerate(zip(ss_sup, ss_cds, ss_col)):
        ax.annotate(c[:12], (sx, cy), fontsize=6.5, ha='left', va='bottom', xytext=(3,3), textcoords='offset points')
    max_v = max(max(ss_cds), max(ss_sup)) * 1.15
    ax.plot([0, max_v], [0, max_v], 'k--', alpha=0.3, label='Parity (Zero Alpha)')
    ax.set_xlim(0, max_v); ax.set_ylim(0, max_v)
    ax.set_title("Steady-State Markets: CDS vs. Passive Yield", fontsize=13, fontweight='bold')
    ax.set_xlabel("Passive Supply APY (%)")
    ax.set_ylabel("CDS Underwriting APY (%)")
    ax.legend(loc='upper left', fontsize=9)
    plt.colorbar(scatter, ax=ax, label=r'Extracted $\alpha$ (USD)', shrink=0.8)

    # Panel B: Per-Market Alpha Waterfall (Steady-State only, clean bars)
    ax = axs[0, 1]
    sorted_ss = sorted(steady, key=lambda x: x['alpha'], reverse=True)
    labels = [b['coll'][:14] for b in sorted_ss]
    alphas = [b['alpha'] for b in sorted_ss]
    colors = ['#27ae60' if a > 0 else '#e74c3c' for a in alphas]
    bars = ax.barh(range(len(labels)), alphas, color=colors, edgecolor='white', linewidth=0.5)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_title(r"Steady-State: Per-Market Risk Premium ($\alpha$)", fontsize=13, fontweight='bold')
    ax.set_xlabel("Cumulative Alpha (USD)")
    ax.axvline(0, color='black', linewidth=0.5)
    # Annotate dollar values
    for i, (bar, val) in enumerate(zip(bars, alphas)):
        ax.text(val + 10, i, f'${val:,.0f}', va='center', fontsize=7)

    # Panel C: Tail-Risk Amplifiers (the 4 extreme markets)
    ax = axs[1, 0]
    tail_labels = [b['coll'] for b in tail]
    tail_cds = [b['cds_apy']*100 for b in tail]
    tail_sup = [b['sup_apy']*100 for b in tail]
    x = np.arange(len(tail_labels))
    w = 0.35
    bars1 = ax.bar(x - w/2, tail_cds, w, label='CDS Underwriting APY', color='#27ae60', edgecolor='black', linewidth=0.5)
    bars2 = ax.bar(x + w/2, tail_sup, w, label='Passive Supply APY', color='#95a5a6', edgecolor='black', linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels(tail_labels, fontsize=10)
    ax.set_ylabel("APY (%)")
    ax.set_title("Tail-Risk Amplifiers: Jump-Diffusion Markets", fontsize=13, fontweight='bold')
    ax.legend(loc='upper right')
    # Annotate alpha
    for i, b in enumerate(tail):
        alpha_pct = (b['cds_apy'] - b['sup_apy']) * 100
        ax.annotate(f'+{alpha_pct:.0f}%\n(α=${b["alpha"]:,.0f})',
                    xy=(i, max(b['cds_apy'], b['sup_apy'])*100),
                    xytext=(0, 15), textcoords='offset points',
                    ha='center', fontsize=8, fontweight='bold', color='#27ae60')

    # Panel D: Portfolio Summary Statistics Table
    ax = axs[1, 1]
    ax.axis('off')

    total_capital = C_PER_MARKET * len(markets)
    ss_cds_total = sum(b['cds_total'] for b in steady)
    ss_sup_total = sum(b['sup_total'] for b in steady)
    tr_cds_total = sum(b['cds_total'] for b in tail)
    tr_sup_total = sum(b['sup_total'] for b in tail)
    all_cds = ss_cds_total + tr_cds_total
    all_sup = ss_sup_total + tr_sup_total

    table_data = [
        ['Metric', 'Steady-State\n(26 Markets)', 'Tail-Risk\n(4 Markets)', 'Portfolio\n(30 Markets)'],
        ['Capital Deployed', f'${C_PER_MARKET*len(steady):,.0f}', f'${C_PER_MARKET*len(tail):,.0f}', f'${total_capital:,.0f}'],
        ['CDS Revenue', f'${ss_cds_total:,.0f}', f'${tr_cds_total:,.0f}', f'${all_cds:,.0f}'],
        ['Supply Revenue', f'${ss_sup_total:,.0f}', f'${tr_sup_total:,.0f}', f'${all_sup:,.0f}'],
        ['Alpha Extracted', f'${ss_cds_total-ss_sup_total:,.0f}', f'${tr_cds_total-tr_sup_total:,.0f}', f'${all_cds-all_sup:,.0f}'],
        ['Alpha / Capital', f'{(ss_cds_total-ss_sup_total)/(C_PER_MARKET*len(steady))*100:.1f}%', 
         f'{(tr_cds_total-tr_sup_total)/(C_PER_MARKET*len(tail))*100:.1f}%',
         f'{(all_cds-all_sup)/total_capital*100:.1f}%'],
    ]

    table = ax.table(cellText=table_data[1:], colLabels=table_data[0],
                     loc='center', cellLoc='center')
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.0, 1.8)
    # Style header
    for j in range(4):
        table[0, j].set_facecolor('#2c3e50')
        table[0, j].set_text_props(color='white', fontweight='bold')
    # Style alpha row
    for j in range(4):
        table[4, j].set_facecolor('#d5f5e3')
        table[4, j].set_text_props(fontweight='bold')
        table[5, j].set_facecolor('#d5f5e3')
        table[5, j].set_text_props(fontweight='bold')
    ax.set_title("Portfolio Performance Summary", fontsize=13, fontweight='bold', pad=20)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = f"{OUTPUT_DIR}/cds_portfolio_regime_separated.png"
    plt.savefig(out, dpi=300, bbox_inches='tight')
    print(f"Saved to {out}")

if __name__ == "__main__":
    main()
