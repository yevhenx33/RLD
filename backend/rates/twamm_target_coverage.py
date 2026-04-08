import math

def simulate_target_coverage():
    target_coverage = 1000.0  # User wants exactly $1000 of coverage
    
    # 100% funding rate decay
    F = 1.0 
    
    print("=" * 70)
    print(" SOLVING FOR EXACT TARGET COVERAGE ")
    print("=" * 70)
    
    # Test for two different durations: 1 Month and 1 Year
    durations = [
        ("1 Month", 1.0 / 12.0, 31),
        ("1 Year", 1.0, 366)
    ]
    
    P_0 = 10.00  # Index price of token at start
    
    for label, duration_years, days_to_sim in durations:
        # --- THE SOLVER MATH ---
        # 1. Upfront Capital must exactly equal Target Coverage
        upfront_capital = target_coverage
        
        # 2. TWAMM Rate (per year) must equal FundingRate * TargetCoverage
        twamm_annual_rate = F * target_coverage
        
        # 3. Total TWAMM Capital needed is Rate * time
        twamm_capital = twamm_annual_rate * duration_years
        
        total_required_capital = upfront_capital + twamm_capital
        
        print(f"\n--- SCENARIO: {label} TARGETING ${target_coverage:.2f} COVERAGE ---")
        print(f"To guarantee exactly ${target_coverage:.2f} of coverage for {label}:")
        print(f"- Upfront Capital Required: ${upfront_capital:.2f}")
        print(f"- TWAMM Capital Required:   ${twamm_capital:.2f} (Streamed over the duration)")
        print(f"- Total Capital Cost:       ${total_required_capital:.2f}")
        print("-" * 50)
        
        # Simulation loop
        initial_tokens = upfront_capital / P_0
        
        print(f"{'Day':^5} | {'Tokens Held':^15} | {'Token Price':^15} | {'Effective Coverage':^20}")
        print("-" * 70)
        
        for day in range(days_to_sim):
            t_years = day / 365.0
            
            # Avoid out of bounds float 
            if t_years > duration_years and day != (days_to_sim - 1):
                pass
                
            current_price = P_0 * math.exp(-F * t_years)
            
            # TWAMM tokens accumulated = (Rate / P0) * (e^t - 1)
            twamm_tokens = (twamm_annual_rate / P_0) * (math.exp(F * t_years) - 1.0)
            
            total_tokens = initial_tokens + twamm_tokens
            coverage = total_tokens * current_price
            
            # Print at regular intervals
            if day == 0 or day == (days_to_sim - 1) or (days_to_sim < 50 and day % 5 == 0) or (days_to_sim > 50 and day % 60 == 0):
                print(f"{day:^5} | {total_tokens:>15.6f} | ${current_price:>14.4f} | ${coverage:>19.4f}")
        print("=" * 70)

if __name__ == '__main__':
    simulate_target_coverage()

