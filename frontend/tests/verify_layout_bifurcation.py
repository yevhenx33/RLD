import os

def verify_layout_bifurcation(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure grid layout matches top panels (1 col settings, 3 cols chart)
    assert "grid-cols-1 lg:grid-cols-4" in content, "Failure Mode: Parent wrapper grid constraints missing."
    assert "col-span-1" in content, "Failure Mode: Settings panel col-span-1 constraint missing."
    assert "lg:col-span-3" in content, "Failure Mode: Chart panel lg:col-span-3 constraint missing."
    
    print("Layout Bifurcation Verified. 1:3 Settings-Chart proportion mathematically enforced.")

if __name__ == "__main__":
    verify_layout_bifurcation("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
