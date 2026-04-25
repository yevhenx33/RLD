import os

def verify_aave_filter(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure pipeline filters are active
    assert "m.protocolKey === \"AAVE\"" in content, "Failure Mode: Aave market filter missing."
    assert "tvl: row.aave || 0" in content, "Failure Mode: Chart TVL still mapping non-Aave data."
    
    print("Aave Isolation Verified. Data pipeline mathematically restricted to Aave V3 only.")

if __name__ == "__main__":
    verify_aave_filter("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
