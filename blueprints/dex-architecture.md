# DEX Architecture Blueprint

This blueprint defines the architecture, invariants, and review standard for the RLD DEX contracts under `contracts/src/dex`. Treat it as the source of truth for future work on the Ghost Router, TWAP engine, limit engine, TWAP oracle accumulator, and hub/spoke accounting model.

## Scope

The DEX surface is intentionally small and must stay understandable:

- `contracts/src/dex/GhostRouter.sol`
- `contracts/src/dex/TwapEngine.sol`
- `contracts/src/dex/LimitEngine.sol`
- `contracts/src/dex/TwapEngineLens.sol`
- `contracts/src/dex/interfaces/*`
- DEX tests under `contracts/test/dex`
- Broker integration points that call DEX contracts, especially TWAMM valuation, cancellation, liquidation, and force-settle paths.

Do not treat this blueprint as covering the ClickHouse indexer, deployment stack, or broker solvency model except where they directly depend on DEX behavior.

## Architecture Summary

The DEX uses a hub/spoke model:

- `GhostRouter` is the hub. It owns custody, market registration, oracle access, fee accrual, Uniswap V4 fallback execution, and cross-engine routing.
- `TwapEngine` is a spoke. It owns TWAMM stream state, rate accrual, order accounting, auction clearing, and force settlement accounting. It does not custody tokens.
- `LimitEngine` is a spoke. It owns limit-order bucket state and active-pool accounting. It does not custody tokens.
- Engines command token movement through router `pullMarketFunds` and `pushMarketFunds`.
- Engines expose ghost inventory through `IGhostEngine` so the router can aggregate and route liquidity.

Mental model:

```text
User / Solver / Taker
        |
        v
  GhostRouter  <---->  Uniswap V4 PoolManager
   |   |   |
   |   |   +-- oracle source + native accumulator
   |   |
   |   +------ TwapEngine: streaming inventory + earnings factors
   |
   +---------- LimitEngine: triggered buckets + active pools
```

The router is custody and execution. Engines are deterministic accounting and entitlement tracking.

## Core Terms

- `marketId`: `bytes32` identifier derived from a hookless Uniswap V4 `PoolKey`.
- `zeroForOne`: direction flag where `true` means sell token0 for token1, and `false` means sell token1 for token0.
- Ghost inventory: sell-side inventory that has accrued or activated but has not yet been cleared into buy-token proceeds.
- Taker intercept: router path where taker input is matched against opposite-direction ghost before AMM fallback.
- Global netting: router path where opposing ghost from all engines is crossed at the market spot price.
- Earnings factor: cumulative per-direction accounting factor used to convert an order's rate or shares into claimable proceeds.

## Router Responsibilities

`GhostRouter` must remain the single custody layer for DEX funds.

It is responsible for:

- Registering and deregistering engines.
- Initializing hookless markets.
- Holding token balances for all registered engines.
- Pulling user/solver/taker funds into the vault.
- Pushing refunds, claims, and clear proceeds out of the vault.
- Reading market spot price from an external oracle or Uniswap V4 spot.
- Maintaining the native price accumulator used by `observe`.
- Running taker swaps through the three-layer routing path.
- Charging and claiming per-market taker input fees.
- Executing engine-requested AMM settlements through Uniswap V4.
- Providing a router-level `forceSettleEngine` entrypoint for liquidation/emergency paths.

The router must not:

- Store per-order DEX accounting.
- Infer engine-specific entitlement rules.
- Let unregistered contracts command vault transfers.
- Catch and ignore settlement commit failures that can change accounting.
- Create market aliases or compatibility IDs without an explicit migration plan.

## Engine Responsibilities

Each engine must implement `IGhostEngine`:

- `syncAndFetchGhost(marketId)`
- `applyNettingResult(marketId, consumed0, consumed1, spotPrice)`
- `takeGhost(marketId, zeroForOne, amountIn, spotPrice)`

An engine may add user-facing lifecycle methods, but its router hooks must obey the same semantics:

- `syncAndFetchGhost` may mutate engine state to accrue or activate inventory before reporting ghost balances.
- `applyNettingResult` must deduct consumed sell-side inventory and credit buy-token proceeds.
- `takeGhost` must deduct the opposite-direction ghost consumed by a taker and credit taker input as proceeds.
- Engine hooks must be `onlyRouter`.
- User-facing state-mutating methods should be `nonReentrant`.
- Engine accounting must be deterministic from its stored state and router-provided price.

