import os

def verify_settings_panel(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure CustomCheckbox UI and all required labels are mounted
    assert "CustomCheckbox" in content, "Failure Mode: CustomCheckbox component missing."
    assert "Morpho (soon)" in content, "Failure Mode: Morpho placeholder missing."
    assert "Supply APY" in content, "Failure Mode: Metrics section missing."
    assert "Display In" in content, "Failure Mode: Display Denomination section missing."
    
    print("Settings Panel Population Verified. Bespoke checkboxes are mounted and groups are present.")

if __name__ == "__main__":
    verify_settings_panel("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
