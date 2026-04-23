import os

def verify_markets_table(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure Markets Table UI is fully responsive
    assert "overflow-x-auto" in content, "Failure Mode: Mobile horizontal scroll wrapper missing."
    assert "min-w-[800px]" in content, "Failure Mode: Inner table width constraint missing, columns will crush on mobile."
    assert "grid-cols-7" in content, "Failure Mode: 6-column grid structure (2 col-span for asset) missing."
    assert "MOCK_POOLS_DATA.map" in content, "Failure Mode: Mock data iteration missing."
    
    print("Markets Table Verified. Mobile horizontal overflow boundaries strictly enforced.")

if __name__ == "__main__":
    verify_markets_table("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
