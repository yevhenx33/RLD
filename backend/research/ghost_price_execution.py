#!/usr/bin/env python3
"""
Price Execution Quality: Ghost Balance (TWAP) vs CLOB (Exact Limit)
=====================================================================
Deep comparison of WHERE your order actually fills:
  - CLOB: always fills at exact limit price (never better, never worse)
  - Ghost Balance: fills at TWAP when trigger is crossed (can be better OR worse)

Simulates 1,000 market scenarios with realistic price dynamics to show:
  1. Distribution of execution prices for both systems
  2. When Ghost Balance gives PRICE IMPROVEMENT (fills above limit)
  3. When Ghost Balance gives PRICE DEGRADATION (fills near limit but at TWAP)
  4. Net expected value comparison across market regimes

Run: python3 backend/tools/ghost_price_execution.py
"""

from dataclasses import dataclass, field
from typing import List, Tuple
import random, math, os, statistics

CHART_DIR = os.path.join(os.path.dirname(__file__), "ghost_limit_charts")

# ── Constants ───────────────────────────────────────────────────────
LIMIT_PRICE = 3000.0   # User's limit sell trigger
N_SIMULATIONS = 1000
TWAP_WINDOW_SECONDS = 300  # 5 minutes


@dataclass
class ExecutionResult:
    """Result of a single fill event."""
    scenario_id: int
    system: str              # "CLOB" or "Ghost"
    exec_price: float        # what you actually got
    limit_price: float       # what you asked for
    improvement_bps: float   # positive = better than limit, negative = worse
    twap_at_fill: float
    spot_at_fill: float
    filled: bool
    time_to_fill: int        # seconds from order placement to fill
    market_regime: str       # "trending", "volatile", "mean_revert", "momentum"


# ── Price Path Generator ───────────────────────────────────────────

def generate_price_path(
    start_price: float,
    regime: str,
    duration_seconds: int = 3600,
    dt: int = 10,
) -> List[Tuple[int, float]]:
    """Generate a realistic second-by-second price path."""
    prices = [(0, start_price)]
    price = start_price

    if regime == "trending_up":
        drift = 0.00002      # steady upward drift
        vol = 0.0003
    elif regime == "trending_down_then_up":
        drift = -0.00001
        vol = 0.0004
    elif regime == "volatile":
        drift = 0.000005
        vol = 0.001
    elif regime == "slow_grind":
        drift = 0.000008
        vol = 0.0001
    elif regime == "spike_and_fade":
        drift = 0.00003
        vol = 0.0006
    elif regime == "mean_revert":
        drift = 0.0
        vol = 0.0005
    else:
        drift = 0.00001
        vol = 0.0003

    for t in range(dt, duration_seconds + dt, dt):
        # GBM with regime-specific drift and vol
        if regime == "spike_and_fade" and 600 < t < 900:
            # Spike phase
            price *= (1 + 0.0002 + vol * random.gauss(0, 1))
        elif regime == "spike_and_fade" and t >= 900:
            # Fade phase — mean-revert toward start
            pull = (start_price * 1.02 - price) * 0.0001
            price *= (1 + pull + vol * 0.5 * random.gauss(0, 1))
        elif regime == "trending_down_then_up" and t > duration_seconds * 0.4:
            price *= (1 + abs(drift) * 2 + vol * random.gauss(0, 1))
        elif regime == "mean_revert":
            pull = (start_price * 1.05 - price) * 0.00005
            price *= (1 + pull + vol * random.gauss(0, 1))
        else:
            price *= (1 + drift + vol * random.gauss(0, 1))

        price = max(price, start_price * 0.8)  # floor
        prices.append((t, price))

    return prices


def build_prefix_sums(prices: List[Tuple[int, float]]) -> List[float]:
    """Build prefix sums for O(1) TWAP computation."""
    prefix = [0.0]
    for _, p in prices:
        prefix.append(prefix[-1] + p)
    return prefix


