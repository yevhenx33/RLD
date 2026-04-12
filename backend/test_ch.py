import clickhouse_connect

try:
    client = clickhouse_connect.get_client(host='localhost', port=8123)
    query = """
    SELECT symbol, protocol, supply_usd, borrow_usd, supply_apy, borrow_apy, utilization
    FROM unified_timeseries WHERE protocol = 'AAVE_MARKET'
    ORDER BY timestamp DESC
    LIMIT 1 BY symbol, protocol
    """
    res = client.query(query)
    for i, r in enumerate(res.result_rows):
        print(r)
        if i > 5: break
except Exception as e:
    print(e)
