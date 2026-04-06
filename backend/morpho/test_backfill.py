import os
import sys
import pytest
import math

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from backend.morpho.backfill_complete import compute_apy, WAD, SECONDS_PER_YEAR

def test_compute_apy_zero_utilization():
    rate_at_target = 1000000000  # Some positive rate
    utilization = 0
    fee = 0
    
    borrow_apy, supply_apy = compute_apy(rate_at_target, utilization, fee)
    assert borrow_apy > 0
    assert supply_apy == 0.0

def test_compute_apy_typical():
    # 5% rate at target per second -> ~5% APY
    # if rate per sec = 5% / SECONDS_PER_YEAR.
    # WAD = 1e18
    rate = int((math.log(1.05) / SECONDS_PER_YEAR) * WAD)
    utilization = int(0.9 * WAD) # 90%
    fee = int(0.1 * WAD) # 10% fee
    
    borrow_apy, supply_apy = compute_apy(rate, utilization, fee)
    
    assert math.isclose(borrow_apy, 0.05, abs_tol=1e-4)
    # Supply APY should be 0.05 * 0.9 * 0.9 = 0.0405
    assert math.isclose(supply_apy, 0.0405, abs_tol=1e-4)

def test_compute_apy_none():
    assert compute_apy(None, 0.0, 0.0) == (None, None)
    assert compute_apy(100, None, 0.0) == (None, None)

def test_block_timestamp_estimation():
    # Simple arithmetic check similar to backfill_complete.py
    head_block = 20000000
    head_ts = 1700000000
    created_block = 19999000
    seconds_per_block = 12
    
    blocks_diff = head_block - created_block
    genesis_ts = head_ts - (blocks_diff * seconds_per_block)
    
    # 1000 blocks diff = 12000 seconds
    assert genesis_ts == 1700000000 - 12000
    
    # Estimate timestamp for a block 10 blocks after genesis
    block = created_block + 10
    block_diff = block - created_block
    snap_ts = genesis_ts + (block_diff * seconds_per_block)
    
    # Should be exactly genesis_ts + 120 seconds
    assert snap_ts == genesis_ts + 120