Engines must not custody tokens directly. Any token movement must go through the router.

## Swap Flow

Router taker swaps follow this order:

1. Validate the market.
2. Pull taker input into the router vault.
3. Collect configured input fee.
4. Read spot price.
5. Run global ghost netting.
6. Run taker intercept against remaining opposite-direction ghost.
7. Route any remaining input through vanilla Uniswap V4.
8. Enforce `amountOutMinimum`.
9. Transfer output to the taker.
10. Write a native oracle observation.

This order is important:

- Fees apply to taker input before ghost/AMM execution.
- Netting runs before intercept so passive internal flow is crossed before taker inventory is consumed.
- Slippage is checked after all sources of output are aggregated.
- Oracle observations are written after successful state transitions.

## Global Netting Invariant

Global netting is a settlement commit, not an optional best-effort action.

Required behavior:

- `syncAndFetchGhost` failures may be isolated by skipping the failing engine and emitting `EngineCallFailed`.
- `applyNettingResult` failures must revert the whole netting operation.
- If an engine contributes to aggregate ghost balances, its commit must succeed or every prior commit in that transaction must roll back.
- Do not catch and ignore `applyNettingResult` failures unless a two-phase commit or equivalent atomicity model is introduced.

Reason: partial netting can credit one side of a cross without deducting the matching counterparty inventory, which risks double claims or vault insolvency.

## TWAP Engine Architecture

`TwapEngine` represents long-running streams as scheduled sell rates rather than as per-block fills.

State model:

- `states[marketId]` stores aggregate ghost and timestamps.
- `streamPools[marketId][zeroForOne]` stores current sell rate, earnings factor, and epoch schedule maps.
- `streamOrders[marketId][orderId]` stores market-scoped order metadata.
- `epochEventBitmap` and `epochWordBitmap` index non-empty start/end epochs so accrual can jump over idle intervals.

Submit flow:

1. Pull sell-side funds through the router.
2. Accrue market state to now.
3. Schedule sell-rate start at the next epoch.
4. Schedule sell-rate end at expiration.
5. Store order metadata with the current earnings-factor baseline.

Accrual flow:

1. If first touch, initialize timestamps and return.
2. Walk only event epochs between `lastUpdateTime` and `block.timestamp`.
3. Accrue ghost for each time segment using current sell rates.
4. Cross each event epoch by settling departing flow, snapshotting factors, and mutating rates.
5. Accrue the tail segment and update `lastUpdateTime`.

Critical TWAP invariant:

- Before any active sell-rate decrease, settle that direction's outstanding ghost.

This applies to:

- Epoch expiry, including partial expiry when other same-direction streams continue.
- Mid-stream cancellation, including partial cancellation when other same-direction streams continue.
- Last-order cancellation.
- Force settlement.

Reason: aggregate ghost was accrued while the departing flow was active. If the rate is reduced before crystallizing that ghost into earnings, proceeds can be misallocated to remaining streams or become unclaimable for expired orders.

## TWAP Claims, Cancellations, and Earnings

Claims:

- Accrue market state first.
- Preview settlement using committed state.
- Compute earnings from `sellRate * earningsFactorDelta`.
- Update the order's `earningsFactorLast`.
- Push buy-token proceeds through the router.

Cancellations:

- Accrue market state first.
- Validate order existence and ownership.
- If started and not expired, settle ghost before reducing active rate.
- Claim pending earnings.
- Refund only unsold sell-side principal.
- Remove scheduled start/end rate deltas.
- Delete the order.

Expired orders:

- Have no sell-token refund.
- Must still be able to claim proceeds earned before expiration.
- Must not receive proceeds from flow that starts at the same epoch after they expire.

## TWAP Auction Clearing

`clearAuction` lets solvers buy directional ghost at a time-dependent discount.

Required behavior:

- Accrue before computing available ghost.
- Compute discount from `lastClearTime`, capped by `maxDiscountBps`.
- Pull solver payment before pushing ghost inventory.
- Deduct consumed ghost and record earnings for sellers.
- Revert if discounted payment is zero.

Known design caveat:

- `lastClearTime` is market-wide, so clearing one direction resets discount timing for the other direction. If direction-specific Dutch-auction fairness is required, introduce per-direction clear timestamps and tests.

## Limit Engine Architecture

`LimitEngine` keeps dormant limit orders in price buckets and activates them on demand.

Current model:

