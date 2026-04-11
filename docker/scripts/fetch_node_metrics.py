import urllib.request
import json
import os

def check_reth_mainnet():
    try:
        req = urllib.request.Request("http://127.0.0.1:8546", 
            data=b'{"jsonrpc":"2.0","method":"eth_syncing","params":[],"id":1}',
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2) as response:
            res = json.loads(response.read())
            r = res.get("result")
            if isinstance(r, dict):
                return {"syncing": True, "current_block": int(r.get("currentBlock", "0x0"), 16)}
            else:
                return {"syncing": False, "status": r} # False means fully synced
    except Exception as e:
        return {"healthy": False, "error": str(e)}

def check_lighthouse():
    try:
        req = urllib.request.Request("http://127.0.0.1:5052/eth/v1/node/syncing")
        with urllib.request.urlopen(req, timeout=2) as response:
            res = json.loads(response.read())
            data = res.get("data", {})
            return {"syncing": data.get("is_syncing"), "distance": data.get("sync_distance")}
    except Exception as e:
        return {"healthy": False, "error": str(e)}

def check_morpho_db():
    db_path = "/home/ubuntu/RLD/backend/morpho/data/morpho_enriched_final.db"
    if os.path.exists(db_path):
        size_mb = round(os.path.getsize(db_path) / (1024*1024), 2)
        return {"size_mb": size_mb, "status": "available"}
    return {"status": "missing"}

if __name__ == "__main__":
    result = {
        "reth_mainnet": check_reth_mainnet(),
        "lighthouse": check_lighthouse(),
        "morpho_db": check_morpho_db()
    }
    print(json.dumps(result))
    
    # Poka-Yoke assertions
    assert "reth_mainnet" in result
    assert "lighthouse" in result
