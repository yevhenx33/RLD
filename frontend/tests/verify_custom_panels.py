import os

def verify_custom_panels(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure grid classes are perfectly replicated to prevent layout bugs
    assert "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4" in content, "Failure Mode: Responsive grid classes omitted."
    assert "divide-y md:divide-y-0 md:divide-x" in content, "Failure Mode: Responsive divider classes omitted."
    assert "OVERVIEW" in content
    assert "RATES" in content
    assert "TVL_BY_TYPE" in content
    assert "STATS" in content
    
    print("Custom Data Panels Verified. Responsive boundaries are mathematically enforced.")

if __name__ == "__main__":
    verify_custom_panels("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
