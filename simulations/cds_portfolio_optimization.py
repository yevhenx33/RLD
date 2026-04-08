import numpy as np
from typing import Dict, List, Tuple

def calculate_tier_weights(collateral_symbol: str) -> float:
    """
    Deterministic risk budgeting. 
    Returns the capital weighting multiplier based on hard-coded collateral tiering.
    """
    tier_1_blue_chips = {"WBTC", "cbBTC", "tBTC", "wstETH", "WETH"}
    tier_2_standard = {"LBTC", "srUSD", "syrupUSDC", "sUSDe"}
    # Everything else is considered Tier 3 Exotic/Speculative
    
    if collateral_symbol in tier_1_blue_chips:
        return 3.0
    elif collateral_symbol in tier_2_standard:
        return 1.0
    else:
        return 0.2

def calculate_allocations(markets: List[str], total_capital: float) -> Dict[str, float]:
    """
    Calculates exact dollar allocations ensuring the sum perfectly equals total_capital.
    """
    weights = {m: calculate_tier_weights(m) for m in markets}
    total_weight = sum(weights.values())
    
    assert total_weight > 0, "Total weight must be positive."
    
    allocations = {m: (weights[m] / total_weight) * total_capital for m in markets}
    
    # Poka-Yoke: Ensure physical limits are respected mathematically
    assert abs(sum(allocations.values()) - total_capital) < 0.01, "Allocation sum mismatch!"
    
    return allocations

def calculate_cds_portfolio_pnl(
    allocations: Dict[str, float], 
    default_markets: set, 
    initial_apy: Dict[str, float]
) -> Tuple[float, float, float]:
    """
    Simulates the ultimate PnL using the exact 7-Day Trap decay metrics.
    Premium Collected = Tokens Minted * (100 * initial_apy) [capped at 100]
    Tokens Minted = Escrow / 100
    Liability = Tokens_Default * 100 or Tokens_End * Final_Price
    """
    F = -np.log(1 - 0.80)
    
    total_escrow = sum(allocations.values())
    total_premium = 0.0
    total_liability = 0.0
    
    # Hardcoded timeline data gathered from DB queries for exact default epochs
    exposure_days = {
        "USR": 356.2,
        "sdeUSD": 218.6,
        "RLP": 356.2,
        "USCC": 221.7,
    }
    
    for m, escrow in allocations.items():
        tokens_minted = escrow / 100.0
        
        # Collect Premium
        start_r = initial_apy.get(m, 0.04) # Default to 4% if missing for MRE
        initial_price = min(100.0, 100.0 * start_r)
        premium = tokens_minted * initial_price
        total_premium += premium
        
        if m in default_markets:
            dt_years = exposure_days[m] / 365.25
            nf = np.exp(-F * dt_years)
            tokens_due = tokens_minted * nf
            liability = tokens_due * 100.0
            total_liability += liability
        else:
            dt_years = 365.0 / 365.25
            nf = np.exp(-F * dt_years)
            tokens_due = tokens_minted * nf
            end_price = 3.0 # Approx avg end price for healthy markets
            total_liability += tokens_due * end_price

    net_pnl = total_premium - total_liability
    return total_premium, total_liability, net_pnl

if __name__ == "__main__":
    test_markets = ["USR", "WBTC", "sdeUSD", "cbBTC", "wstETH", "srUSD", "RLP", "syrupUSDC", "sUSDe", "LBTC", "USCC", "tBTC"]
    
    allocations = calculate_allocations(test_markets, 1000000.0)
    
    # Poka-Yoke verification 1: Check Blue Chip allocation vs Speculative
    assert allocations["WBTC"] > 170000, "Blue chips underfunded"
    assert allocations["USR"] < 15000, "Speculative overfunded"
    
    print(f"{'Market':<12} {'Tier Multiplier':<18} {'Capital Allocation'}")
    print("-" * 50)
    for m, alloc in allocations.items():
        print(f"{m:<12} {calculate_tier_weights(m):<18} ${alloc:,.2f}")
        
    print("\nSimulating Optimized CDS Portfolio PnL...")
    defaults = {"USR", "sdeUSD", "RLP", "USCC"}
    apys = {
        "USR": 0.039, "WBTC": 0.0396, "sdeUSD": 0.0011, "cbBTC": 0.0403, 
        "wstETH": 0.0415, "srUSD": 0.054, "RLP": 0.0589, "syrupUSDC": 0.0443, 
        "sUSDe": 0.0603, "LBTC": 0.047, "USCC": 0.0234, "tBTC": 0.0605
    }
    
    premium, liability, net = calculate_cds_portfolio_pnl(allocations, defaults, apys)
    
    # Poka-Yoke verification 2: Confirm losses are mathematically compressed
    assert net > -25000.0, "Tail risk was not successfully suppressed"
    
    print("-" * 50)
    print(f"Total Premium Collected: ${premium:,.2f}")
    print(f"Total Liability Paid:    ${liability:,.2f}")
    print(f"Optimized Net PnL:       ${net:,.2f} ({(net/1000000)*100:.2f}%)")
