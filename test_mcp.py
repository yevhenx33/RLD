import requests
import json
import threading

def run():
    session = requests.Session()
    # To use SSE, we might need to GET to get the stream and endpoint
    url = "https://docs.eurekalabs.xyz/~gitbook/mcp"
    headers = {"Accept": "text/event-stream"}
    try:
        r = session.get(url, headers=headers, stream=True)
        for line in r.iter_lines():
            if line:
                decoded = line.decode('utf-8')
                print(decoded)
                if 'endpoint' in decoded:
                    break
    except Exception as e:
        print(e)

run()
