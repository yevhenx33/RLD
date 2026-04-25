import clickhouse_connect

client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

query_eth = "SELECT min(timestamp), max(timestamp) FROM chainlink_prices WHERE feed='ETH / USD'"
print("ETH Dates:", client.query(query_eth).result_rows)

query_usdc = "SELECT min(timestamp), max(timestamp) FROM aave_timeseries WHERE symbol='USDC' AND protocol='AAVE_MARKET'"
print("USDC Dates:", client.query(query_usdc).result_rows)
