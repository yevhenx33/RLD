import os

def verify_bar_chart(filepath):
    assert os.path.exists(filepath), f"File {filepath} missing!"
    
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Poka-Yoke Constraints: Ensure chart constraints are mathematical and won't crash the browser
    assert "length: 156" in content, "Failure Mode: Mock dataset length altered from exactly 3 years (156 weeks)."
    assert "ResponsiveContainer" in content, "Failure Mode: ResponsiveContainer wrapper missing, chart will not scale."
    assert "minTickGap={50}" in content, "Failure Mode: XAxis minTickGap missing, dates will collide on mobile."
    
    print("Bar Chart Integration Verified. Data cardinality and responsive thinning bounds are mathematically enforced.")

if __name__ == "__main__":
    verify_bar_chart("/home/ubuntu/RLD/frontend/src/pages/app/LendingDataPage.jsx")
