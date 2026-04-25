import os
import sys
from pathlib import Path

def verify_cds_button(filepath: Path) -> bool:
    """
    Asserts that the "Explore CDS" button has been properly activated 
    and points to the correct internal route.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Missing target file: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Ensure the disabled state is removed
    assert "cursor-not-allowed" not in content.split("Explore CDS")[0][-150:], "Failure Mode: CDS button still has cursor-not-allowed."
    assert "border-cyan-900/40" not in content, "Failure Mode: The 'Soon' badge has not been removed."
    
    # 2. Ensure it is wrapped in a valid React Router Link
    assert 'to="/markets/cds"' in content, "Failure Mode: The button is not correctly routing to /markets/cds."
    assert "Explore CDS ↗" in content, "Failure Mode: The active state label/icon is missing."

    return True

if __name__ == "__main__":
    homepage_path = Path("/home/ubuntu/RLD/frontend/src/components/landing/Homepage.jsx")
    print(f"Running Poka-Yoke Verification against {homepage_path.resolve()}...")
    try:
        verify_cds_button(homepage_path)
        print("✅ SUCCESS: CDS button is active and routable.")
        sys.exit(0)
    except AssertionError as e:
        print(f"❌ POKA-YOKE FAILURE: {e}")
        sys.exit(1)
