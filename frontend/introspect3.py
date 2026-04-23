import urllib.request
import json

url = "http://localhost:5173/envio-graphql"
query = """
query {
  __schema {
    types {
      name
      fields {
        name
      }
    }
  }
}
"""
req = urllib.request.Request(url, json.dumps({"query": query}).encode('utf-8'), {'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        for t in data['data']['__schema']['types']:
            if t['name'] in ['MarketTimeseriesPoint', 'ProtocolTvlPoint']:
                print(f"Type: {t['name']}")
                for f in (t.get('fields') or []):
                    print(f"  {f['name']}")
except Exception as e:
    print(e)
