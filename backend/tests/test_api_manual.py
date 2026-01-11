from fastapi.testclient import TestClient
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from api import app

client = TestClient(app)

def test_api():
    print("🚀 Testing API against clean_rates.db...")
    
    # Test 1: USDC Rates
    print("\n1. Testing /rates?symbol=USDC&limit=5")
    response = client.get("/rates?symbol=USDC&limit=5")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Success! Received {len(data)} records.")
        if data:
            print(f"Sample: {data[0]}")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")

    # Test 2: DAI Rates (Aggregated)
    print("\n2. Testing /rates?symbol=DAI&resolution=4H&limit=5")
    response = client.get("/rates?symbol=DAI&resolution=4H&limit=5")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Success! Received {len(data)} records.")
        if data:
            print(f"Sample: {data[0]}")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")

    # Test 3: ETH Prices
    print("\n3. Testing /eth-prices?limit=5")
    response = client.get("/eth-prices?limit=5")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Success! Received {len(data)} records.")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")

    # Test 4: Markets Page "Latest" Check (RAW, Limit 1)
    print("\n4. Testing /rates?symbol=USDC&resolution=RAW&limit=1")
    response = client.get("/rates?symbol=USDC&resolution=RAW&limit=1")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Success! Received {len(data)} records.")
        if data:
            print(f"Sample: {data[0]}")
            if "apy" not in data[0]:
                print("❌ CRITICAL: 'apy' field missing in RAW response!")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")

    # Test 5: Markets Page History (1D)
    print("\n5. Testing /rates?symbol=USDC&resolution=1D&limit=5")
    response = client.get("/rates?symbol=USDC&resolution=1D&limit=5")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Success! Received {len(data)} records.")
        if data:
            print(f"Sample: {data[0]}")
    else:
        print(f"❌ Failed: {response.status_code} - {response.text}")

if __name__ == "__main__":
    test_api()
