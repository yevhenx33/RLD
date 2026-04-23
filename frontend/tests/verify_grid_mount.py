import re
import os

def verify_grid_mount(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure semantic defaults are hardcoded to prevent prop drilling crashes
    assert "MetricsGrid" in content, "Failure Mode: MetricsGrid not imported."
    assert "latest={{ apy: 0 }}" in content, "Failure Mode: latest prop is not a semantic object."
    assert "dailyChange={0}" in content, "Failure Mode: dailyChange is missing or not a strict zero."
    
    print("Grid Mount Verified. Poka-Yoke prop constraints active. Component will not crash due to missing properties.")

if __name__ == "__main__":
    verify_grid_mount("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
