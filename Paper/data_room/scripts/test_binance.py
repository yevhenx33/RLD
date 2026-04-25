import requests
import pandas as pd
url = "https://api.binance.com/api/v3/klines"
params = {"symbol": "ETHUSDT", "interval": "1d", "limit": 1000}
res = requests.get(url, params=params)
data = res.json()
df = pd.DataFrame(data, columns=['open_time', 'open', 'high', 'low', 'close', 'vol', 'close_time', 'qav', 'nat', 'tbb', 'tbq', 'ignore'])
df['date'] = pd.to_datetime(df['open_time'], unit='ms')
df['close'] = df['close'].astype(float)
print(df[['date', 'close']].head())
