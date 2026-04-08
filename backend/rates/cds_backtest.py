import typing
import numpy as np
import polars as pl
from datetime import datetime, timedelta

def build_dummy_data(days: int = 100) -> pl.DataFrame:
    """
    Creates dummy 1H data mimicking Aave, Morpho, and ETH prices.
    Injects a 'crisis regime' to test bounded payouts.
    """
    np.random.seed(42)
    base_time = datetime(2025, 1, 1)
    timestamps = [base_time + timedelta(hours=i) for i in range(days * 24)]
    
    aave_rate = np.random.uniform(0.01, 0.05, size=len(timestamps))
    morpho_rate = np.random.uniform(0.01, 0.06, size=len(timestamps))
    eth_price = 3000.0 + np.cumsum(np.random.normal(0, 5, size=len(timestamps)))
    
    # Inject crisis regime (r > 0.20) halfway through
    mid_idx = len(timestamps) // 2
    end_idx = mid_idx + 120 # 5 days of crisis
    aave_rate[mid_idx:end_idx] = np.random.uniform(0.20, 0.85, size=120)
    morpho_rate[mid_idx:end_idx] = np.random.uniform(0.15, 0.95, size=120)
    # ETH price tanks during the yield crisis
    eth_price[mid_idx:end_idx] = eth_price[mid_idx:end_idx] * 0.8 

    return pl.DataFrame({
        "timestamp": timestamps,
        "aave_rate": aave_rate,
        "morpho_rate": morpho_rate,
        "eth_price": eth_price
    })

def calculate_derivative_price(rate: float) -> float:
    """
    Core Protocol Constraint: P = 100 * r.
    Must be strictly capped: [0.0001, 100.0].
    """
    raw_p = rate * 100.0
    return float(max(0.0001, min(100.0, raw_p)))

def deterministic_pnl_loop(df: pl.DataFrame, notional_usd: float = 10000.0) -> pl.DataFrame:
    """
    Deterministic daily loop that calculates the CDS Underwriter P&L.
    Normalizes 1H asynchronous data to a daily UTC midnight grid.
    """
    # 1. STRICT ASOF / FORWARD_FILL TO DAILY GRID
    daily_df = (
        df.with_columns(pl.col("timestamp").dt.date().alias("date_utc"))
        .group_by("date_utc")
        .agg([
            pl.col("aave_rate").last().alias("aave_r"),
            pl.col("morpho_rate").last().alias("morpho_r"),
            pl.col("eth_price").last().alias("wsteth_px")
        ])
        .sort("date_utc")
    )

    # 2. VECTORIZED DERIVATIVE PRICING (Apply Bounded Caps)
    daily_df = daily_df.with_columns([
        pl.col("aave_r").map_elements(calculate_derivative_price, return_dtype=pl.Float64).alias("aave_P"),
        pl.col("morpho_r").map_elements(calculate_derivative_price, return_dtype=pl.Float64).alias("morpho_P")
    ])

    # 3. YIELD SPREAD & LOSS CALCULATIONS
    baseline_strike_P = 8.0 # Yield spike threshold (equiv to 8% APY)
    fixed_funding_premium = 10.0 # Fixed daily premium earned by underwriting ($10/day)

    daily_df = daily_df.with_columns([
        (pl.col("aave_P") - baseline_strike_P).clip(lower_bound=0).alias("aave_payout_pct"),
        (pl.col("morpho_P") - baseline_strike_P).clip(lower_bound=0).alias("morpho_payout_pct"),
    ])

    # Underwriter pays out % of notional per market when crisis hits
    daily_df = daily_df.with_columns([
        (notional_usd * 0.5 * (pl.col("aave_payout_pct") / 100.0)).alias("aave_loss"),
        (notional_usd * 0.5 * (pl.col("morpho_payout_pct") / 100.0)).alias("morpho_loss"),
    ])

    # 4. COLLATERAL (HEDGE) VALUE TRACKING
    initial_eth = daily_df["wsteth_px"].item(0)
    eth_amount = notional_usd / initial_eth

    daily_df = daily_df.with_columns([
        ((pl.col("wsteth_px") - pl.col("wsteth_px").shift(1).fill_null(pl.col("wsteth_px"))) * eth_amount).alias("hedge_pnl"),
        (pl.lit(fixed_funding_premium) - pl.col("aave_loss") - pl.col("morpho_loss")).alias("cds_net_pnl")
    ]).with_columns([
        (pl.col("hedge_pnl") + pl.col("cds_net_pnl")).alias("total_strategy_pnl")
    ])

    return daily_df

def calculate_portfolio_sharpe(daily_returns: pl.Series, risk_free_rate: float = 0.0) -> float:
    """Returns annualized Sharpe ratio."""
    mean_ret = daily_returns.mean()
    std_ret = daily_returns.std()
    if std_ret == 0 or std_ret is None:
        return 0.0
    return ((mean_ret - risk_free_rate) / std_ret) * np.sqrt(365)

if __name__ == "__main__":
    # ---------------------------------------------------------
    # POKA-YOKE VERIFICATION & MONTE-CARLO PREP
    # ---------------------------------------------------------
    print("Initiating Monte-Carlo Data Generation...")
    dummy_df = build_dummy_data(days=200)

    print("Running Deterministic P&L Loop...")
    results_df = deterministic_pnl_loop(dummy_df)
    
    # 1. ASSERTIONS: No NaN values exist in the final P&L DataFrame
    assert results_df["total_strategy_pnl"].null_count() == 0, "FATAL: NaN encountered in P&L array. Suspected Lookahead missing data."
    assert results_df["aave_P"].null_count() == 0, "FATAL: NaN encountered in Pricing array."

    # 2. ASSERTIONS: Bounded Payout Liability Held True (P in [0.0001, 100])
    max_aave_p = results_df["aave_P"].max()
    min_aave_p = results_df["aave_P"].min()
    assert 0.0001 <= min_aave_p, f"Pricing constraint breached (min): {min_aave_p}"
    assert max_aave_p <= 100.0, f"Pricing constraint breached (max): {max_aave_p}"
    
    # Print Sample Statistics to prove execution
    sharpe = calculate_portfolio_sharpe(results_df["total_strategy_pnl"])
    print("\n--- PASSED: POKA-YOKE CONSTRAINTS VALIDATED ---")
    print(f"Max Aave Derivative Price Encountered: {max_aave_p:.2f}")
    print(f"Annualized Strategy Sharpe: {sharpe:.2f}")
    print("-----------------------------------------------\n")
    print("Simulation Ready for N-Market Real Data Ingestion.")
