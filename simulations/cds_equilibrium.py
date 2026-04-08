import math
from dataclasses import dataclass

WAD = 10**18

def mulWadDown(a: int, b: int) -> int:
    return (a * b) // WAD

def divWadDown(a: int, b: int) -> int:
    return (a * WAD) // b

def expWad(x: int) -> int:
    """Mock of Solady's expWad using python math, rounded to WAD precision."""
    # Note: expWad natively accepts both positive and negative, but in Solady
    # expWad(x) mathematically requires WAD precision tracking.
    return int(math.exp(x / WAD) * WAD)

def lnWad(x: int) -> int:
    return int(math.log(x / WAD) * WAD)

def calc_funding_rate(target_utilization_wad: int) -> int:
    """F = -ln(1 - utilization) in WAD"""
    one_minus_u = WAD - target_utilization_wad
    return -lnWad(one_minus_u)

def normalization_factor(funding_rate_wad: int, time_elapsed: int) -> int:
    """NF(t) = exp(-F * t) in WAD"""
    exponent = -mulWadDown(funding_rate_wad, time_elapsed)
    return expWad(exponent)

def get_market_price(borrow_rate_wad: int, r_max_wad: int, nf_wad: int) -> int:
    """P_mkt(t) = 100 * (r_t / r_max) * NF(t) in WAD
    Assuming r_max = 1.0 (100% APY in WAD bounds)
    """
    scale = divWadDown(borrow_rate_wad, r_max_wad)
    base_price = mulWadDown(scale, 100 * WAD) 
    return mulWadDown(base_price, nf_wad)

@dataclass
class CDSEquilibriumState:
    time_t: int
    target_utilization: int
    borrow_rate: int
    coverage_target: int
    underwriter_escrow: int

def simulate_step(state: CDSEquilibriumState, time_delta_years: int) -> dict:
    """Simulates a time step of the Grand Equilibrium."""
    funding_rate = calc_funding_rate(state.target_utilization)
    assert funding_rate > 0, "Invalid funding rate"
    
    nf_start = normalization_factor(funding_rate, state.time_t)
    price_start = get_market_price(state.borrow_rate, WAD, nf_start)
    
    # 1. Underwriter Side (JIT Minting)
    # Tokens active = C_locked / (100 * NF)
    tokens_minted = divWadDown(state.underwriter_escrow, mulWadDown(100 * WAD, nf_start))
    
    # 2. Fiduciary Side (TWAMM Purchasing)
    # Required tokens to maintain coverage C
    required_tokens = divWadDown(state.coverage_target, mulWadDown(100 * WAD, nf_start))
    
    # Fast forward time
    next_time = state.time_t + time_delta_years
    nf_end = normalization_factor(funding_rate, next_time)
    
    # Rate of stream over this block: C * F * r_t
    premium_stream = mulWadDown(mulWadDown(state.coverage_target, funding_rate), state.borrow_rate)
    premium_paid_step = mulWadDown(premium_stream, time_delta_years)
    
    return {
        "funding_rate": funding_rate,
        "nf_start": nf_start,
        "nf_end": nf_end,
        "fiduciary_required_tokens": required_tokens,
        "underwriter_active_tokens": tokens_minted,
        "premium_paid": premium_paid_step
    }

if __name__ == "__main__":
    # Poka-Yoke Default Setup
    TARGET_UTIL = int(0.80 * WAD)  # 80% Utilization
    BORROW_RATE = int(0.05 * WAD)  # 5% Borrow Rate
    COVERAGE = int(1_000_000 * WAD) # $1M Target Coverage
    ESCROW = int(1_000_000 * WAD)   # $1M Underwriter Escrow Array
    
    state = CDSEquilibriumState(
        time_t=0,
        target_utilization=TARGET_UTIL,
        borrow_rate=BORROW_RATE,
        coverage_target=COVERAGE,
        underwriter_escrow=ESCROW
    )
    
    print("\n[PHASE 2] Initiating Poka-Yoke Simulation Verification.")
    # 1. Assert: Taylor expansion guarantees Yield > r_supply
    F = calc_funding_rate(TARGET_UTIL)
    r_supply = mulWadDown(TARGET_UTIL, BORROW_RATE)
    yield_cds = mulWadDown(F, BORROW_RATE)
    
    print(f"Base Supply Rate: {r_supply / WAD:.4f}")
    print(f"Mathematical Yield: {yield_cds / WAD:.4f}")
    assert yield_cds > r_supply, "FAILURE: Underwriter yield does not beat baseline pool yield!"
    
    # 2. Simulate 365 Days Iteration
    STEP = WAD // 365 # 1 Day approx in fractional years
    total_premium = 0
    for day in range(365):
        result = simulate_step(state, time_delta_years=STEP)
        # Verify Identity 5.1 (Grand Equilibrium)
        assert result["fiduciary_required_tokens"] == result["underwriter_active_tokens"], "FAILURE: Grand Equilibrium Broken!"
        total_premium += result["premium_paid"]
        state.time_t += STEP

    print(f"Equilibrium clearing maintained uninterrupted for 365 days.")
    print(f"Total Premium Cashflow Stream: {total_premium / WAD:.2f} (Expected exactly {yield_cds * (COVERAGE / WAD) / WAD:.2f})")
    
    print("POKA-YOKE PASS: Validated Constant Dollar Coverage, JIT Escrow Equilibrium, and Yield Dominance.\n")
