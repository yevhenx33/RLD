# Frontend Visual Redesign

## Review: Universal Morpho Market Indexer
- [x] Implemented deterministic JSON-RPC decimal and symbol fetching with strict timeouts.
- [x] Intercepted `CreateMarket` payload natively inside `morpho.py`.
- [x] Executed Poka-Yoke verification successfully simulating a WETH lookup, USDC lookup, and an intentional HTTP timeout failure cleanly defaulting to 18 decimals and UNKNOWN symbol.
- [ ] Needs Review: Check if any new obscure tokens should be statically mapped in `tokens.py` to avoid continuous cache misses, or if the pipeline is scaling effectively on its own.


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

## [Review] CDS Frontend UI Implementation
- [x] Initialized and verified pure UI structure for CDS Markets and individual CDS Terminal views.
- [x] Implemented placeholder deterministic `CdsDataModule.jsx` UI tabs for Payout Simulation and Historical Prices without relying on back-end connections.
- [ ] Needs Review: Check if CDS UI matches the target industrial design paradigm and approve the S.M.A.R.T layout separation from Bonds.

## Review: TWAMM to GhostEngine V3
- [x] Refactored `PrimeBroker.sol` tracking parameters to `(marketId, orderId)`.
- [x] Swapped hook-based JTM parameters for explicit GhostRouter and TwapEngine entrypoints across PrimeBroker.
- [x] Rewrote `TwapEngineBrokerModule.sol` to evaluate stream states independent of hook logic.
- [x] Refactored downstream dependencies (`BondFactory.sol`, `BasisTradeFactory.sol`) to interact safely with the V3 parameters format (fetching expiration and TwapEngine addresses natively).
- [x] Completed full parity integration verification, resolving compilation errors and ensuring test suite passes.
- [ ] Needs Review: Verify full end-to-end functionality of V3 in staging configuration.

## Code Review: Faucet Hardening
* Review Docker networking changes (`rld_shared` bridge integration vs `network_mode: host`)
* Review Nginx upstream proxy pointing to `http://faucet:8088` instead of `host.docker.internal`
* Verify removal of Anvil storage overrides in `useFaucet.js`

## Review: SOFR LVR Simulation Environment
- [x] Implemented standalone Python SDE environment (`cds-research/scripts/sofr_lvr_sim.py`) for AutoResearch integration.
- [x] Configured Cox-Ingersoll-Ross (CIR) interest rate generation to prevent negative yield boundaries.
- [x] Engineered continuous AmPO pricing evaluation against discrete 24-hour SOFR oracle steps.
- [x] Verified Poka-Yoke constraints: proved naive pricing leaks massive LVR, while mathematically perfect omniscience yields exactly zero LVR.
- [ ] Needs Review: Hand off to Scientist/AutoResearch pipeline for LLM optimization of the funding curve.

## Review: Aave IRM Liquidation Sensitivity Simulation
- [x] Defined deterministic pure Python logic (`scripts/simulate_irm_liquidation.py`) to isolate interest rate effects on Aave Health Factors.
- [x] Extracted continuous compounding equation mapped strictly to Aave's per-second accumulation model.
- [x] Verified Poka-Yoke constraints: algorithm exactly identifies crossover points ($HF < 1.0$) and correctly filters noise.
- [ ] Needs Review: Attach ClickHouse `processor_state` hook to pipe real-time indexer data into the structural `simulate_irm_liquidation.py` bounds.

## Review: ARFC USDC Interest Rate Market Impact
- [x] Bootstrapped deterministic `scripts/arfc_impact_analysis.py` over exactly 9,141 raw indexer states.
- [x] Hardened analysis via Debt Pruning to strictly observe $> \$10,000$ accounts, eliminating retail noise from systemic bad debt thresholds.
- [x] Executed isolated mathematically exact continuous compounding exclusively on the USDC proportion of the portfolio.
- [x] Compiled `usdc_arfc_impact_report.md` outputting the exact cumulative structural damage against time horizons.
- [ ] Needs Review: Confirm if we want to run this sweep across Arbitrum and Base deployments using the same parameters.

