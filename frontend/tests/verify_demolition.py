import re
import os

def verify_demolition(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure complete demolition of side-effects
    assert "useSWR" not in content, "Failure Mode: useSWR hook still present, causing phantom network requests."
    assert "useEffect" not in content, "Failure Mode: useEffect hook still present, causing side-effects."
    assert "fetch" not in content, "Failure Mode: Fetch API still present."
    assert "DATA" in content, "Failure Mode: Target 'DATA' header missing."
    assert "export default function LendingDataPage" in content, "Failure Mode: Component export missing."
    
    print("Demolition verified. No side-effects present. Component is a pure static skeleton.")

if __name__ == "__main__":
    verify_demolition("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
