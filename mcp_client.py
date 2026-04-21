import requests
import json
import sseclient
import threading
import time

url = "https://docs.eurekalabs.xyz/~gitbook/mcp"

def run_client():
    headers = {
        "Accept": "text/event-stream"
    }

    try:
        response = requests.get(url, headers=headers, stream=True)
        client = sseclient.SSEClient(response)

        endpoint = None

        for event in client.events():
            if event.event == "endpoint":
                endpoint = event.data
                print(f"Got endpoint: {endpoint}")
                break

        if not endpoint:
            print("No endpoint received")
            return

        # Prepare request
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list"
        }

        # The endpoint must be absolute, if it's relative resolve it
        from urllib.parse import urljoin
        post_url = urljoin(url, endpoint)

        print(f"Sending POST to {post_url}")
        res = requests.post(post_url, json=req)
        print(f"POST status: {res.status_code}")

        # Continue reading events for the response
        for event in client.events():
            if event.event == "message":
                data = json.loads(event.data)
                print(f"Received JSON: {json.dumps(data, indent=2)}")
                if "result" in data and "tools" in data["result"]:
                    # Found tools
                    # Let's call the first search tool
                    tools = data["result"]["tools"]
                    for t in tools:
                        if t["name"] == "search":
                            req2 = {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/call",
                                "params": {
                                    "name": t["name"],
                                    "arguments": {"query": "BEX separation math execution"}
                                }
                            }
                            requests.post(post_url, json=req2)
                            break
                if data.get("id") == 2:
                    break

    except Exception as e:
        print(f"Error: {e}")

run_client()
