import urllib.request
import json

url = "http://localhost:5173/analytics/graphql"
query = "{ __schema { types { name } } }"
req = urllib.request.Request(url, json.dumps({"query": query}).encode('utf-8'), {'Content-Type': 'application/json'})
try:
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        for t in data['data']['__schema']['types']:
            if not t['name'].startswith('__'):
                print(t['name'])
except Exception as e:
    print(e)
