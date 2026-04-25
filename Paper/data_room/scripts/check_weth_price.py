import clickhouse_connect
client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)
query = """
SELECT toYear(timestamp) as year, min(price_usd), max(price_usd)
FROM aave_timeseries
WHERE symbol='WETH' AND protocol='AAVE_MARKET'
GROUP BY year
ORDER BY year
"""
print(client.query_df(query))
