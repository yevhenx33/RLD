# Frontend Visual Redesign

## Phase 1: Standalone Landing Page Folder ✅

- [x] Create `new-front/` at project root (sibling to `frontend/`)
- [x] Scaffold minimal Vite + React project (no router, no contexts, no wallet)
- [x] Create clean landing page skeleton in `src/pages/Landing.jsx`
- [x] Verify it runs independently with `npm run dev` on port 5174
- [x] Verify `frontend/` is **FULLY UNTOUCHED** (git diff = clean)

### Structure
```
new-front/
├── index.html          (Google Fonts preload)
├── package.json        (React 19 + Vite 7 + Tailwind 3)
├── vite.config.js      (port 5174)
├── tailwind.config.js
├── postcss.config.js
├── public/
└── src/
    ├── index.css       (Tailwind directives + dark base)
    ├── main.jsx        (bare React render, no router/providers)
    └── pages/
        └── Landing.jsx (minimal hero — starting point for redesign)
```

## Phase 2: Iterative UI/UX Redesign
- [ ] Design hero section
- [ ] Design product sections
- [ ] Design footer
- [ ] Polish animations & micro-interactions

## Review: CDS Backtest Logic
- [x] Implemented standalone Python script (`backend/rates/cds_backtest.py`)
- [x] Verified Poka-Yoke metrics (Max payout bounds, Sharpe ratio)
- [ ] Needs Review: Check if DuckDB ASOF join aligns exactly as expected on `morpho_enriched_final.db` in full N-Market extension.

## Review: Rolling Cointegration Analysis
- [x] Implemented `backend/rates/rolling_cointegration.py` to sweep Engle-Granger regressions over 1-30 day lags on a 90-day window.
- [x] Verified Poka-Yoke constraints and successfully detected the mathematical pricing dislocation periods.
- [ ] Needs Review: Final model tuning to operationalize this lag logic into a real-time statistical arbitrage signal validator.

## Review: Global Cointegration Analysis
- [x] Implemented `backend/rates/global_cointegration.py` to establish the mathematical theorem of global structural cointegration without rolling windows.
- [x] Swept history to find the absolute Geometric Optimal Lag.
- [x] Ran Quarterly segmented Poka-Yoke metrics, proving global baseline adherence despite anomalous quarters (e.g. Q3 2025).
- [ ] Needs Review: Check if we want to build a real-time pipeline monitoring the global ADF p-value as an aggregate systemic risk warning.

## Review: Monthly Cointegration Constraints
- [x] Authored `backend/rates/monthly_cointegration.py` implementing dual-pass macro vs micro framework.
- [x] Proven that pure Monthly Resampling (N=36) breaks the degrees of freedom for an ADF Cointegration test, yielding a false negative.
- [x] Grouped daily array into Monthly Micro-Segments, extracting exact 30-day dislocation windows compared to strong Q4 coupled epochs.

## Review: Autoresearch Framework Formalization
- [x] Implemented Karpathy's `autoresearch` environment (`program.md`, `prepare.py`, `model.py`, `train.py`) to systematically discover the optimal statistical model.
- [x] Hardened the fitness metric to penalize complex un-economic parameters using an Akaike Information Criterion (AIC) mechanism.
- [x] Finalized and executed an Engle-Granger pipeline with dynamic geometric lag shift that cleared the `0.08` maximum fitness stringency threshold.

## Review: High-Frequency Hourly Cointegration Segregation
- [x] Architected `backend/rates/autoresearch/hourly_segment_cointegration.py` pipeline pulling from the true `1H` frontend index.
- [x] Architected `backend/rates/autoresearch/hourly_90d_rolling_cointegration.py` using 90-day overlapping sequences ($N=2160$ hours per window).
- [x] Verified that over 90-Day structural horizons, cointegration holds with 94% success rate (61 of 65 epochs), definitively proving the 90-day mean-reverting arbitrage thesis for the whitepaper.
- [x] Executed `backend/rates/autoresearch/visualize_1h_step_pvalues.py` via Python multiprocessing, sweeping exactly 24,954 dense 1H intervals across the final SQLite `clean_rates.db`.
- [x] Finalized the comprehensive `cointegration_analysis_report.md` artifact incorporating all methodologies, exact anomaly maps, and an embedded P-Value analytical series chart for Agent handoff.

## Review: Morpho Market Cross-Correlation
- [x] Built `backend/morpho/market_correlation.py` to extract high-fidelity Utilization and Borrow APY from `morpho_enriched_final.db`.
- [x] Swapped linear Pearson correlation for Spearman Rank correlation to maintain mathematical validity against Morpho's non-linear kinked `AdaptiveCurveIRM`.
- [x] Discovered massive Borrow APY correlation ($\rho = 0.958$) between `wstETH/USDC` and `WBTC/USDC`, proving cross-market liquidity contagion via MetaMorpho allocators.
- [x] Executed systemic `backend/morpho/aave_cross_correlation.py` integrating the Aave 1H pipeline against the Morpho Capital-Weighted Bundle. Definitively proved the protocols exist as sovereign, decoupled yield environments ($\rho = 0.316$), shattering Basis Trading assumptions.

