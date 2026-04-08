import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def execute_cross_correlation_individual():
    # ── 1. Fetch AAVE Data ──
    aave_db = "/home/ubuntu/RLD/backend/clean_rates_readonly.db"
    conn_aave = sqlite3.connect(aave_db)
    df_aave = pd.read_sql_query("SELECT timestamp, usdc_rate as Aave_V3_USDC FROM hourly_stats WHERE usdc_rate IS NOT NULL", conn_aave)
    conn_aave.close()
    
    df_aave['datetime'] = pd.to_datetime(df_aave['timestamp'], unit='s')
    df_aave = df_aave.set_index('datetime').resample('1h').ffill()
    df_aave.drop(columns=['timestamp'], inplace=True)
    
    # ── 2. Fetch Morpho USDC Markets ──
    morpho_db = "/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db"
    conn_morpho = sqlite3.connect(morpho_db)
    
    query_top_markets = """
        SELECT m.market_id, m.collateral_symbol || '/' || m.loan_symbol AS pair_name, COUNT(s.timestamp) as observations
        FROM market_snapshots s
        JOIN market_params m ON s.market_id = m.market_id
        WHERE m.loan_symbol = 'USDC'
        GROUP BY m.market_id
        ORDER BY observations DESC
        LIMIT 10
    """
    top_markets = pd.read_sql_query(query_top_markets, conn_morpho)
    market_ids = tuple(top_markets['market_id'].tolist())
    market_names = top_markets.set_index('market_id')['pair_name'].to_dict()
    
    query_data = f"""
        SELECT 
            timestamp, 
            market_id, 
            borrow_apy
        FROM market_snapshots
        WHERE market_id IN {market_ids} AND borrow_apy IS NOT NULL
    """
    df_morpho_raw = pd.read_sql_query(query_data, conn_morpho)
    conn_morpho.close()
    
    # ── 3. Pivot Morpho Data ──
    df_morpho_raw['datetime'] = pd.to_datetime(df_morpho_raw['timestamp'], unit='s')
    df_morpho_raw['market'] = df_morpho_raw['market_id'].map(market_names)
    df_morpho = df_morpho_raw.pivot_table(index='datetime', columns='market', values='borrow_apy')
    df_morpho = df_morpho.resample('1h').ffill()
    
    # ── 4. Align Both Domains (11 Series) & Calculate Spearman Correlation ──
    df_aligned = pd.concat([df_aave, df_morpho], axis=1)
    
    # Avoid dropping everything if some exotic markets started later, just use Pairwise corr
    corr_matrix = df_aligned.corr(method='spearman')
    
    print("\n[ CROSS-PROTOCOL LIQUIDITY CONTAGION (AAVE vs MORPHO 10 MARKETS) ]")
    print(f"Aave V3 specific correlations against Morpho pools:")
    print("-" * 65)
    
    aave_corrs = corr_matrix['Aave_V3_USDC'].drop('Aave_V3_USDC').sort_values(ascending=False)
    for market, val in aave_corrs.items():
        if pd.isna(val): continue
        status = "✅ Coupled" if val > 0.8 else "⚠️ Weak/Decoupled"
        print(f"   - Aave vs {market:<15}: {val:>6.3f} ({status})")
        
    # ── 5. Generate Heatmap Visuals ──
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 10))
    cax = ax.matshow(corr_matrix, cmap='coolwarm', vmin=-1, vmax=1)
    fig.colorbar(cax)
    
    ax.set_xticks(np.arange(len(corr_matrix.columns)))
    ax.set_yticks(np.arange(len(corr_matrix.index)))
    ax.set_xticklabels(corr_matrix.columns, rotation=45, ha='left')
    ax.set_yticklabels(corr_matrix.index)
    
    # Bold the Aave Label explicitly
    aave_index = list(corr_matrix.columns).index('Aave_V3_USDC')
    ax.get_xticklabels()[aave_index].set_weight("bold")
    ax.get_xticklabels()[aave_index].set_color("#FF2A55")
    ax.get_yticklabels()[aave_index].set_weight("bold")
    ax.get_yticklabels()[aave_index].set_color("#FF2A55")

    ax.set_title("Borrow APY Cross-Correlation: Aave V3 vs Top 10 Morpho Markets", pad=20, weight='bold')
    
    # Annotate cells
    for i in range(len(corr_matrix.index)):
        for j in range(len(corr_matrix.columns)):
            val = corr_matrix.iloc[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", color="white" if abs(val) > 0.5 else "black")
                
    dist_img_path = "/home/ubuntu/.gemini/antigravity/brain/111bf96c-cbbc-44dc-abea-9821b77a260e/artifacts/aave_morpho_individual_correlation.png"
    plt.tight_layout()
    plt.savefig(dist_img_path, facecolor='#0D1117', dpi=300)
    print(f"\n🎨 Saved 11x11 Cross-Protocol Heatmap -> {dist_img_path}")
    print("PIPELINE: PASS.")

if __name__ == "__main__":
    execute_cross_correlation_individual()
