import sys
import os
import asyncio
from fastapi.testclient import TestClient

# Mock Environment
os.environ["API_KEY"] = "test"
os.environ["DB_DIR"] = os.path.dirname(os.path.abspath(__file__))

# Import the API app
from api import app

client = TestClient(app)

def test_routes():
    print("🚀 Testing API Routes locally...")
    
    headers = {"X-API-Key": "test"}
    
    # 1. Health Check
    try:
        res = client.get("/")
        print(f"Health Check: {res.status_code} - {res.json()}")
    except Exception as e:
        print(f"❌ Health Check Failed: {e}")

    # 2. Rates
    try:
        res = client.get("/rates?resolution=RAW&limit=1", headers=headers)
        print(f"Rates Endpoint: {res.status_code}")
        if res.status_code != 200:
            print(f"Error Response: {res.text}")
    except Exception as e:
        print(f"❌ Rates Endpoint Failed: {e}")

if __name__ == "__main__":
    test_routes()
