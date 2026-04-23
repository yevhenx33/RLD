import os

def verify_change_indicator(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure change prop is wired and uses flex baseline to prevent layout crushing
    assert "flex items-baseline gap-2 whitespace-nowrap" in content, "Failure Mode: Typography flex boundaries omitted, text will wrap and break grid."
    assert "change.startsWith('+')" in content, "Failure Mode: Polarity coloring logic missing."
    
    print("Change Indicator Verified. Typography flex container is strictly bound.")

if __name__ == "__main__":
    verify_change_indicator("/home/ubuntu/RLD/frontend/src/components/pools/MetricsGrid.jsx")
