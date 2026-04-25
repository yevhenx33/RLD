import clickhouse_connect

client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

query = "SELECT min(timestamp), max(timestamp) FROM aave_timeseries WHERE symbol='WETH' AND protocol='AAVE_MARKET'"
print("WETH aave_timeseries Dates:", client.query(query).result_rows)

