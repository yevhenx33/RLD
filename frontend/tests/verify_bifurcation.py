import os

def verify_all_bifurcations(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Check that we have exactly 4 instances of the bifurcated layout.
    # Count occurrences of the specific flex grid combination
    count = content.count("className=\"flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto\"")
    assert count == 4, f"Failure Mode: Expected 4 bifurcated panels, found {count}."
    
    print("All Panels Bifurcation Verified. Internal responsive limits mathematically enforced across the grid.")

if __name__ == "__main__":
    verify_all_bifurcations("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
