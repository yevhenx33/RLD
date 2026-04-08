import math

def simulate_twamm_coverage_365():
    total_capital = 1000.0
    duration_months = 12.0
    duration_years = duration_months / 12.0
    
    # 100% funding rate decay
    F = 1.0 
    
    # --- Strategy A: TWAMM (Constant Exposure) ---
    upfront_buy = total_capital / (1.0 + F * duration_years)
    stream_amount = total_capital - upfront_buy
    twamm_annual_rate = stream_amount / duration_years
    
    # --- Strategy B: No TWAMM (Passive Hold) ---
    passive_upfront_buy = total_capital
    
    print("=" * 80)
    print(" STRATEGY COMPARISON: TWAMM vs NO TWAMM (365 DAYS)")
    print("=" * 80)
    
    P_0 = 10.00  # Index price of token
    initial_tokens_twamm = upfront_buy / P_0
    initial_tokens_passive = passive_upfront_buy / P_0
    
    print(f"\n{'Day':^5} | {'Token Price':^15} | {'TWAMM Coverage (BOUGHT 50%)':^25} | {'NO TWAMM Coverage (BOUGHT 100%)':^25}")
    print("-" * 85)
    
    for day in range(366):
        t_years = day / 365.0
        
        # Token Price drops exponentially
        current_price = P_0 * math.exp(-F * t_years)
        
        # --- TWAMM Strategy ---
        twamm_tokens = (twamm_annual_rate / P_0) * (math.exp(F * t_years) - 1.0)
        total_tokens_twamm = initial_tokens_twamm + twamm_tokens
        coverage_twamm = total_tokens_twamm * current_price
        
        # --- Passive Strategy ---
        coverage_passive = initial_tokens_passive * current_price
        
        if day % 30 == 0 or day == 365:
            print(f"{day:^5} | ${current_price:>14.4f} | ${coverage_twamm:>24.4f} | ${coverage_passive:>24.4f}")

    print("=" * 85)
    
    print("\nFINAL SUMMARY AT END OF 1 YEAR (365 DAYS):")
    print(f"Strategy A (TWAMM):    Started at ${upfront_buy:.2f}.  Ended at ${coverage_twamm:.2f}.   Loss = ${(upfront_buy - coverage_twamm):.2f}")
    print(f"Strategy B (No TWAMM): Started at ${total_capital:.2f}. Ended at ${coverage_passive:.2f}. Loss = ${(total_capital - coverage_passive):.2f}")


if __name__ == '__main__':
    simulate_twamm_coverage_365()

