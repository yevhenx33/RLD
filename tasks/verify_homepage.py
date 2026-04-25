import os
import sys
from pathlib import Path

def verify_homepage_is_deterministic(filepath: Path) -> bool:
    """
    Asserts that the frontend Homepage component is stripped of network-polluting 
    debugging logic and non-deterministic inline styling.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Missing target file: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Ensure diagnose() is removed (Network side-effect)
    assert "async function diagnose()" not in content, "Failure Mode: diagnose() function is still present and polluting network requests."
    assert "fetch('/fonts.css'" not in content, "Failure Mode: font fetch requests are still firing on mount."
    
    # 2. Ensure repetitive inline styling is removed
    assert "style={{ fontFamily:" not in content, "Failure Mode: Inline fontFamily overrides exist instead of centralized Tailwind classes."

    # 3. Ensure the replacement typography class is used
    assert "font-jbm" in content, "Failure Mode: font-jbm class is missing. The typography fallback has not been properly applied."

    return True

if __name__ == "__main__":
    homepage_path = Path("../frontend/src/components/landing/Homepage.jsx")
    print(f"Running Poka-Yoke Verification against {homepage_path.resolve()}...")
    try:
        verify_homepage_is_deterministic(homepage_path)
        print("✅ SUCCESS: Homepage is deterministic and side-effect free.")
        sys.exit(0)
    except AssertionError as e:
        print(f"❌ POKA-YOKE FAILURE: {e}")
        sys.exit(1)
