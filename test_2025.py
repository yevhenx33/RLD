import subprocess
import json
import datetime

url = "http://127.0.0.1:5000/graphql"

payload = {
    "query": "{ historicalRates(symbols: [\"USDC\", \"DAI\", \"USDT\", \"SOFR\", \"WETH\"], resolution: \"1D\", limit: 17520) { timestamp symbol apy price } }"
}

result = subprocess.run(
    ["curl", "-s", "-X", "POST", url, "-H", "Content-Type: application/json", "-d", json.dumps(payload)],
    capture_output=True,
    text=True
)

data = json.loads(result.stdout)
nodes = data.get("data", {}).get("historicalRates", [])

# Parse strictly > "2025-01-01" like the frontend does
start_ts_2025 = datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc).timestamp()

filtered = [n for n in nodes if n["timestamp"] >= start_ts_2025]

print(f"Total rows fetched: {len(nodes)}")
print(f"Rows matching >= 2025-01-01: {len(filtered)}")

if len(filtered) > 0:
    min_ts = min([n["timestamp"] for n in filtered])
    min_date = datetime.datetime.fromtimestamp(min_ts, tz=datetime.timezone.utc)
    print(f"Oldest record in 2025 set: {min_date}")
else:
    print("NO DATA FOUND FOR 2025")
