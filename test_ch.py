import clickhouse_connect
client = clickhouse_connect.get_client(host='127.0.0.1', port=8123)

query = """
SELECT count(*), min(price), max(price) 
FROM chainlink_prices 
WHERE feed='ETH / USD' AND toYear(timestamp) = 2025
"""
print("ETH / USD stats:")
print(client.query_df(query))

query_sample = """
SELECT toDate(timestamp) as date,
       argMax(price, block_number) as eth_price
FROM chainlink_prices
WHERE feed='ETH / USD' AND toYear(timestamp) = 2025
GROUP BY date
ORDER BY date
LIMIT 5
"""
print("ETH / USD sample:")
print(client.query_df(query_sample))