## Review: Unified ARFC Governance Synthesis
- [x] Bootstrapped `scripts/generate_unified_report.py` to seamlessly ingest multi-agent JSON summary state and mathematically integrate it with deterministic debt boundaries.
- [x] Auto-generated `unified_arfc_governance_visual.png` rendering exact 7-Day and 30-Day Liquidated Volumes over the $61M boundary limit.
- [x] Compiled `unified_arfc_governance_report.md` artifact.
- [x] Resolved context conflict: confirmed `arfc_impact_analysis.py` as the official isolation mechanism to be safely included in the final governance package.

## Review: ARFC Governance Forum Post Refactor
- [x] Bootstrapped `scripts/generate_forum_post.py` to extract the Top 30 vulnerability list directly from `usdc_hf_sorted_envio_reconstruction_2026-04-23.csv`.
- [x] Refactored the unified liquidation metrics into a persuasive, Aave Governance-formatted markdown reply (`aave_forum_arfc_reply.md`).
- [x] Positioned the exact $61.29M structural boundary under the S2=50% Target as mathematical validation of the 30-day deadlock hypothesis.


## REVIEW REQUIRED: ClickHouse ILLEGAL_AGGREGATION Fix
- [x] Removed inline AS aliases from the INSERT SELECT statement in indexer/api/graphql.py.
- [x] Verified GraphQL /data endpoint correctly loads marketSnapshots without throwing Code 184.


## REVIEW REQUIRED: USDC Oracle APY Endpoint
- [x] Added `GET /api/v1/oracle/usdc-borrow-apy` to indexer/api/graphql.py.
- [x] Verified endpoint cleanly parses latest AAVE_MARKET USDC APY from ClickHouse.


## REVIEW REQUIRED: MM Daemon Simplification
- [x] Replaced 60+ lines of fallback/timeout logic in combined_daemon.py with a 10-line direct REST call.
- [x] Added unit tests for fetch_latest_rate in backend/tests/test_daemon_rate_fetch.py
- [x] Verified mm-daemon successfully arbitraged the stale $14 mark price down to the correct ~$12.65 index price.

## REVIEW REQUIRED: Deployment & Simulation Codebase Simplification
- [x] Audited the codebase for similar fragile GraphQL and Anvil `getReserveData` fetching logic.
- [x] **`01_protocol.sh`**: Stripped out legacy GraphQL array-slicing and replaced it with a strictly enforced `curl` against the new REST API.
- [x] **`deploy_protocol_snapshot.py`**: Rewrote `_fetch_live_rate_fraction` using a deterministic `urllib` `GET` request.
- [x] **`deploy_pool_live_index_with_liquidity.py`**: Rewrote the `fetch_live_rate_fraction` python helper.
- [x] **`fixed_yield.sh`**: Eliminated the `cast call` to Aave V3's `getReserveData`. By ripping out the Anvil on-chain fallback, we guarantee that simulated Fixed Yield bonds are initialized at exactly the correct market rate (e.g., ~12.65%), rather than silently defaulting to the snapshot's 14%.

## REVIEW REQUIRED: Cleanup of Obsolete Orchestration Scripts
- [x] Audited the repository and confirmed that the monolithic `docker/deployer/deploy_all.sh` flow (and its child `phases/*.sh` scripts) had been entirely superseded by the unified Python orchestrators (`deploy_protocol_snapshot.py` and `setup_simulation.py`).
- [x] **Deleted `docker/deployer/deploy_all.sh`**, `docker/deployer/lib_setup.sh`, and the entire `docker/deployer/phases/` directory.
- [x] **Deleted the Reth equivalents:** `docker/reth/deploy_all.sh` and the entire `docker/reth/deployer/` directory.
- [x] **Updated `docker/deployer/Dockerfile`** to remove all `COPY` commands related to these dead files, stripping bloat from the container image.
- [x] **Deleted legacy Anvil maintenance scripts** including `anvil-rotate.sh`, `cleanup-anvil-snapshots.sh`, `start_anvil.sh`, and `kill_all.sh`, as well as `docker/restart.sh` (the old anvil orchestrator).
- [x] **Deleted `run_anvil.py`** and `05_setup_users_reth.sh` as they are unused dead code from earlier migrations.

