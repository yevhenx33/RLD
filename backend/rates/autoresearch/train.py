import sys
from prepare import load_and_preprocess_data, evaluate_fitness
from model import run_optimal_model

def run_autoresearch_evaluation():
    csv_path = "/home/ubuntu/RLD/Research/RLD/datasets/aave_usdc_rates_eth_prices.csv"
    
    print("\n--- AUTORESEARCH: EVALUATION LOOP ---")
    print("Loading data pipeline...")
    df = load_and_preprocess_data(csv_path)
    
    eth_log = df['eth_log'].values
    apy_pct = df['apy_pct'].values
    
    print("Executing Formalized Model...")
    # The agent's modified model implementation
    residuals, num_params = run_optimal_model(eth_log, apy_pct)
    
    print("Scoring Fitness Algorithm...")
    # Lower fitness means higher predictive strength relative to architectural complexity
    final_fitness = evaluate_fitness(residuals, num_params)
    
    print(f"\nFinal Autoresearch Fitness Score: {final_fitness:.5f}")
    
    if final_fitness < 0.05 + (0.01 * 3): # 0.05 base + (0.01 per param * 3) = 0.08 Threshold
        print("RESULT: ✔ NEW GLOBAL OPTIMUM FOUND")
    else:
        print("RESULT: ✖ SCORE REJECTED. (Reverting model...)")
        
    # Strictly assert that the math meets standard statistical benchmarks
    assert final_fitness < 0.08, "Fatal: Process finalized a structurally invalid logic model."
    
if __name__ == "__main__":
    run_autoresearch_evaluation()
