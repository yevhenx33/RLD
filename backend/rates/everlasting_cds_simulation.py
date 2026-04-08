import math

# Fixed Funding Rate of 100% (c = 1.0)
F = 1.0

def everlasting_simulation():
    print("=" * 60)
    print(" PART 1: 33-YEAR NORMALIZATION FACTOR ASYMPTOTE")
    print("=" * 60)
    print(f"{'Year':^6} | {'NF (Token Value)':^20} | {'Cumulative Decay':^20}")
    print("-" * 60)
    
    # We'll print every few years out to 33 years
    for y in [0, 1, 2, 5, 10, 15, 20, 33]:
        nf = math.exp(-F * y)
        decay = (1 - nf) * 100
        print(f"{y:^6} | {nf:>16.10f} | {decay:>18.6f}%")
        
    print("\n")
    print("=" * 60)
    print(" PART 2: EVERLASTING EQUIVALENCE (1-MONTH vs 1-YEAR)")
    print("=" * 60)
    
    # 1. The Buy and Hold (Letting it bleed)
    one_year_hold_cost = 1.0 - math.exp(-F * 1.0)
    print(f"Cost to buy 1 token and hold for 1 year (bleeding exposure):")
    print(f"-> {one_year_hold_cost * 100:.2f}% of token value lost.\n")
    
    # 2. Sequential 1-Month rolling (Constant Exposure)
    # If a user wants to maintain EXACTLY 1 token of exposure for a full year
    # They must buy back the amount that bled out every single month.
    monthly_cost = 1.0 - math.exp(-F * (1/12.0))
    annual_cost_constant_exposure = monthly_cost * 12
    
    print(f"Cost to maintain EXACTLY 1.0 token of exposure for 1 year full-time:")
    print(f"-> Bleed per month = {monthly_cost * 100:.4f}%")
    print(f"-> Total replenishment cost over 12 months = {annual_cost_constant_exposure * 100:.2f}% of 1 token.\n")
    
    # 3. The Everlasting Paper Core Equation (Weighted Expirations)
    # To buy an everlasting option that specifically maps to constant full exposure, 
    # it costs exactly the integral of the funding rate over the period.
    print(f"Everlasting Integral Equivalence:")
    integral_cost = F * 1.0  # Integral of f * dt from 0 to 1
    print(f"-> Continuous funding integral for 1 unit over 1 year = {integral_cost * 100.0:.2f}%")
    print(f"-> Rolling 1 month x12 is asymptotically approaching this integral ({annual_cost_constant_exposure * 100:.2f}% -> {integral_cost * 100:.2f}%)")
    
    # 4. Compounding Proof
    print("\nThe True Replenishing Math (Proving Fungible Sequences):")
    # If the user buys 1 token, and every month sells whatever is left, and buys a brand new 1-month equivalent.
    # In everlasting options, the survival fraction is exactly compounding.
    print(f"Survival fraction after 1 month = math.exp(-1/12) = {math.exp(-1/12):.6f}")
    print(f"Survival fraction compounded 12 times = (math.exp(-1/12))^12 = {(math.exp(-1/12)**12):.6f}")
    print(f"Survival fraction after 1 year = math.exp(-1) = {math.exp(-1):.6f}")
    print("CONCLUSION: (Month_Decay)^12 == Year_Decay. Perfectly zero-sum invariant.")

if __name__ == '__main__':
    everlasting_simulation()
