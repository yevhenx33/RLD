import os

def verify_chart_height(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure height has been scaled by 1.5x proportionally
    assert "h-[375px]" in content, "Failure Mode: Mobile height missing or incorrect."
    assert "md:h-[525px]" in content, "Failure Mode: Desktop height missing or incorrect."
    
    print("Chart Height Adjusted. 1.5x proportional scaling verified on all viewports.")

if __name__ == "__main__":
    verify_chart_height("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