- Pending orders are grouped by `(marketId, direction, triggerPrice)`.
- Once a bucket activates, inventory is one-way merged into a global active pool for that direction.
- Active orders share `earningsFactor` and `depletionFactor` accounting.
- Activation is demand-triggered by router hooks, not by autonomous keepers.
- Activated buckets do not deactivate if price reverts.

Trigger semantics:

- `zeroForOne=true`: sell token0, activate when spot price is greater than or equal to trigger.
- `zeroForOne=false`: sell token1, activate when spot price is less than or equal to trigger.

Known gas caveat:

- Activation currently scans the sparse `bucketPrices` array for each market/direction.
- Gas scales with historical unique trigger-price count.
- Do not assume this is production-scalable for adversarial fragmented triggers.
- A future optimization should use a range index, bitmap, heap, or keeper-assisted activation queue.

The current instruction is to skip changing limit-engine logic unless explicitly requested.

## Oracle Architecture

`GhostRouter` supports two spot sources:

- External oracle: `IGhostOracle(market.oracle).getSpotPrice(marketId)`.
- Uniswap V4 spot: `poolManager.getSlot0(poolId)` converted from `sqrtPriceX96` into token1/token0 price scaled to `1e18`.

All DEX clearing paths use the router spot price.

Native accumulator:

- Stores per-market observations as `priceCumulative = sum(price * elapsedSeconds)`.
- Uses a fixed-size ring buffer.
- Skips same-block writes.
- Supports `observe(marketId, secondsAgos)` for cumulative reads.
- Resets when oracle mode/source changes.
- Has per-market max staleness.
- Extrapolates only within the freshness window.
- Exposes `pokeOracle(marketId)` so keepers can maintain heartbeat observations between swaps.

Oracle invariants:

- Do not mix observation history across price-source changes.
- Do not backfill long idle windows with a newly manipulated current spot.
- Do not silently accept zero prices.
- External oracle adapters should provide freshness, decimals normalization, and market/source validation before returning a price.

Operational requirement:

- Markets that rely on `observe` need a heartbeat keeper that calls `pokeOracle` more frequently than `oracleMaxStaleness`.
- If the heartbeat is missed, the accumulator should reset or `observe` should revert rather than fabricating history.

## Force Settlement Architecture

TWAP force settlement crystallizes ghost into AMM proceeds so liquidation, cancellation, and emergency accounting can value orders accurately.

Canonical path:

1. Caller invokes `GhostRouter.forceSettleEngine(engine, marketId, zeroForOne)`.
2. Router validates the engine is registered and the market exists.
3. Router calls engine `forceSettle`.
4. Engine accrues state and calls router `settleGhost`.
5. Router swaps ghost through vanilla V4 and returns `amountOut`.
6. Engine records `amountOut` into the correct earnings factor and zeros directional ghost.

Do not call `TwapEngine.forceSettle` directly from external integrations. It is router-gated and should revert outside the router path.

Broker liquidation integrations must call the router-level entrypoint.

## Vault and Accounting Invariants

Future DEX changes must preserve:

- Router token balances cover all modeled engine liabilities.
- Registered engines are the only contracts allowed to pull/push market funds.
- User claims and refunds are paid from router custody.
- Engine accounting must not create claims without corresponding router-held assets or executed swaps.
- Netting, intercept, clear, settle, cancel, and claim paths must update accounting before or atomically with external token transfers.
- No order can claim earnings before its start epoch.
- No order can claim earnings after its expiration snapshot.
- Cancellations cannot refund more sell-side principal than remains unsold.
- Taker intercept must never consume more input than the taker budget.
- Tiny fills must round taker input consumed up where needed to prevent free dust extraction.

## Access Control Invariants

Router:

- `registerEngine` and `deregisterEngine` are owner-only.
- Market initialization and oracle mode updates are owner-only.
- Fee-controller assignment is owner-only.
- Fee updates and fee claims require owner or configured market fee controller.
- `pullMarketFunds`, `pushMarketFunds`, and `settleGhost` are engine-only.
- `forceSettleEngine` is permissionless but only targets registered engines.

Engines:

- Router hooks are router-only.
- User order lifecycle methods validate ownership where required.
- User-facing state-changing methods should be non-reentrant.

Important deregistration warning:

- Removing an active engine can strand user funds because that engine can no longer command router transfers.
- Before deregistering, drain, migrate, settle, or explicitly document the active-liability state.

## Security Review Checklist

Before approving DEX changes, verify:

