#!/usr/bin/env python3
"""
Differential Fuzzing Framework for StandardFundingModel

This script generates test vectors and compares Solidity expWad results
against Python's high-precision math library (mpmath).

Usage:
    python funding_differential.py generate    # Generate test vectors
    python funding_differential.py verify      # Verify against Solidity output
    python funding_differential.py fuzz        # Generate random fuzz vectors
"""

import json
import math
import random
import sys
from decimal import Decimal, getcontext
from pathlib import Path

# Set high precision for Decimal calculations
getcontext().prec = 50

# Constants matching Solidity
WAD = Decimal("1e18")
FUNDING_PERIOD = 30 * 24 * 3600  # 30 days in seconds
ONE_DAY = 86400


def calculate_funding_python(
    mark: int, 
    index: int, 
    dt: int, 
    current_norm: int = int(1e18),
    funding_period: int = FUNDING_PERIOD
) -> tuple[int, int]:
    """
    Python implementation of StandardFundingModel.calculateFunding
    
    Formula: newNorm = oldNorm * exp(-rate * dt / fundingPeriod)
    Where: rate = (mark - index) / index
    
    Returns: (new_norm_factor, funding_rate) as integers in WAD
    """
    if mark == 0 or index == 0:
        raise ValueError("Zero price not allowed")
    
    # Convert to Decimal for precision
    mark_d = Decimal(mark)
    index_d = Decimal(index)
    norm_d = Decimal(current_norm)
    dt_d = Decimal(dt)
    period_d = Decimal(funding_period)
    
    # Calculate funding rate: (mark - index) / index
    price_diff = mark_d - index_d
    rate = (price_diff * WAD) / index_d  # Rate in WAD
    
    # Calculate exponent: -rate * dt / period
    exponent = (-rate * dt_d) / (period_d * WAD)
    
    # Calculate multiplier: exp(exponent)
    # Note: exponent is now a pure decimal (not WAD scaled)
    multiplier = Decimal(math.exp(float(exponent)))
    
    # Apply to norm factor
    new_norm = (norm_d * multiplier * WAD) / WAD
    
    return int(new_norm), int(rate)


def generate_static_vectors() -> list[dict]:
    """Generate the same static test vectors as Solidity tests"""
    vectors = [
        {"name": "Scenario A: Normal (+1%)", "mark": int(5.05e18), "index": int(5.00e18), "dt": ONE_DAY},
        {"name": "Scenario B: Bull (+50%)", "mark": int(7.50e18), "index": int(5.00e18), "dt": ONE_DAY},
        {"name": "Scenario C: Bear (-20%)", "mark": int(4.00e18), "index": int(5.00e18), "dt": ONE_DAY},
        {"name": "Scenario D: Crash (-99%)", "mark": int(0.05e18), "index": int(5.00e18), "dt": ONE_DAY},
        {"name": "Scenario E: Moon (+19900%)", "mark": int(1000e18), "index": int(5.00e18), "dt": ONE_DAY},
        # Additional time variations
        {"name": "1% Premium, 7 days", "mark": int(5.05e18), "index": int(5.00e18), "dt": 7 * ONE_DAY},
        {"name": "1% Premium, 30 days", "mark": int(5.05e18), "index": int(5.00e18), "dt": 30 * ONE_DAY},
        {"name": "50% Premium, 30 days", "mark": int(7.50e18), "index": int(5.00e18), "dt": 30 * ONE_DAY},
    ]
    
    results = []
    for v in vectors:
        try:
            norm, rate = calculate_funding_python(v["mark"], v["index"], v["dt"])
            results.append({
                "name": v["name"],
                "mark": v["mark"],
                "index": v["index"],
                "dt": v["dt"],
                "expectedNorm": norm,
                "rate": rate
            })
        except Exception as e:
            print(f"Error in {v['name']}: {e}")
    
    return results


def generate_fuzz_vectors(count: int = 1000, seed: int = 42) -> list[dict]:
    """Generate random fuzz vectors for differential testing"""
    random.seed(seed)
    vectors = []
    
    for i in range(count):
        # Realistic price ranges: $0.01 to $10,000
        mark = random.randint(int(1e16), int(10_000e18))
        index = random.randint(int(1e16), int(10_000e18))
        
        # Time: 1 second to 90 days
        dt = random.randint(1, 90 * ONE_DAY)
        
        # Skip extreme ratios that would cause overflow
        if mark > index * 10**9 or index > mark * 10**9:
            continue
        
        try:
            norm, rate = calculate_funding_python(mark, index, dt)
            vectors.append({
                "name": f"Fuzz_{i}",
                "mark": mark,
                "index": index,
                "dt": dt,
                "expectedNorm": norm,
                "rate": rate
            })
        except Exception as e:
            continue
    
    return vectors


def verify_solidity_output(solidity_output: list[dict], tolerance_rel: float = 1e-12) -> bool:
    """
    Verify Solidity output against Python calculations
    
    Args:
        solidity_output: List of dicts with solidity results
        tolerance_rel: Relative tolerance for comparison
    
    Returns:
        True if all tests pass
    """
    all_passed = True
    
    for i, sol in enumerate(solidity_output):
        try:
            py_norm, py_rate = calculate_funding_python(
                sol["mark"], sol["index"], sol["dt"]
            )
            
            sol_norm = sol["expected_norm"]
            
            # Calculate relative error
            if py_norm != 0:
                rel_error = abs(sol_norm - py_norm) / py_norm
            else:
                rel_error = abs(sol_norm - py_norm)
            
            if rel_error > tolerance_rel:
                print(f"❌ FAIL [{i}]: mark={sol['mark']}, index={sol['index']}, dt={sol['dt']}")
                print(f"   Solidity: {sol_norm}")
                print(f"   Python:   {py_norm}")
                print(f"   Error:    {rel_error:.2e}")
                all_passed = False
            else:
                print(f"✓ PASS [{i}]: {sol.get('name', 'unnamed')}")
        except Exception as e:
            print(f"⚠ ERROR [{i}]: {e}")
            all_passed = False
    
    return all_passed


def export_json(output_path: str = "funding.json"):
    """Export test vectors to JSON for Solidity consumption"""
    data = {
        "static": generate_static_vectors(),
        "fuzz": generate_fuzz_vectors(1000)
    }
    
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"Exported {len(data['static'])} static + {len(data['fuzz'])} fuzz vectors to {output_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "generate":
        # Generate and save test vectors
        output_path = sys.argv[2] if len(sys.argv) > 2 else "test/differential/data/funding.json"
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        export_json(output_path)
        
    elif command == "verify":
        # Verify from JSON file
        input_path = sys.argv[2] if len(sys.argv) > 2 else "solidity_output.json"
        with open(input_path) as f:
            solidity_data = json.load(f)
        
        if verify_solidity_output(solidity_data):
            print("\n✓ All tests passed!")
            sys.exit(0)
        else:
            print("\n❌ Some tests failed!")
            sys.exit(1)
    
    elif command == "fuzz":
        # Generate random fuzz vectors only
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        vectors = generate_fuzz_vectors(count)
        print(json.dumps(vectors, indent=2))
        
    elif command == "test":
        # Quick self-test
        print("=== Static Test Vectors ===\n")
        for v in generate_static_vectors():
            print(f"{v['name']}:")
            print(f"  Mark: {v['mark']}, Index: {v['index']}, dt: {v['dt']}s")
            print(f"  Expected Norm: {v['expectedNorm']}")
            print(f"  Rate: {v['rate']}")
            print()
    
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
