import os

def verify_protocol_column(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure protocol column was extracted and layout expanded
    assert "grid-cols-9" in content, "Failure Mode: Grid not expanded to 9 columns."
    assert "min-w-[1000px]" in content, "Failure Mode: Inner table width constraint not expanded to 1000px, columns will crush on mobile."
    assert "text-center" in content, "Failure Mode: Columns not centered."
    assert "Protocol" in content, "Failure Mode: Protocol header missing."
    
    print("Protocol Column Extraction Verified. Horizontal layout safely expanded to 1000px bounds.")

if __name__ == "__main__":
    verify_protocol_column("/home/ubuntu/RLD/frontend/tests/../src/pages/app/LendingDataPage.jsx")