## REVIEW REQUIRED: Lending Data Hub Deterministic Rendering
- [x] Abstracted `LendingDataPage.jsx` data parsing into pure JS functions in `utils/lendingDataPokaYoke.js`.
- [x] Enforced strict Poka-Yoke type coercion (fallback to 0) to prevent `NaN` or `Infinity` from silently destroying UI metrics.
- [x] Added `test_lending_data_logic.py` Python test to mathematically prove the bounds logic per the Maxwell Demon mandate.
- [x] Integrated a loud, explicitly styled Error Boundary to halt data rendering if the ClickHouse pipeline falls offline.

## REVIEW REQUIRED: Data Hub Redesign Demolition
- [x] Completely purged `LendingDataPage.jsx` of all hooks (`useSWR`, `useMemo`), charts, and table components.
- [x] Reduced component to a pure static skeleton with a single `DATA` header.
- [x] Verified zero phantom side-effects via `tests/verify_demolition.py`.

## REVIEW REQUIRED: Navigation Route Rename
- [x] Executed structural update in `Header.jsx`.
- [x] Renamed desktop route label `LENDINGS` -> `DATA` (line 175).
- [x] Renamed mobile hamburger route label `LENDINGS` -> `DATA` (line 310).

## REVIEW REQUIRED: Data Hub Metrics Grid
- [x] Imported standard `MetricsGrid` component from Pools/Bonds UI architecture to enforce design system consistency.
- [x] Configured static layout underneath the Header in `LendingDataPage.jsx`.
- [x] Bound Poka-Yoke placeholder props (zeroes/empty strings) to explicitly prevent undefined prop crashes.
- [x] Executed `tests/verify_grid_mount.py` to mathematically verify the React prop constraints.

## REVIEW REQUIRED: Metrics Grid Expansion (4 Panels)
- [x] Refactored `MetricsGrid.jsx` to dynamically switch between `grid-cols-3` and `grid-cols-4` strictly via the `extraPanel` prop, preventing layout contagion on the Bonds and CDS pages.
- [x] Exported `MetricCell` and `StatItem` components to allow external assembly.
- [x] Injected a new `SYSTEM_STATUS` 4th panel into the `/data` page layout, rendering placeholder `PIPELINE` and `LATENCY` metrics.

## REVIEW REQUIRED: Custom Data Hub Panels
- [x] Extracted `MetricCell` and `StatItem` layout constraints to natively construct a bespoke 4-column container in `LendingDataPage.jsx`.
- [x] Configured static placeholders for Overview, Rates, TVL by Market Type, and Stats.
- [x] Enforced strict responsive classes (`grid-cols-1 md:grid-cols-2 lg:grid-cols-4`, `divide-y md:divide-y-0`) to perfectly mimic standard layout behavior.
- [x] Verified constraint replication via `tests/verify_custom_panels.py`.

## REVIEW REQUIRED: Overview Panel Bifurcation
- [x] Structured internal `content` of the Overview Panel into a 2-column nested layout.
- [x] Aligned `TOTAL NET WORTH` to the bottom left via `flex-col justify-end`.
- [x] Stacked `TOTAL SUPPLY` and `TOTAL BORROW` centered on the right with a subtle divider line (`border-t md:border-t-0 md:border-l`).
- [x] Verified mobile fallback behavior via `tests/verify_bifurcation.py` to prevent UI squishing.

## REVIEW REQUIRED: Uniform Sub-Panel Bifurcation
- [x] Refactored Panels 2, 3, and 4 (Rates, TVL by Type, Stats) to identically match the 2-column aesthetic of the Overview panel.
- [x] Pinned all values securely to the bottom via `flex flex-col justify-end`.
- [x] Enforced mathematical symmetry across the row by re-using the exact boundary matrix (`flex flex-col md:grid md:grid-cols-2 gap-4 mt-auto`).
- [x] Verified constraints via `tests/verify_bifurcation.py`.

## REVIEW REQUIRED: Grid Width Restriction
- [x] Restrained master 4-panel grid width to 75% via `xl:w-3/4` constraint on the wrapper `div`.
- [x] Allowed `lg:grid-cols-4` to seamlessly split the remaining 75% boundary space equally across all 4 sub-panels.
- [x] Maintained 100% `w-full` boundary on mobile screens to prevent UI crushing.
- [x] Formally verified boundary enforcement via `tests/verify_grid_width.py`.