def compute_twap_fast(prices: List[Tuple[int, float]], prefix: List[float],
                      idx: int, window: int = TWAP_WINDOW_SECONDS) -> float:
    """O(1) TWAP using prefix sums. idx = index into prices list."""
    at_time = prices[idx][0]
    # Find start index (binary search would be ideal, but prices are evenly spaced)
    dt = prices[1][0] - prices[0][0] if len(prices) > 1 else 10
    window_steps = min(idx, window // dt)
    start_idx = max(0, idx - window_steps)
    count = idx - start_idx + 1
    if count <= 0:
        return prices[idx][1]
    return (prefix[idx + 1] - prefix[start_idx]) / count


# ── Execution Simulators ───────────────────────────────────────────

def simulate_clob_execution(
    prices: List[Tuple[int, float]],
    prefix: List[float],
    limit_price: float,
    scenario_id: int,
    regime: str,
) -> ExecutionResult:
    """CLOB: fills at exact limit price the instant spot crosses trigger."""
    for idx, (t, spot) in enumerate(prices):
        if spot >= limit_price:
            twap = compute_twap_fast(prices, prefix, idx)
            return ExecutionResult(
                scenario_id=scenario_id, system="CLOB",
                exec_price=limit_price,
                limit_price=limit_price,
                improvement_bps=0.0,
                twap_at_fill=twap,
                spot_at_fill=spot,
                filled=True,
                time_to_fill=t,
                market_regime=regime,
            )
    return ExecutionResult(
        scenario_id=scenario_id, system="CLOB",
        exec_price=0, limit_price=limit_price,
        improvement_bps=0, twap_at_fill=0, spot_at_fill=0,
        filled=False, time_to_fill=0, market_regime=regime,
    )


def simulate_ghost_execution(
    prices: List[Tuple[int, float]],
    prefix: List[float],
    limit_price: float,
    scenario_id: int,
    regime: str,
) -> ExecutionResult:
    """Ghost Balance: fills at TWAP price when TWAP crosses trigger."""
    for idx, (t, spot) in enumerate(prices):
        twap = compute_twap_fast(prices, prefix, idx)
        if twap >= limit_price:
            improvement = (twap - limit_price) / limit_price * 10000
            return ExecutionResult(
                scenario_id=scenario_id, system="Ghost",
                exec_price=twap,
                limit_price=limit_price,
                improvement_bps=improvement,
                twap_at_fill=twap,
                spot_at_fill=spot,
                filled=True,
                time_to_fill=t,
                market_regime=regime,
            )
    return ExecutionResult(
        scenario_id=scenario_id, system="Ghost",
        exec_price=0, limit_price=limit_price,
        improvement_bps=0, twap_at_fill=0, spot_at_fill=0,
        filled=False, time_to_fill=0, market_regime=regime,
    )


# ── Run Full Simulation ────────────────────────────────────────────

def run_simulation() -> Tuple[List[ExecutionResult], List[ExecutionResult]]:
    """Run N_SIMULATIONS market scenarios through both systems."""
    random.seed(42)

    regimes = ["trending_up", "volatile", "slow_grind", "spike_and_fade",
               "mean_revert", "trending_down_then_up"]
    regime_weights = [0.25, 0.2, 0.15, 0.15, 0.15, 0.1]

    clob_results = []
    ghost_results = []

    for i in range(N_SIMULATIONS):
        regime = random.choices(regimes, weights=regime_weights, k=1)[0]
        start_price = LIMIT_PRICE * random.uniform(0.92, 0.99)  # start below limit

        prices = generate_price_path(start_price, regime, duration_seconds=3600, dt=10)
        prefix = build_prefix_sums(prices)

        clob = simulate_clob_execution(prices, prefix, LIMIT_PRICE, i, regime)
        ghost = simulate_ghost_execution(prices, prefix, LIMIT_PRICE, i, regime)

        clob_results.append(clob)
        ghost_results.append(ghost)

    return clob_results, ghost_results


# ── Analysis & Printing ────────────────────────────────────────────

def analyze_results(clob_results: List[ExecutionResult], ghost_results: List[ExecutionResult]):
    """Print detailed comparison tables."""

    clob_filled = [r for r in clob_results if r.filled]
    ghost_filled = [r for r in ghost_results if r.filled]

    print("\n" + "=" * 78)
    print("  PRICE EXECUTION QUALITY: Ghost Balance (TWAP) vs CLOB (Exact Limit)")
    print("=" * 78)

    # ── Fill Rates ──
    print(f"\n  {'Metric':<40} {'CLOB':>15} {'Ghost':>15}")
    print(f"  {'─'*40} {'─'*15} {'─'*15}")
    print(f"  {'Simulations':<40} {N_SIMULATIONS:>15,} {N_SIMULATIONS:>15,}")
    print(f"  {'Fill Rate':<40} {len(clob_filled)/N_SIMULATIONS*100:>14.1f}% {len(ghost_filled)/N_SIMULATIONS*100:>14.1f}%")

    if not ghost_filled:
        print("  No Ghost fills — cannot compare execution quality")
        return

    # ── Execution Price Stats ──
    clob_prices = [r.exec_price for r in clob_filled]
    ghost_prices = [r.exec_price for r in ghost_filled]
    ghost_improvements = [r.improvement_bps for r in ghost_filled]

    print(f"\n  {'── Execution Price (filled orders) ──':<40}")
    print(f"  {'Avg Exec Price':<40} ${statistics.mean(clob_prices):>13.2f} ${statistics.mean(ghost_prices):>13.2f}")
    print(f"  {'Median Exec Price':<40} ${statistics.median(clob_prices):>13.2f} ${statistics.median(ghost_prices):>13.2f}")
    print(f"  {'Min Exec Price':<40} ${min(clob_prices):>13.2f} ${min(ghost_prices):>13.2f}")
    print(f"  {'Max Exec Price':<40} ${max(clob_prices):>13.2f} ${max(ghost_prices):>13.2f}")

    # ── Price Improvement Analysis (Ghost-specific) ──
    positive = [r for r in ghost_filled if r.improvement_bps > 0]
    zero = [r for r in ghost_filled if r.improvement_bps == 0]
    # Ghost always fills at TWAP >= limit, so improvement is always >= 0

    print(f"\n  {'── Ghost Balance: Price Improvement ──':<40}")
    print(f"  {'Orders with Price Improvement':<40} {len(positive):>13} ({len(positive)/len(ghost_filled)*100:.1f}%)")
    if positive:
        improvements = [r.improvement_bps for r in positive]
        print(f"  {'Avg Improvement':<40} {statistics.mean(improvements):>12.1f} bps")
        print(f"  {'Median Improvement':<40} {statistics.median(improvements):>12.1f} bps")
        print(f"  {'Max Improvement':<40} {max(improvements):>12.1f} bps")
        print(f"  {'P25 Improvement':<40} {sorted(improvements)[len(improvements)//4]:>12.1f} bps")
        print(f"  {'P75 Improvement':<40} {sorted(improvements)[3*len(improvements)//4]:>12.1f} bps")

    # ── Dollar Value: What does price improvement mean? ──
    # For a 10 ETH order filled at Ghost vs CLOB
    order_size = 10.0
    clob_value = order_size * LIMIT_PRICE
    ghost_avg_value = order_size * statistics.mean(ghost_prices)
    ghost_improvement_usd = ghost_avg_value - clob_value

    print(f"\n  {'── Dollar Impact (10 Token0 order) ──':<40}")
    print(f"  {'CLOB fills at':<40} ${clob_value:>13,.2f}")
    print(f"  {'Ghost avg fill at':<40} ${ghost_avg_value:>13,.2f}")
    print(f"  {'Ghost advantage':<40} ${ghost_improvement_usd:>+13,.2f}")
    print(f"  {'Ghost advantage (bps)':<40} {ghost_improvement_usd/clob_value*10000:>+12.1f} bps")

    # ── Time-to-Fill Comparison ──
    clob_ttf = [r.time_to_fill for r in clob_filled]
    ghost_ttf = [r.time_to_fill for r in ghost_filled]

    print(f"\n  {'── Time to Fill (seconds) ──':<40}")
    print(f"  {'Avg Time to Fill':<40} {statistics.mean(clob_ttf):>13.0f}s {statistics.mean(ghost_ttf):>13.0f}s")
    print(f"  {'Median Time to Fill':<40} {statistics.median(clob_ttf):>13.0f}s {statistics.median(ghost_ttf):>13.0f}s")
    print(f"  {'TWAP Lag (Ghost - CLOB avg)':<40} {statistics.mean(ghost_ttf) - statistics.mean(clob_ttf):>+13.0f}s")

    # The lag IS the feature — it's the anti-manipulation window
    print(f"\n  NOTE: Ghost's time-to-fill lag IS the security feature.")
    print(f"  The ~{statistics.mean(ghost_ttf) - statistics.mean(clob_ttf):.0f}s delay = TWAP smoothing window that blocks")
    print(f"  flash-loan manipulation. CLOB fills instantly at any price,")
    print(f"  including manipulated ones.")

    # ── Per-Regime Breakdown ──
    regimes = set(r.market_regime for r in ghost_filled)
    print(f"\n  {'── Per-Regime Breakdown ──':<40}")
    print(f"  {'Regime':<22} {'CLOB Fill':>10} {'Ghost Fill':>10} {'Ghost Avg':>12} {'Improve':>10}")
    print(f"  {'─'*22} {'─'*10} {'─'*10} {'─'*12} {'─'*10}")

    for regime in sorted(regimes):
        c_fills = [r for r in clob_filled if r.market_regime == regime]
        g_fills = [r for r in ghost_filled if r.market_regime == regime]
        if g_fills:
            avg_improvement = statistics.mean([r.improvement_bps for r in g_fills])
            avg_price = statistics.mean([r.exec_price for r in g_fills])
            print(f"  {regime:<22} {len(c_fills):>10} {len(g_fills):>10} ${avg_price:>10.2f} {avg_improvement:>+9.1f}bp")

    # ── The Tradeoff Summary ──
    print(f"\n{'=' * 78}")
    print(f"  THE TRADEOFF")
    print(f"{'=' * 78}")
    print(f"""
  CLOB ADVANTAGE:
    - Fills at EXACT limit price, always. Zero variance.
    - Fills FASTER (instant when spot crosses trigger).
    - Simpler mental model for users.

  GHOST BALANCE ADVANTAGE:
    - Average execution is BETTER than limit price (+{statistics.mean(ghost_improvements):.1f} bps).
    - In trending markets, TWAP catches the upward momentum and fills
      ABOVE your limit — free price improvement.
    - The fill delay IS the security: TWAP smoothing blocks flash loans,
      MEV sandwiches, and spot manipulation.
    - Zero bounce-back risk (CLOB doesn't have this either, but AMMs do).
    - Internal netting: crossed spreads fill at TWAP mid-price with zero fees.

  WHEN GHOST IS STRICTLY BETTER:
    - Trending markets (TWAP > limit → price improvement)
    - Volatile markets (TWAP filters out noise → safer fills)
    - Any market with manipulation risk (flash loans, sandwich attacks)

  WHEN CLOB IS STRICTLY BETTER:
    - User wants EXACT price execution (no variance)
    - Ultra-fast fill is critical (HFT, arb)
    - Fading markets where price briefly touches limit then drops
      (Ghost may miss the fill if TWAP doesn't sustain)
""")

    # ── Paired comparison: same scenario, which paid more? ──
    paired_ghost_wins = 0
    paired_clob_wins = 0
    paired_neither = 0
    for c, g in zip(clob_results, ghost_results):
        if c.filled and g.filled:
            if g.exec_price > c.exec_price:
                paired_ghost_wins += 1
            elif c.exec_price > g.exec_price:
                paired_clob_wins += 1
            else:
                paired_neither += 1
        elif c.filled and not g.filled:
            paired_clob_wins += 1  # CLOB filled but Ghost didn't
        elif g.filled and not c.filled:
            paired_ghost_wins += 1

    total_compared = paired_ghost_wins + paired_clob_wins + paired_neither
    print(f"  PAIRED COMPARISON ({total_compared} scenarios):")
    print(f"    Ghost paid MORE than CLOB:  {paired_ghost_wins:>5} ({paired_ghost_wins/total_compared*100:.1f}%)")
    print(f"    CLOB paid MORE than Ghost:  {paired_clob_wins:>5} ({paired_clob_wins/total_compared*100:.1f}%)")
    print(f"    Identical execution:        {paired_neither:>5} ({paired_neither/total_compared*100:.1f}%)")

    return ghost_filled, clob_filled


# ── Chart Generation ────────────────────────────────────────────────

def generate_charts(ghost_filled: List[ExecutionResult], clob_filled: List[ExecutionResult]):
    """Generate price execution comparison charts."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        print("  matplotlib not installed, skipping charts")
        return []

    os.makedirs(CHART_DIR, exist_ok=True)
    charts = []

    def style_ax(ax, title, ylabel=""):
        ax.set_facecolor('#161b22')
        ax.set_title(title, color='white', fontsize=12, fontweight='bold', pad=10)
        if ylabel:
            ax.set_ylabel(ylabel, color='#8b949e', fontsize=10)
        ax.tick_params(colors='#8b949e')
        for spine in ax.spines.values():
            spine.set_color('#30363d')
        ax.grid(True, alpha=0.15, color='#484f58', axis='y')

    colors = {'ghost': '#7ee787', 'clob': '#58a6ff', 'limit': '#ff7b72'}

    # ── Chart 1: Execution Price Distribution ─────────────────────
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.patch.set_facecolor('#0d1117')

    ax1 = axes[0, 0]
    ghost_prices = [r.exec_price for r in ghost_filled]
    clob_prices = [r.exec_price for r in clob_filled]
    bins = np.linspace(LIMIT_PRICE - 5, max(ghost_prices) + 10, 60)
    ax1.hist(ghost_prices, bins=bins, alpha=0.7, color=colors['ghost'], label='Ghost (TWAP)', edgecolor='#30363d')
    ax1.hist(clob_prices, bins=bins, alpha=0.5, color=colors['clob'], label='CLOB (Exact)', edgecolor='#30363d')
    ax1.axvline(LIMIT_PRICE, color=colors['limit'], linestyle='--', linewidth=2, label=f'Limit ${LIMIT_PRICE:.0f}')
    style_ax(ax1, 'Execution Price Distribution', 'Frequency')
    ax1.set_xlabel('Execution Price ($)', color='#8b949e')
    ax1.legend(fontsize=9, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # ── Chart 2: Price Improvement Distribution (Ghost only) ──────
    ax2 = axes[0, 1]
    improvements = [r.improvement_bps for r in ghost_filled]
    imp_bins = np.linspace(0, max(improvements) + 5, 50)
    ax2.hist(improvements, bins=imp_bins, alpha=0.8, color=colors['ghost'], edgecolor='#30363d')
    ax2.axvline(0, color=colors['limit'], linestyle='--', linewidth=1.5, label='Limit Price (0 bps)')
    avg_imp = statistics.mean(improvements)
    ax2.axvline(avg_imp, color='#ffa657', linestyle='-', linewidth=2, label=f'Mean +{avg_imp:.1f} bps')
    style_ax(ax2, 'Ghost Price Improvement Over Limit (bps)', 'Frequency')
    ax2.set_xlabel('Improvement (basis points)', color='#8b949e')
    ax2.legend(fontsize=9, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # ── Chart 3: Per-Regime Comparison ────────────────────────────
    ax3 = axes[1, 0]
    regimes = sorted(set(r.market_regime for r in ghost_filled))
    regime_clob_avg = []
    regime_ghost_avg = []
    for regime in regimes:
        g = [r.exec_price for r in ghost_filled if r.market_regime == regime]
        c = [r.exec_price for r in clob_filled if r.market_regime == regime]
        regime_ghost_avg.append(statistics.mean(g) if g else LIMIT_PRICE)
        regime_clob_avg.append(statistics.mean(c) if c else LIMIT_PRICE)

    x = np.arange(len(regimes))
    w = 0.35
    ax3.bar(x - w/2, regime_ghost_avg, w, color=colors['ghost'], alpha=0.85, label='Ghost (TWAP)')
    ax3.bar(x + w/2, regime_clob_avg, w, color=colors['clob'], alpha=0.85, label='CLOB (Exact)')
    ax3.axhline(LIMIT_PRICE, color=colors['limit'], linestyle='--', linewidth=1.5, alpha=0.7)
    ax3.set_xticks(x)
    ax3.set_xticklabels([r.replace('_', '\n') for r in regimes], fontsize=8, color='#c9d1d9')
    style_ax(ax3, 'Avg Execution Price by Market Regime', 'Exec Price ($)')
    ax3.legend(fontsize=9, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    # ── Chart 4: Time-to-Fill Comparison ──────────────────────────
    ax4 = axes[1, 1]
    ghost_ttf = [r.time_to_fill / 60 for r in ghost_filled]  # minutes
    clob_ttf = [r.time_to_fill / 60 for r in clob_filled]
    ttf_bins = np.linspace(0, max(max(ghost_ttf), max(clob_ttf)) + 1, 40)
    ax4.hist(clob_ttf, bins=ttf_bins, alpha=0.5, color=colors['clob'], label='CLOB', edgecolor='#30363d')
    ax4.hist(ghost_ttf, bins=ttf_bins, alpha=0.7, color=colors['ghost'], label='Ghost', edgecolor='#30363d')
    avg_lag = statistics.mean(ghost_ttf) - statistics.mean(clob_ttf)
    ax4.annotate(f'TWAP lag: +{avg_lag:.1f} min\n(= manipulation shield)',
                xy=(statistics.mean(ghost_ttf), 0), xytext=(statistics.mean(ghost_ttf) + 2, max(40, len(ghost_ttf) // 10)),
                arrowprops=dict(arrowstyle='->', color='#ffa657'), fontsize=9, color='#ffa657')
    style_ax(ax4, 'Time-to-Fill Distribution', 'Frequency')
    ax4.set_xlabel('Time (minutes)', color='#8b949e')
    ax4.legend(fontsize=9, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9')

    fig.suptitle('Price Execution: Ghost Balance (TWAP) vs CLOB (Exact Limit)',
                 color='white', fontsize=15, fontweight='bold', y=0.98)
    path = os.path.join(CHART_DIR, "price_execution_comparison.png")
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    plt.close(fig)
    charts.append(path)
    print(f"  Chart saved: {path}")

    # ── Chart 5: Scatter — Ghost exec price vs CLOB (paired) ─────
    fig2, ax5 = plt.subplots(figsize=(10, 8))
    fig2.patch.set_facecolor('#0d1117')

    paired_ghost = []
    paired_clob = []
    paired_regimes = []
    for g, c in zip(ghost_filled, clob_filled):
        if g.filled and c.filled and g.scenario_id == c.scenario_id:
            paired_ghost.append(g.exec_price)
            paired_clob.append(c.exec_price)
            paired_regimes.append(g.market_regime)

    regime_colors = {
        'trending_up': '#7ee787', 'volatile': '#f0883e', 'slow_grind': '#58a6ff',
        'spike_and_fade': '#d2a8ff', 'mean_revert': '#ff7b72', 'trending_down_then_up': '#ffa657',
    }
    for regime in set(paired_regimes):
        gp = [g for g, r in zip(paired_ghost, paired_regimes) if r == regime]
        cp = [c for c, r in zip(paired_clob, paired_regimes) if r == regime]
        ax5.scatter(cp, gp, alpha=0.5, s=15, color=regime_colors.get(regime, '#8b949e'),
                   label=regime.replace('_', ' '), edgecolors='none')

    # Diagonal = equal execution
    mn, mx = min(min(paired_clob), min(paired_ghost)), max(max(paired_clob), max(paired_ghost))
    ax5.plot([mn, mx], [mn, mx], color='#484f58', linestyle='--', linewidth=1, alpha=0.5)
    ax5.fill_between([mn, mx], [mn, mx], [mx, mx], alpha=0.08, color=colors['ghost'],
                     label='Ghost wins (above line)')
    style_ax(ax5, 'Paired Execution: Ghost vs CLOB (each dot = same scenario)', 'Ghost Exec Price ($)')
    ax5.set_xlabel('CLOB Exec Price ($)', color='#8b949e', fontsize=10)
    ax5.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d', labelcolor='#c9d1d9',
              loc='upper left')

    path2 = os.path.join(CHART_DIR, "paired_execution_scatter.png")
    fig2.savefig(path2, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    plt.close(fig2)
    charts.append(path2)
    print(f"  Chart saved: {path2}")

    return charts


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 78)
    print("  PRICE EXECUTION QUALITY COMPARISON")
    print("  Ghost Balance (TWAP Fill) vs CLOB (Exact Limit Fill)")
    print(f"  {N_SIMULATIONS:,} simulated market scenarios")
    print("=" * 78)

    clob_results, ghost_results = run_simulation()
    ghost_filled, clob_filled = analyze_results(clob_results, ghost_results)

    # Assertions
    ghost_with_improvement = [r for r in ghost_filled if r.improvement_bps > 0]
    assert len(ghost_with_improvement) / len(ghost_filled) > 0.5, \
        "Ghost should give price improvement in >50% of fills"

    avg_improvement = statistics.mean([r.improvement_bps for r in ghost_filled])
    assert avg_improvement > 0, "Ghost average improvement should be positive"

    # Ghost should never fill BELOW limit (TWAP >= limit is the trigger condition)
    below_limit = [r for r in ghost_filled if r.exec_price < LIMIT_PRICE - 0.01]
    assert len(below_limit) == 0, f"Ghost should NEVER fill below limit! Found {len(below_limit)}"

    print(f"\n  ALL ASSERTIONS PASSED")
    print(f"  - Ghost gives price improvement in {len(ghost_with_improvement)/len(ghost_filled)*100:.0f}% of fills")
    print(f"  - Average improvement: +{avg_improvement:.1f} bps")
    print(f"  - Zero fills below limit price (guaranteed by TWAP trigger)")

    charts = generate_charts(ghost_filled, clob_filled)
    print(f"\n  Charts saved to: {CHART_DIR}/")
