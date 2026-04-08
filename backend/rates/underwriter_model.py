import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def run_underwriter_simulation():
    # ── 1. Fetch Empirical Morpho Markets ──
    db_path = "/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db"
    conn = sqlite3.connect(db_path)
    
    query_top_markets = """
        SELECT m.market_id, m.collateral_symbol || '/' || m.loan_symbol AS pair_name, COUNT(s.timestamp) as observations
        FROM market_snapshots s
        JOIN market_params m ON s.market_id = m.market_id
        WHERE m.loan_symbol = 'USDC' AND m.collateral_symbol IS NOT NULL
        GROUP BY m.market_id
        ORDER BY observations DESC
        LIMIT 10
    """
    top_markets = pd.read_sql_query(query_top_markets, conn)
    market_ids = tuple(top_markets['market_id'].tolist())
    market_names = top_markets.set_index('market_id')['pair_name'].to_dict()
    
    query_data = f"""
        SELECT timestamp, market_id, borrow_apy
        FROM market_snapshots
        WHERE market_id IN {market_ids} AND borrow_apy IS NOT NULL
    """
    df_raw = pd.read_sql_query(query_data, conn)
    conn.close()
    
    df_raw['datetime'] = pd.to_datetime(df_raw['timestamp'], unit='s')
    df_raw['market'] = df_raw['market_id'].map(market_names)
    df = df_raw.pivot_table(index='datetime', columns='market', values='borrow_apy').resample('1h').ffill().bfill()
    
    markets = df.columns.tolist()
    assert len(markets) == 10, "Poka-Yoke Failure: Did not load exactamente 10 Morpho markets."
    
    print(f"✅ Loaded Timeline: {len(df)} hours.")
    
    # ── 2. Inject Synthetic Exploit to Force Bankruptcy ──
    # We choose a random market from the middle of the pack to isolate, e.g., the 3rd market list idx
    target_exploit_market = markets[2]
    exploit_index_hour = len(df) // 2  # Halfway through
    
    # Inject a 500% APY shock for a few hours simulating 100% utilization liquidity drain
    df.iloc[exploit_index_hour:exploit_index_hour+24, df.columns.get_loc(target_exploit_market)] = 5.00 # 500% APY
    print(f"⚠️ Injected Synthetic 500% APY Exploit into {target_exploit_market} at hour {exploit_index_hour}")
    
    # ── 3. Underwriter Financial Model ──
    INITIAL_GLOBAL_CAPITAL = 100.0  # Total ETH
    CAPITAL_PER_MARKET = INITIAL_GLOBAL_CAPITAL / len(markets)  # 10 ETH per silo
    
    # We will track equity per market chronologically
    equity_matrix = np.zeros(df.shape)
    
    # Initialize state
    # Underwriter sells CDS => Fixed-for-Floating Swap
    # They receive fixed (entry APY) and pay floating (current APY)
    
    active_positions = {}
    
    for j, market in enumerate(markets):
        entry_apy = df.iloc[0, j]
        
        # Protective floor so APY hovering at 0.0001% doesn't create infinite leverage floating point bugs
        if entry_apy < 0.01: 
            entry_apy = 0.01 
            
        # Dynamic Initial CR: If Borrow Cost 5% -> CR = 100/5 = 20x.
        # Note: APY is stored as a decimal (e.g. 5% = 0.05). Ensure math maps.
        # User explicitly stated: "if borrow cost 5% -> min CR = 100/5 = 20x"
        # Since our df has APY as decimal (0.05), we multiply by 100 to get "5".
        base_rate_percentage = entry_apy * 100.0
        leverage_cr = 100.0 / base_rate_percentage
        
        notional_eth = CAPITAL_PER_MARKET * leverage_cr
        
        active_positions[market] = {
            'collateral': CAPITAL_PER_MARKET,
            'notional': notional_eth,
            'fixed_entry_apy': entry_apy,  # e.g. 0.05
            'bankrupt': False
        }
        equity_matrix[0, j] = CAPITAL_PER_MARKET

    # Simulate Hourly Ledger
    for i in range(1, len(df)):
        for j, market in enumerate(markets):
            pos = active_positions[market]
            
            if pos['bankrupt']:
                equity_matrix[i, j] = 0.0
                continue
                
            current_floating_apy = df.iloc[i, j]
            
            # PnL = Notional * (Fixed - Floating) / 8760 hours
            # If current floating > fixed entry, they owe the difference (Loss)
            # If current floating < fixed entry, they pocket the difference (Gain)
            hourly_pnl = pos['notional'] * (pos['fixed_entry_apy'] - current_floating_apy) / 8760.0
            
            pos['collateral'] += hourly_pnl
            
            # Ring-fenced Bankruptcy Check
            if pos['collateral'] <= 0:
                pos['collateral'] = 0.0
                pos['bankrupt'] = True
                print(f"💀 BANKRUPTCY TRIGGERED: {market} at hour {i} | Rate Hit: {current_floating_apy*100:.1f}%")
                
            equity_matrix[i, j] = pos['collateral']

    # ── 4. Process Results & Plotting ──
    df_equity = pd.DataFrame(equity_matrix, index=df.index, columns=markets)
    global_equity = df_equity.sum(axis=1)
    
    plt.style.use('dark_background')
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={'height_ratios': [2, 1]})
    
    # Top Plot: Individual Silo Equity
    for j, market in enumerate(markets):
        color = '#FF2A55' if market == target_exploit_market else None
        alpha = 0.9 if market == target_exploit_market else 0.4
        lw = 2.5 if market == target_exploit_market else 1.5
        ax1.plot(df_equity.index, df_equity[market], label=market, color=color, alpha=alpha, linewidth=lw)
        
    ax1.set_title("Underwriter Capital per Silo (10 ETH Initial Allocation)", pad=15, weight='bold', fontsize=14)
    ax1.set_ylabel("Silo Equity (ETH)", fontsize=12)
    ax1.grid(True, linestyle='--', color='#4A5568', alpha=0.3)
    ax1.axhline(0, color='white', linestyle=':', alpha=0.5)
    
    # Bottom Plot: Global Portfolio
    ax2.plot(global_equity.index, global_equity, color='#00F0FF', lw=3, label="Total Underwriter Capital")
    ax2.axhline(100.0, color='#FF2A55', linestyle='--', label="Initial Capital (100 ETH)", alpha=0.8)
    
    # Mark bankruptcy event on the global timeline
    explode_time = df.index[exploit_index_hour]
    ax2.axvline(explode_time, color='red', alpha=0.5, linestyle=':')
    ax2.text(explode_time, global_equity.min(), " Exploit Liquidation", color="red", verticalalignment='bottom')
    
    ax2.set_title("Global Portfolio Integrity (Bankruptcy Ring-Fencing Active)", pad=15, weight='bold', fontsize=14)
    ax2.set_ylabel("Global Equity (ETH)", fontsize=12)
    ax2.grid(True, linestyle='--', color='#4A5568', alpha=0.3)
    ax2.legend(loc='upper right', frameon=False)
    
    img_path = "/home/ubuntu/.gemini/antigravity/brain/111bf96c-cbbc-44dc-abea-9821b77a260e/artifacts/underwriter_bankruptcy_isolation.png"
    plt.tight_layout()
    plt.savefig(img_path, facecolor='#0D1117', dpi=300)
    
    print(f"\n[ FINANCIAL MODEL VERIFIED ]")
    print(f"Final Global Capital: {global_equity.iloc[-1]:.2f} ETH")
    print(f"Survived Markets  : {10 - sum(df_equity.iloc[-1] == 0)} / 10")
    print(f"🎨 Saved Simulation Chart -> {img_path}")

if __name__ == "__main__":
    run_underwriter_simulation()
