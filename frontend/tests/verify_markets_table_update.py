import os

def verify_markets_table_update(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure new column is injected and layout expanded
    assert "grid-cols-8" in content, "Failure Mode: Grid not expanded to 8 columns."
    assert "min-w-[900px]" in content, "Failure Mode: Inner table width constraint not expanded to 900px, columns will crush on mobile."
    assert "text-center" in content, "Failure Mode: Columns not centered."
    assert "Net Worth" in content, "Failure Mode: Net Worth header missing."
    
    print("Markets Table Expansion Verified. Net Worth injected and columns centered.")

if __name__ == "__main__":
    verify_markets_table_update("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
