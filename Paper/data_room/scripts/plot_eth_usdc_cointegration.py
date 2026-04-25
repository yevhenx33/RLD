import clickhouse_connect
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def fetch_data():
    client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
    
    query_eth = """
    SELECT toDate(timestamp) as date,
           argMax(price, block_number) as eth_price
    FROM chainlink_prices
    WHERE feed='ETH / USD' AND toYear(timestamp) = 2025
    GROUP BY date
    ORDER BY date
    """
    df_eth = client.query_df(query_eth)
    
    query_usdc = """
    SELECT toDate(timestamp) as date,
           argMax(borrow_apy, inserted_at) as borrow_rate
    FROM aave_timeseries
    WHERE protocol='AAVE_MARKET' AND symbol='USDC' AND toYear(timestamp) = 2025
    GROUP BY date
    ORDER BY date
    """
    df_usdc = client.query_df(query_usdc)
    
    df = pd.merge(df_eth, df_usdc, on='date', how='inner')
    df['date'] = pd.to_datetime(df['date'])
    df['borrow_rate'] = df['borrow_rate'] * 100  # Convert to percentage
    return df

def generate_charts():
    df = fetch_data()
    
    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["DejaVu Serif", "Computer Modern Roman", "Times New Roman"],
        "mathtext.fontset": "cm",
        "axes.labelsize": 9,
        "font.size": 9,
        "legend.fontsize": 8,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "axes.grid": True,
        "grid.linestyle": ":",
        "grid.alpha": 0.6,
        "figure.figsize": (16, 12)
    })

    fig = plt.figure()
    gs = fig.add_gridspec(3, 2, height_ratios=[1, 1, 1], hspace=0.4, wspace=0.25)
    
    ax_top = fig.add_subplot(gs[0, :])
    ax_q1 = fig.add_subplot(gs[1, 0])
    ax_q2 = fig.add_subplot(gs[1, 1])
    ax_q3 = fig.add_subplot(gs[2, 0])
    ax_q4 = fig.add_subplot(gs[2, 1])
    
    def plot_panel(ax, sub_df, title_prefix):
        if len(sub_df) < 2:
            return
        corr = sub_df['eth_price'].corr(sub_df['borrow_rate'])
        
        color_price = '#2980B9' # Belize Blue
        color_rate = '#E74C3C'  # Alizarin Red
        
        ax.plot(sub_df['date'], sub_df['eth_price'], color=color_price, linewidth=1.5)
        ax.set_ylabel('ETH Price ($)', color=color_price)
        ax.tick_params(axis='y', labelcolor=color_price)
        
        ax2 = ax.twinx()
        ax2.grid(False) # Turn off grid for the secondary axis to prevent overlapping
        ax2.plot(sub_df['date'], sub_df['borrow_rate'], color=color_rate, linestyle='--', linewidth=1.5)
        ax2.set_ylabel('Borrow Rate (%)', color=color_rate)
        ax2.tick_params(axis='y', labelcolor=color_rate)
        
        # Determine title structure based on panel
        if "Full" in title_prefix:
            ax.set_title(f"{title_prefix} (Correlation: {corr:.2f})", fontsize=11)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=0, ha="center")
        else:
            ax.set_title(f"{title_prefix} (Corr: {corr:.2f})", fontsize=11)
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b'))
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
        
    # Top panel
    plot_panel(ax_top, df, "Full Year 2025 Analysis")
    
    # Quarters
    q1_df = df[(df['date'] >= '2025-01-01') & (df['date'] < '2025-04-01')]
    plot_panel(ax_q1, q1_df, "2025Q1")
    
    q2_df = df[(df['date'] >= '2025-04-01') & (df['date'] < '2025-07-01')]
    plot_panel(ax_q2, q2_df, "2025Q2")
    
    q3_df = df[(df['date'] >= '2025-07-01') & (df['date'] < '2025-10-01')]
    plot_panel(ax_q3, q3_df, "2025Q3")
    
    q4_df = df[(df['date'] >= '2025-10-01') & (df['date'] <= '2025-12-31')]
    plot_panel(ax_q4, q4_df, "2025Q4")
    
    # Avoid overlapping
    # Removed tight_layout because it overrides the gridspec wspace/hspace
    plt.savefig("../assets/eth_usdc_cointegration.png", dpi=300, bbox_inches='tight')
    print("Saved eth_usdc_cointegration.png with LaTeX styling.")

if __name__ == "__main__":
    generate_charts()
