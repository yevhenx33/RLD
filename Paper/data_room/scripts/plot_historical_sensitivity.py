import clickhouse_connect
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import requests
import datetime

# 1. Setup LaTeX styling
plt.rcParams.update({
    "text.usetex": False,
    "font.family": "serif",
    "font.serif": ["Computer Modern Roman", "Times New Roman", "serif"],
    "axes.titlesize": 14,
    "axes.labelsize": 12,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    "legend.fontsize": 10,
    "figure.titlesize": 16,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linestyle": "--"
})

# 2. Fetch ETH data from Binance (since 2023-04-01)
# 2023-04-01 is roughly 1680307200000 ms
start_time = int(datetime.datetime(2023, 4, 1).timestamp() * 1000)
end_time = int(datetime.datetime.now().timestamp() * 1000)

eth_data = []
current_start = start_time
while current_start < end_time:
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": "ETHUSDT", "interval": "1d", "limit": 1000, "startTime": current_start}
    res = requests.get(url, params=params).json()
    if not res:
        break
    eth_data.extend(res)
    current_start = res[-1][0] + 1

df_eth = pd.DataFrame(eth_data, columns=['open_time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'nat', 'tbb', 'tbq', 'ignore'])
df_eth['date'] = pd.to_datetime(df_eth['open_time'], unit='ms').dt.normalize()
df_eth['eth_price'] = df_eth['close'].astype(float)
df_eth = df_eth[['date', 'eth_price']].drop_duplicates('date')

# 3. Fetch Aave USDC Borrow Rate from ClickHouse
client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
query_usdc = """
SELECT toDate(timestamp) as date,
       argMax(borrow_apy, inserted_at) as borrow_rate
FROM aave_timeseries
WHERE protocol='AAVE_MARKET' AND symbol='USDC' AND timestamp >= '2023-04-01'
GROUP BY date
ORDER BY date
"""
df_usdc = client.query_df(query_usdc)
df_usdc['date'] = pd.to_datetime(df_usdc['date'])
df_usdc['borrow_rate'] = df_usdc['borrow_rate'] * 100

# Merge
df = pd.merge(df_eth, df_usdc, on='date', how='inner')
df = df.sort_values('date').reset_index(drop=True)

# Calculate 7D MA for APY
df['borrow_rate_7d'] = df['borrow_rate'].rolling(window=7, min_periods=1).mean()

# 4. Plot
fig, ax1 = plt.subplots(figsize=(12, 6))

color_rate = '#E74C3C'  # Alizarin Red
color_price = '#27AE60' # Nephritis Green

# Borrow APY (Left Axis)
line1 = ax1.plot(df['date'], df['borrow_rate_7d'], color=color_rate, linewidth=1.5, label='Borrow APY (7d MA)')
ax1.fill_between(df['date'], 0, df['borrow_rate_7d'], color=color_rate, alpha=0.1)
ax1.set_xlabel('Date')
ax1.set_ylabel('APY (%)', color=color_rate)
ax1.tick_params(axis='y', labelcolor=color_rate)
ax1.set_ylim(0, df['borrow_rate_7d'].max() * 1.1)

# ETH Price (Right Axis)
ax2 = ax1.twinx()
ax2.grid(False)
line2 = ax2.plot(df['date'], df['eth_price'], color=color_price, linewidth=1.2, label='ETH Price')
ax2.set_ylabel('ETH Price (USD)', color=color_price)
ax2.tick_params(axis='y', labelcolor=color_price)
ax2.set_ylim(0, df['eth_price'].max() * 1.1)

# Format y-axis for ETH
import matplotlib.ticker as ticker
ax2.yaxis.set_major_formatter(ticker.StrMethodFormatter('${x:,.0f}'))

# Format x-axis dates
ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
ax1.xaxis.set_major_formatter(mdates.DateFormatter('%d-%b-%y'))
plt.setp(ax1.get_xticklabels(), rotation=45, ha='right')

# Legends
lines = line1 + line2
labels = [l.get_label() for l in lines]
ax1.legend(lines, labels, loc='upper left')

# Title
plt.title('Interest Rate Sensitivity vs. Market Moves (Aave USDC)', pad=15, fontweight='bold')

plt.tight_layout()
plt.savefig("../assets/historical_sensitivity.png", dpi=300, bbox_inches='tight')
print("Saved historical_sensitivity.png to assets folder.")
