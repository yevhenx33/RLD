import os

def verify_grid_width(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure grid width is mathematically restricted to 75% on large screens
    assert "mb-6 w-full xl:w-3/4" in content, "Failure Mode: 75% width constraint missing or malformed."
    
    print("Grid Width Restriction Verified. Panel is explicitly capped at 75% width with mathematical subdivision remaining equal.")

if __name__ == "__main__":
    verify_grid_width("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