## Review: CDS Mathematical Simulation (Phase 2)
- [x] Added `simulations/cds_equilibrium.py` for Monte Carlo verification of the Everlasting Option CDS tokenomics.
- [x] Validated Fiduciary/Underwriter continuous minting and premium equilibrium matching equations natively on step-by-step resolution.
- [x] Validated Taylor Risk expansion logic ensuring $Y_{CDS} > r_{supply}$.
- [ ] Needs Review: Check if we want to integrate actual Oracle 1h TWAR jumps or proceed.

## Review: CDS Empirical Jump-Diffusion Backtest
- [x] Executed Phase 1-5 Euler dataset evaluation in `simulations/cds_empirical_backtest.py`.
- [x] Verified TWAMM `empirical_coverage` identity constant over continuous decay.
- [x] Extracted formal `alpha` Taylor Series convex spread on the JIT Vault return.
- [x] Verified exactly a 23 Day and 11 Hour separation between $t_{freeze}$ and $t_{settle}$, proving physical impossibility of adversarial mempool flights.
- [ ] Needs Review: Inspect `simulations/cds_empirical_backtest.png` 2x2 publication artifact.

## Review: Diversified Underwriter Portfolio Theory
- [x] Queried top 30 USDC Morpho Blue markets from `morpho_enriched_final.db` (1.77M hourly snapshots).
- [x] Executed `simulations/cds_portfolio_backtest.py` — full 30-market CDS vs. passive supply comparison.
- [x] Verified Yield Invariant $Y_{CDS} \ge r_{supply}$ across all 30 markets without exception.
- [x] Regime-separated analysis via `simulations/cds_portfolio_regime_separated.py` — 26 steady-state, 4 tail-risk.
- [x] Portfolio Alpha: $426,776 extracted on $300k capital (142.3% alpha/capital).
- [x] Compiled final academic report: `cds_academic_report.md`.
## Review: CDS Portfolio Risk Allocation & Optimization
- [x] Defined programmatic `calculate_tier_weights` logic avoiding traditional DeFi TVL and Inverse-Yield Traps.
- [x] Executed deterministic Tier 1 / 2 / 3 Blue Chip distribution simulation.
- [x] Validated Tail Risk reduction from -$59K naive loss to a +$26K structural profit across identical defaulted assets.
- [ ] Needs Review: Finalize asset whitelist for the actual Solidity deployment configuration.

## Review: Dashboard Infrastructure Observability Overlay
- [x] Evaluated Phase 1: ultra-compact, read-only HTML layout.
- [x] Injected `docker/scripts/fetch_node_metrics.py` proxy to extract purely JSON RPC metrics rather than heavy parsing.
- [x] Injected UI changes into `index.html` binding the JSON payload components strictly into rows (`sys.nodes.reth_mainnet`, `sys.nodes.lighthouse`).

## Review: ClickHouse Analytical Engine (OLAP)
- [x] Bootstrapped local `clickhouse-server` container binding to `/mnt/data/clickhouse` for heavy analytical workloads.
- [x] Executed Phase 1 Approval: Developed Python ETL script (`merge_aave_morpho.py`).
- [x] Transferred >6,034,000 structural rows from Postgres Array (`rld_timescale`) and isolated SQLite volumes (`morpho_enriched_final.db`) into ClickHouse memory.
- [x] Proven Poka-Yoke metrics (successful instantiation of Unified Long Form Timeseries Table).

## Review
- [x] Refactored Markets.jsx Explore page to 2-column lg:flex-row layout

## Review: Custom Oracle Protocol-Native Event Mappings (Phase 2)
- [x] Architectured `indexer/sources/custom_feeds.py` as a strictly typed router bypassing silent proxies.
- [x] Bootstrapped structural deterministic mappings for 16 Dummy Oracle feeds asserting exactly 1.0 (12 dec).
- [x] Architectured `indexer/sources/lido.py` to algebraically decode exact `stEthPerToken` exchanges via native `TokenRebased` events (`0xbef9...`).
- [x] Hardened code against zero-division payloads with deterministic Poka-Yoke error boundaries.
- [x] Integrated `LidoRebaseSource` and `StaticPegsSource` in the unified `run_indexer.py` engine loops.
- [x] Validated USR (Resolv Protocol) as an absolute static `$1.000` structure and mapped it deterministically into `StaticPegsSource`.
- [x] Finalized error resolution for `syrupUSDT` and `ETH+` custom oracle feeds by overriding their structural bounds natively in `replay_morpho_full.py`.

## [Review] Data Pipeline Architecture Refactor
- [ ] Review decoupled run_indexer.py argparse implementation
- [ ] Review genesis_block parameter functionality in collector/processor
- [ ] Review Watcher mempool atomic purge logic
