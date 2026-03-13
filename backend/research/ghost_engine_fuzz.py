#!/usr/bin/env python3
"""
Stress Fuzz: Modular GhostEngine (TWAP Streams + Limit Orders)
================================================================
3,000 rounds of adversarial mixed operations across 3 seeds.
Full drain + conservation proof after each run.

Run: python3 backend/tools/ghost_engine_fuzz.py
"""

import sys, os, random, time
sys.path.insert(0, os.path.dirname(__file__))
from ghost_unified_sim import (
    GhostEngine, TwapStreamModule, LimitOrderModule, EPSILON
)

CHART_DIR = os.path.join(os.path.dirname(__file__), "ghost_limit_charts")


def run_fuzz(n_rounds: int, n_streamers: int, n_limiters: int,
             n_takers: int, seed: int):
    """Run adversarial fuzz with mixed order types."""
    random.seed(seed)
    engine = GhostEngine()
    stream_mod = TwapStreamModule()
    limit_mod = LimitOrderModule()
    engine.register_module(stream_mod)
    engine.register_module(limit_mod)

    total_users = n_streamers + n_limiters + n_takers

    # Mint tokens
    for i in range(n_streamers):
        engine.mint(f"S{i}", 200, 1_000_000)
    for i in range(n_limiters):
        engine.mint(f"L{i}", 100, 500_000)
    for i in range(n_takers):
        engine.mint(f"T{i}", 500, 5_000_000)

    price = 3000.0
    engine.twap = price
    check_interval = max(1, n_rounds // 10)

    # Track active orders for claims
    active_streams = []    # (user, oid)
    active_limits = []     # (user, tick, is_sell)

    stats = {"stream": 0, "limit": 0, "swap": 0, "claim_s": 0,
             "claim_l": 0, "cancel_s": 0, "net": 0, "time": 0}

    t_start = time.time()

    for r in range(n_rounds):
        # Random price walk
        price *= 1 + random.gauss(0, 0.005)
        price = max(2500, min(3500, price))
        engine.twap = engine.twap * 0.7 + price * 0.3

        action = random.random()

        # ── Submit stream order ──
        if action < 0.15:
            user = f"S{random.randint(0, n_streamers - 1)}"
            w = engine._wallet(user)
            is_sell = random.random() < 0.5
            if is_sell and w["T0"] > 1:
                amt = round(random.uniform(0.5, min(20, w["T0"] * 0.3)), 6)
                dur = random.uniform(50, 500)
                try:
                    oid = engine.submit_stream(user, amt, dur, True)
                    active_streams.append((user, oid))
                    stats["stream"] += 1
                except AssertionError:
                    pass
            elif not is_sell and w["T1"] > 1000:
                amt = round(random.uniform(100, min(5000, w["T1"] * 0.3)), 2)
                dur = random.uniform(50, 500)
                try:
                    oid = engine.submit_stream(user, amt, dur, False)
                    active_streams.append((user, oid))
                    stats["stream"] += 1
                except AssertionError:
                    pass

        # ── Submit limit order ──
        elif action < 0.30:
            user = f"L{random.randint(0, n_limiters - 1)}"
            w = engine._wallet(user)
            is_sell = random.random() < 0.5
            tick = round(price * random.uniform(0.95, 1.05))

            if is_sell and w["T0"] > 0.1:
                amt = round(random.uniform(0.1, min(5, w["T0"] * 0.3)), 6)
                try:
                    engine.submit_limit(user, amt, tick, True)
                    active_limits.append((user, tick, True))
                    stats["limit"] += 1
                except AssertionError:
                    pass
            elif not is_sell and w["T1"] > tick:
                amt = round(random.uniform(0.01, min(1, w["T1"] / tick * 0.3)), 6)
                try:
                    engine.submit_limit(user, amt, tick, False)
                    active_limits.append((user, tick, False))
                    stats["limit"] += 1
                except AssertionError:
                    pass

        # ── Taker swap ──
        elif action < 0.55:
            user = f"T{random.randint(0, n_takers - 1)}"
            buy_t0 = random.random() < 0.5
            amt = round(random.uniform(0.1, 10), 6)
            engine.advance_time(random.uniform(1, 30))
            engine.swap(user, buy_t0, amt)
            stats["swap"] += 1
            stats["time"] += 1

        # ── Advance time only ──
        elif action < 0.65:
            engine.advance_time(random.uniform(5, 60))
            stats["time"] += 1

        # ── Claim stream ──
        elif action < 0.75 and active_streams:
            user, oid = random.choice(active_streams)
            engine.claim_stream(user, oid)
            stats["claim_s"] += 1

        # ── Claim limit ──
        elif action < 0.85 and active_limits:
            user, tick, is_sell = random.choice(active_limits)
            engine.claim_limit(user, tick, is_sell)
            stats["claim_l"] += 1

        # ── Cancel stream ──
        elif action < 0.90 and active_streams:
            user, oid = random.choice(active_streams)
            engine.cancel_stream(user, oid)
            active_streams = [(u, o) for u, o in active_streams if o != oid]
            stats["cancel_s"] += 1

        # ── Double-claim (adversarial) ──
        elif action < 0.95 and active_limits:
            user, tick, is_sell = random.choice(active_limits)
            engine.claim_limit(user, tick, is_sell)
            engine.claim_limit(user, tick, is_sell)  # second should be no-op
            stats["claim_l"] += 2

        # ── Invariant check ──
        if (r + 1) % check_interval == 0:
            engine.check_invariants(f"round {r+1}")
            elapsed = time.time() - t_start
            print(f"    Round {r+1:>5,}/{n_rounds:,} | "
                  f"checks: {engine.invariant_checks} | "
                  f"{(r+1)/elapsed:,.0f} ops/s | "
                  f"ghost0: {engine.total_ghost0:.2f} ghost1: {engine.total_ghost1:.2f}")

    t_rounds = time.time() - t_start
    print(f"    Complete: {n_rounds:,} rounds in {t_rounds:.1f}s")
    print(f"    Ops: {stats}")

    return engine, stream_mod, limit_mod, active_streams, active_limits, stats


def full_drain(engine: GhostEngine, stream_mod: TwapStreamModule,
               limit_mod: LimitOrderModule,
               active_streams, active_limits):
    """Drain ALL orders and verify conservation."""
    print(f"\n    ── FULL DRAIN ──")

    # Cancel all remaining streams (refund + earnings)
    for user, oid in active_streams:
        order = stream_mod.orders.get(oid)
        if order and order.sell_rate > 0:
            engine.cancel_stream(user, oid)

    # Also claim expired streams that weren't cancelled
    for oid, order in list(stream_mod.orders.items()):
        if order.sell_rate > 0:
            engine.cancel_stream(order.owner, oid)

    # Claim all limit orders
    for tick in list(limit_mod.sell_buckets.keys()):
        bucket = limit_mod.sell_buckets[tick]
        owners = set(o.owner for o in bucket.orders)
        for owner in owners:
            engine.claim_limit(owner, tick, True)

    for tick in list(limit_mod.buy_buckets.keys()):
        bucket = limit_mod.buy_buckets[tick]
        owners = set(o.owner for o in bucket.orders)
        for owner in owners:
            engine.claim_limit(owner, tick, False)

    engine.check_invariants("after drain")

    # Conservation check
    total_t0 = sum(w["T0"] for w in engine.wallets.values())
    total_t1 = sum(w["T1"] for w in engine.wallets.values())
    residual_t0 = engine.hook_t0
    residual_t1 = engine.hook_t1

    tol = EPSILON * engine.op_count
    print(f"    Hook T0:   {residual_t0:>18.10f}")
    print(f"    Hook T1:   {residual_t1:>18.10f}")
    print(f"    Ghost T0:  {engine.total_ghost0:>18.10f}")
    print(f"    Ghost T1:  {engine.total_ghost1:>18.10f}")

    # All wallets + hook should == minted
    assert abs(total_t0 + residual_t0 - engine.total_minted_t0) < tol, \
        f"T0 conservation: {total_t0 + residual_t0} vs {engine.total_minted_t0}"
    assert abs(total_t1 + residual_t1 - engine.total_minted_t1) < tol, \
        f"T1 conservation: {total_t1 + residual_t1} vs {engine.total_minted_t1}"

    # No negative balances
    neg = sum(1 for w in engine.wallets.values()
              if w["T0"] < -EPSILON or w["T1"] < -EPSILON)
    assert neg == 0, f"{neg} users have negative balances"

    n_users = len(engine.wallets)
    print(f"    CONSERVATION: ✅ (tol={tol:.2e})")
    print(f"    ALL {n_users} USERS NON-NEGATIVE ✅")

    return residual_t0, residual_t1


def generate_chart(results: list):
    """Generate summary chart."""
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError:
        print("    matplotlib not installed, skipping chart")
        return None

    os.makedirs(CHART_DIR, exist_ok=True)
    fig, axes = plt.subplots(1, 3, figsize=(18, 6))
    fig.patch.set_facecolor('#0d1117')

    def style_ax(ax, title):
        ax.set_facecolor('#161b22')
        ax.set_title(title, color='white', fontsize=11, fontweight='bold', pad=10)
        ax.tick_params(colors='#8b949e')
        for sp in ax.spines.values():
            sp.set_color('#30363d')

    # 1. Operations breakdown per seed
    ax1 = axes[0]
    seeds = [r['seed'] for r in results]
    labels = ['stream', 'limit', 'swap', 'claim_s', 'claim_l', 'cancel_s']
    colors = ['#7ee787', '#58a6ff', '#f778ba', '#d2a8ff', '#ffa657', '#ff7b72']
    bottom = [0] * len(seeds)
    for j, label in enumerate(labels):
        vals = [r['stats'].get(label, 0) for r in results]
        ax1.bar(range(len(seeds)), vals, bottom=bottom, color=colors[j],
                label=label, alpha=0.85)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax1.set_xticks(range(len(seeds)))
    ax1.set_xticklabels([f'Seed {s}' for s in seeds], color='#8b949e')
    ax1.legend(fontsize=7, facecolor='#161b22', edgecolor='#30363d',
               labelcolor='#c9d1d9', ncol=2)
    style_ax(ax1, 'Operations per Seed')

    # 2. Residuals
    ax2 = axes[1]
    res_t0 = [abs(r['residual_t0']) for r in results]
    res_t1 = [abs(r['residual_t1']) for r in results]
    x = range(len(seeds))
    ax2.bar([i - 0.15 for i in x], res_t0, 0.3, color='#7ee787', label='|T0|')
    ax2.bar([i + 0.15 for i in x], res_t1, 0.3, color='#58a6ff', label='|T1|')
    ax2.set_xticks(range(len(seeds)))
    ax2.set_xticklabels([f'Seed {s}' for s in seeds], color='#8b949e')
    ax2.set_ylabel('Residual', color='#8b949e')
    ax2.legend(fontsize=8, facecolor='#161b22', edgecolor='#30363d',
               labelcolor='#c9d1d9')
    style_ax(ax2, 'Hook Residuals After Drain')

    # 3. Scorecard
    ax3 = axes[2]
    total_rounds = sum(r['rounds'] for r in results)
    total_checks = sum(r['checks'] for r in results)
    total_ops = sum(sum(r['stats'].values()) for r in results)
    lines = [
        f"{total_rounds:,} rounds",
        f"{total_ops:,} operations",
        f"{total_checks} invariant checks",
        f"{len(seeds)} random seeds",
        "",
        "ALL PASSED ✅",
    ]
    for i, line in enumerate(lines):
        color = '#7ee787' if i == len(lines) - 1 else '#c9d1d9'
        size = 16 if i == len(lines) - 1 else 11
        ax3.text(0.5, 0.85 - i * 0.13, line, ha='center', va='center',
                fontsize=size, color=color,
                fontweight='bold' if i == len(lines) - 1 else 'normal',
                transform=ax3.transAxes)
    style_ax(ax3, 'Mixed Engine Fuzz')
    ax3.set_xticks([])
    ax3.set_yticks([])

    fig.suptitle('GhostEngine Stress Fuzz — TWAP Streams + Limit Orders',
                 color='white', fontsize=13, fontweight='bold', y=1.02)
    fig.tight_layout()

    path = os.path.join(CHART_DIR, "ghost_engine_fuzz.png")
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0d1117')
    plt.close(fig)
    print(f"\n    Chart saved: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    N_ROUNDS = 3000
    N_STREAMERS = 30
    N_LIMITERS = 30
    N_TAKERS = 10
    SEEDS = [42, 137, 999]

    print("=" * 70)
    print(f"  GHOSTENGINE STRESS FUZZ")
    print(f"  {N_ROUNDS:,} rounds × {len(SEEDS)} seeds = {N_ROUNDS * len(SEEDS):,} total")
    print(f"  {N_STREAMERS} streamers + {N_LIMITERS} limit makers + {N_TAKERS} takers")
    print(f"  Mixed: TWAP streams + limit orders + adversarial patterns")
    print("=" * 70)

    all_results = []

    for seed in SEEDS:
        print(f"\n  ── SEED {seed} ──")
        engine, stream_mod, limit_mod, active_streams, active_limits, stats = \
            run_fuzz(N_ROUNDS, N_STREAMERS, N_LIMITERS, N_TAKERS, seed)

        res_t0, res_t1 = full_drain(engine, stream_mod, limit_mod,
                                     active_streams, active_limits)

        all_results.append({
            'seed': seed,
            'rounds': N_ROUNDS,
            'checks': engine.invariant_checks,
            'stats': stats,
            'residual_t0': res_t0,
            'residual_t1': res_t1,
            'ops': engine.op_count
        })

    total_checks = sum(r['checks'] for r in all_results)
    total_ops = sum(r['ops'] for r in all_results)

    print(f"\n{'=' * 70}")
    print(f"  FINAL VERDICT")
    print(f"  {N_ROUNDS * len(SEEDS):,} rounds | {total_ops:,} ops | {total_checks} invariant checks")
    print(f"  CONSERVATION:  ✅  All tokens accounted for (all seeds)")
    print(f"  SOLVENCY:      ✅  Hook solvent at every checkpoint")
    print(f"  GHOST SYNC:    ✅  Σ moduleGhost == totalGhost")
    print(f"  NO NEGATIVE:   ✅  All user balances ≥ 0")
    print(f"  FULL DRAIN:    ✅  Hook drains correctly after all claims")
    print(f"  CROSS-MODULE:  ✅  Stream + limit coexist without leaks")
    print(f"{'=' * 70}")

    generate_chart(all_results)
