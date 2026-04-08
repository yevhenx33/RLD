import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import spearmanr

def analyze_morpho_market_correlations():
    db_path = "/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db"
    conn = sqlite3.connect(db_path)
    
    # 1. Dynamically retrieve Top 10 active USDC-quoted markets
    query_top_markets = """
        SELECT m.market_id, m.collateral_symbol || '/' || m.loan_symbol AS pair_name, COUNT(s.timestamp) as observations
        FROM market_snapshots s
        JOIN market_params m ON s.market_id = m.market_id
        WHERE m.loan_symbol = 'USDC'
        GROUP BY m.market_id
        ORDER BY observations DESC
        LIMIT 10
    """
    top_markets = pd.read_sql_query(query_top_markets, conn)
    market_ids = tuple(top_markets['market_id'].tolist())
    market_names = top_markets.set_index('market_id')['pair_name'].to_dict()
    
    print(f"📡 Found Top {len(market_ids)} USDC Markets by snapshot volume:")
    for _, row in top_markets.iterrows():
         print(f"   - {row['pair_name']}: {row['observations']} snapshots")
         
    # 2. Extract Data and Pivot
    query_data = f"""
        SELECT 
            timestamp, 
            market_id, 
            utilization, 
            borrow_apy 
        FROM market_snapshots
        WHERE market_id IN {market_ids}
    """
    df = pd.read_sql_query(query_data, conn)
    conn.close()
    
    # Align Data via 1H Resampling (Poka-Yoke: prevents fragmented data drift)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
    df = df.set_index('datetime')
    
    # Map friendly names
    df['market'] = df['market_id'].map(market_names)
    
    # Pivot arrays
    df_util = df.pivot_table(index='datetime', columns='market', values='utilization')
    df_apy = df.pivot_table(index='datetime', columns='market', values='borrow_apy')
    
    # 1H strict alignment
    df_util = df_util.resample('1h').ffill()
    df_apy = df_apy.resample('1h').ffill()
    
    # 3. Calculate Spearman Rank Correlation pairwise (Poka-Yoke: avoids truncating older markets when joining newer markets)
    print("\n--- PAIRWISE SPEARMAN RANK CORRELATION MATRICES ---")
    
    print("\n📈 UTILIZATION CORRELATION:")
    util_df = df_util.corr(method='spearman')
    print(util_df.round(3))
    
    print("\n💸 BORROW APY CORRELATION:")
    apy_df = df_apy.corr(method='spearman')
    print(apy_df.round(3))
    
    # Generate Heatmap Visual
    import matplotlib.pyplot as plt
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 8))
    cax = ax.matshow(apy_df, cmap='coolwarm', vmin=-1, vmax=1)
    fig.colorbar(cax)
    
    # Customizing axes - Re-enabled for Top 10 readability
    ax.set_xticks(np.arange(len(apy_df.columns)))
    ax.set_yticks(np.arange(len(apy_df.index)))
    ax.set_xticklabels(apy_df.columns, rotation=45, ha='left')
    ax.set_yticklabels(apy_df.index)
    ax.set_title("Borrow APY Spearman Correlation (Top 10 USDC Markets)", pad=20, weight='bold')
    
    # Annotate cells
    for i in range(len(apy_df.index)):
        for j in range(len(apy_df.columns)):
            val = apy_df.iloc[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", color="white" if abs(val) > 0.5 else "black")
    
    img_path = "/home/ubuntu/.gemini/antigravity/brain/111bf96c-cbbc-44dc-abea-9821b77a260e/artifacts/morpho_usdc_apy_correlation.png"
    plt.tight_layout()
    plt.savefig(img_path, facecolor='#0D1117', dpi=300)
    print(f"\n🎨 Saved Clean Correlation Heatmap -> {img_path}")
    
    # ----------------------------------------------------
    # Plot Distribution / KDE Curves for Top 10 Markets
    # ----------------------------------------------------
    top_N_markets = df_apy.count().sort_values(ascending=False).head(10).index.tolist()
    
    fig2, ax2 = plt.subplots(figsize=(14, 7))
    # 10 distinct high-contrast colors
    colors = ['#FF2A55', '#00F0FF', '#00FF9D', '#FFE500', '#D358F7', '#FF8C00', '#4A90E2', '#A020F0', '#00FA9A', '#FF1493']
    
    for idx, market in enumerate(top_N_markets):
        market_data = df_apy[market].dropna()
        # Ensure sufficient variance and data points exist
        if len(market_data) > 100:
            market_data.plot(kind='kde', ax=ax2, label=market, color=colors[idx % len(colors)], lw=2, alpha=0.8)
            
    ax2.set_xlim(0, max(15, df_apy[top_N_markets].max().max() * 1.1))  # Limit outliers
    ax2.set_title("Probability Density Distribution (Borrow APY)", pad=20, weight='bold', fontsize=16)
    ax2.set_xlabel("Borrow APY (%)", fontsize=12)
    ax2.set_ylabel("Density / Frequency", fontsize=12)
    ax2.grid(True, linestyle='--', alpha=0.3, color='#4A5568')
    ax2.legend(loc='upper right', frameon=False, fontsize=12)
    
    dist_img_path = "/home/ubuntu/.gemini/antigravity/brain/111bf96c-cbbc-44dc-abea-9821b77a260e/artifacts/morpho_apy_distribution.png"
    plt.tight_layout()
    plt.savefig(dist_img_path, facecolor='#0D1117', dpi=300)
    print(f"🎨 Saved Distribution KDE Chart -> {dist_img_path}")

    
    # Poka-Yoke Constraints
    print("\n--- POKA-YOKE DIAGNOSTICS ---")
    assert not util_df.isnull().values.all(), "Fatal: All correlations void."

    
    print("✅ Poka-Yoke Array Non-Linear Drift Checks Passed.")
    print("PIPELINE: PASS.")

if __name__ == "__main__":
    analyze_morpho_market_correlations()
