import os

def verify_ui_copy(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure UI text mappings are active
    assert "value=\"(soon)\"" in content, "Failure Mode: Isolated TVL mock value not updated."
    assert "AAVE_V3" in content, "Failure Mode: AAVE_MARKET string map not updated."
    
    print("UI Copy Verified. Text mapping cleanly abstracted at the view-layer.")

if __name__ == "__main__":
    verify_ui_copy("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
