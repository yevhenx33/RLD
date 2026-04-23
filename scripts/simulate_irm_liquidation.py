import math
from typing import List
from dataclasses import dataclass

@dataclass
class AccountSnapshot:
    address: str
    collateral_usd: float
    debt_usd: float
    # Weighted average of liquidation thresholds for the collateral assets (e.g., 0.85 for ETH)
    liquidation_threshold: float

@dataclass
class LiquidationAlert:
    address: str
    current_hf: float
    projected_hf: float
    projected_debt_usd: float
    days_to_liquidation: int

def calculate_health_factor(collateral_usd: float, debt_usd: float, liquidation_threshold: float) -> float:
    """
    Standard Aave Health Factor calculation.
    HF = (Collateral * LiquidationThreshold) / Debt
    """
    if debt_usd <= 0:
        return float('inf')
    return (collateral_usd * liquidation_threshold) / debt_usd

def project_debt(current_debt: float, apy: float, days: int) -> float:
    """
    Project future debt given an APY and a number of days using continuous compounding.
    Aave accrues interest per second, which mathematically converges to continuous compounding.
    Formula: Debt_future = Debt_current * e^(r * t)
    where r is the annual continuous rate (r = ln(1 + APY)), and t is time in years.
    """
    time_in_years = days / 365.0
    continuous_rate = math.log(1 + apy)
    return current_debt * math.exp(continuous_rate * time_in_years)

def identify_liquidation_risks(
    accounts: List[AccountSnapshot], 
    new_usdc_apy: float, 
    projection_days: int
) -> List[LiquidationAlert]:
    """
    Sweep across user accounts to identify which will fall below Health Factor 1.0
    under a new interest rate regime, assuming static collateral prices.
    """
    alerts = []
    
    for account in accounts:
        current_hf = calculate_health_factor(
            account.collateral_usd, 
            account.debt_usd, 
            account.liquidation_threshold
        )
        
        # Skip accounts already liquidated
        if current_hf < 1.0:
            continue
            
        projected_debt = project_debt(account.debt_usd, new_usdc_apy, projection_days)
        projected_hf = calculate_health_factor(
            account.collateral_usd, 
            projected_debt, 
            account.liquidation_threshold
        )
        
        if projected_hf < 1.0:
            # Mathematically solve for exact days until HF = 1.0
            # 1.0 = (Collateral * LT) / (Debt * e^(r * t/365))
            # e^(r * t/365) = (Collateral * LT) / Debt
            # t = 365 * ln((Collateral * LT) / Debt) / r
            r = math.log(1 + new_usdc_apy)
            required_ratio = (account.collateral_usd * account.liquidation_threshold) / account.debt_usd
            
            if required_ratio > 0 and r > 0:
                exact_days = (365.0 * math.log(required_ratio)) / r
                days_to_liquidation = math.ceil(exact_days)
            else:
                days_to_liquidation = 0
                
            alerts.append(
                LiquidationAlert(
                    address=account.address,
                    current_hf=round(current_hf, 4),
                    projected_hf=round(projected_hf, 4),
                    projected_debt_usd=round(projected_debt, 2),
                    days_to_liquidation=days_to_liquidation
                )
            )
            
    # Sort by most critical (closest to liquidation)
    alerts.sort(key=lambda x: x.days_to_liquidation)
    return alerts

if __name__ == "__main__":
    # ---------------------------------------------------------
    # POKA-YOKE VERIFICATION & MONTE CARLO SETUP
    # ---------------------------------------------------------
    
    # 1. Verify Happy Path & Catch Failure Modes
    test_accounts = [
        AccountSnapshot("0xSafe", 10000.0, 2000.0, 0.85),    # HF = 4.25
        AccountSnapshot("0xRisky", 10000.0, 8000.0, 0.85),   # HF = 1.0625
        AccountSnapshot("0xDegen", 10000.0, 8400.0, 0.85),   # HF = 1.0119
    ]
    
    # Shock the system with 50% APY to force liquidations within 30 days
    NEW_APY = 0.50 
    PROJECTION_DAYS = 30
    
    risks = identify_liquidation_risks(test_accounts, NEW_APY, PROJECTION_DAYS)
    
    # Assertions strictly enforcing the logic boundaries
    assert len(risks) == 2, "Failure Mode: Did not identify the exact 2 mathematically at-risk accounts."
    assert risks[0].address == "0xDegen", "Failure Mode: Did not rank the most at-risk account first."
    
    print("=== POKA-YOKE VERIFICATION ===")
    for alert in risks:
        assert alert.projected_hf < 1.0, f"Invalid state: {alert.address} HF >= 1.0 but flagged."
        assert alert.days_to_liquidation <= PROJECTION_DAYS, f"Invalid state: Liquidation day outside horizon."
        print(f"[ALERT] {alert.address} liquidates in {alert.days_to_liquidation} days. (HF: {alert.current_hf} -> {alert.projected_hf})")
        
    print("\n[✔] Poka-Yoke Verification Passed. Logic is structurally deterministic and ready for fuzzing.")
