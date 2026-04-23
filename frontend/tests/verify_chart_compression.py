import os

def verify_chart_compression(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure height has been scaled down by exactly 25% (0.75x)
    assert "h-[280px]" in content, "Failure Mode: Mobile height not reduced correctly."
    assert "md:h-[394px]" in content, "Failure Mode: Desktop height not reduced correctly."
    
    print("Chart Height Compression Verified. 0.75x mathematical bounds enforced.")

if __name__ == "__main__":
    verify_chart_compression("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
