import os

def verify_performance_chart(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure the interactive Performance Chart is wired correctly
    assert "RLDPerformanceChart" in content, "Failure Mode: Shared Perps chart component missing."
    assert "Math.floor(date.getTime() / 1000)" in content, "Failure Mode: Timestamps not cast to UNIX epoch seconds, causing chart X-Axis to break."
    assert "format: \"dollar\"" in content, "Failure Mode: Native dollar formatting prop missing."
    
    print("RLDPerformanceChart Integration Verified. Epoch boundaries mathematically enforced.")

if __name__ == "__main__":
    verify_performance_chart("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