- Market token ordering and hookless pool requirements are preserved.
- Price scale remains token1 per token0, scaled to `1e18`.
- Oracle source changes reset accumulator history.
- TWAP sell-rate decreases settle accrued ghost first.
- Global netting commits are atomic.
- Intercept and netting cannot overconsume engine inventory.
- Engine failures cannot create partial settlement or inconsistent accounting.
- Slippage checks remain after total output aggregation.
- Payment is pulled before payout in solver clear paths.
- Fee accrual cannot steal from engine liabilities or claim more than accrued.
- Refunds and claims use the correct token direction.
- Wrong-market order IDs cannot access another market's state.
- State-mutating external calls are either non-reentrant or router-gated with a clear reentrancy argument.
- `forceSettleEngine` cannot target unregistered engines.
- Tests cover partial expiry, partial cancellation, atomic netting failure, oracle staleness, and force settlement.

## Gas Review Checklist

High-impact gas areas:

- Router loops over `approvedEngines`.
- TWAP accrual over event epochs.
- Limit activation over unique trigger prices.
- Ring-buffer observation lookup.
- Uniswap V4 fallback unlock/callback path.

Optimization guidance:

- Prefer reducing loop cardinality over micro-optimizing arithmetic.
- Avoid adding new per-swap scans over unbounded user-created arrays.
- Keep TWAP accrual event-indexed; do not reintroduce linear idle-interval loops.
- Consider per-market/per-direction active-engine indexes if engine count grows.
- Consider a range-indexed limit trigger data structure before production scale.
- Use `mulDiv` for high-range price math and avoid overflow-prone multiply/divide patterns.
- Benchmark direct pool routing versus router routing whenever changing swap path gas.

## Testing Standard

After DEX edits, run at minimum:

```bash
cd contracts
forge test --match-path "test/dex/*.t.sol"
```

If broker liquidation, TWAMM valuation, or market deployment integrations change, also run:

```bash
cd contracts
forge test --match-path "test/rld/*.t.sol"
forge test --match-path "test/integration/*.t.sol"
```

For focused TWAP changes:

```bash
cd contracts
forge test --match-contract TwapEngineUnitTest
forge test --match-contract TwapEngineStatefulInvariantTest
```

For router execution/gas changes:

```bash
cd contracts
forge test --match-contract GhostRouterIntegrationTest
forge test --match-contract RouterExecutionProfilesGasBenchTest
```

Required test categories:

- Unit tests for new accounting branches.
- Integration tests across router and engine.
- Revert-path tests for access control and atomicity.
- Fuzz tests for bounded refunds, bounded fills, and price ranges.
- Stateful invariants for router balance coverage and order metadata consistency.
- Gas benchmarks for router/direct-pool tradeoffs and limit trigger fragmentation.

## Operational Monitoring

Index and alert on:

- `EngineCallFailed`
- `GlobalNettingExecuted`
- `GhostTaken`
- `GhostSettledViaAMM`
- `GhostSettled`
- `ForceSettled`
- `EngineForceSettleRequested`
- `OracleObservationReset`
- `OracleObservationWritten`
- `OracleMaxStalenessUpdated`
- `AuctionCleared`
- `LimitBucketActivated`

Recommended alerts:

- Repeated `EngineCallFailed` for one engine/market.
- No oracle heartbeat before `oracleMaxStaleness`.
- Large ghost inventory persisting longer than expected.
- Repeated force settlement on the same market/direction.
- Router balance below modeled liabilities.
- Limit bucket count growth by market/direction.
- Router gas premium exceeding solver route threshold.

## Future Simplification Path

Preferred simplifications:

- Keep router as custody/execution only.
- Keep engines as accounting-only modules.
- Use one canonical force-settle path through the router.
- Keep netting atomic unless a formal multi-phase settlement protocol is introduced.
- Move complex oracle freshness logic into oracle adapters where possible, while keeping accumulator source-change resets in the router.
- Replace limit-engine full scans with a proper executable-trigger index.
- Consider splitting fee controller, oracle controller, and engine controller roles if governance needs finer permissioning.

Avoid:

- Adding compatibility shims for unlaunched DEX behavior.
- Duplicating order state between router and engines.
- Introducing background assumptions without keeper runbooks.
- Letting convenience paths bypass router custody or engine accounting.

## Handoff Standard

Every DEX final response should include:

- What changed.
- Which contracts and tests were touched.
- What was verified.
- Any intentionally skipped area, especially `LimitEngine`.
- Any remaining risk or operational requirement, especially oracle heartbeat and solver routing.

