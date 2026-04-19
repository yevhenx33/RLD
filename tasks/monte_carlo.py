import random
from typing import List, Tuple

def distribute_pro_rata_safe(match_amount: int, balances: List[int]) -> List[int]:
    total = sum(balances)
    if total == 0 or match_amount == 0:
        return [0] * len(balances)
        
    allocations = [0] * len(balances)
    running_fraction = 0
    running_matched = 0
    
    for i in range(len(balances)):
        running_fraction += balances[i]
        
        expected_cumulative_match = (match_amount * running_fraction) // total
        this_match = expected_cumulative_match - running_matched
        allocations[i] = this_match
        running_matched += this_match
        
    return allocations

def execute_global_netting(
    engines_g0: List[int],
    engines_g1: List[int],
) -> Tuple[List[int], List[int]]:
    total_g0 = sum(engines_g0)
    total_g1 = sum(engines_g1)

    intersection_matched = min(total_g0, total_g1)

    matched_0 = distribute_pro_rata_safe(intersection_matched, engines_g0)
    matched_1 = distribute_pro_rata_safe(intersection_matched, engines_g1)
    
    return matched_0, matched_1

def run_monte_carlo(iterations=100_000, num_engines=5):
    print(f"Starting Monte-Carlo Simulation: {iterations} iterations, {num_engines} mock engines.")
    
    zero_balance_safeguards_triggered = 0
    total_volume_matched = 0
    
    for _ in range(iterations):
        g0 = [random.choice([0, random.randint(1, 10000)]) for _ in range(num_engines)]
        g1 = [random.choice([0, random.randint(1, 10000)]) for _ in range(num_engines)]
        
        expected_match = min(sum(g0), sum(g1))
        total_volume_matched += expected_match
        
        m0, m1 = execute_global_netting(g0, g1)
        
        # INVARIANT 1: Total allocations match exact expected match budget
        assert sum(m0) == expected_match, f"Budget math failed G0: {sum(m0)} != {expected_match}"
        assert sum(m1) == expected_match, f"Budget math failed G1: {sum(m1)} != {expected_match}"
        
        # INVARIANT 2: No allocation exceeds the local balance
        for i in range(num_engines):
            assert m0[i] <= g0[i], f"Overdraft! Engine {i} matched {m0[i]}, but only had {g0[i]}"
            assert m1[i] <= g1[i], f"Overdraft! Engine {i} matched {m1[i]}, but only had {g1[i]}"
            
            if g0[i] == 0 and m0[i] == 0:
                zero_balance_safeguards_triggered += 1
            if g1[i] == 0 and m1[i] == 0:
                zero_balance_safeguards_triggered += 1

    print("\n[✔] Monte-Carlo Simulation Passed Successfully")
    print(f"Total Iterations: {iterations}")
    print(f"Total Volume Matched: {total_volume_matched:,} raw units")
    print(f"Zero-Balance Sweep Safeguards Triggered: {zero_balance_safeguards_triggered:,} times")

if __name__ == "__main__":
    run_monte_carlo()
