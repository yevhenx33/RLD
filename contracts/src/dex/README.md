# TWAMM V3 (Hub/Spoke) Documentation

This document explains the `twamm_v3` system step by step, from deployment to order settlement.
It is written against the current implementation in this folder:

- `GhostRouter.sol` (hub/vault/router)
- `TwapEngine.sol` (TWAMM spoke engine)
- `TwapEngineLens.sol` (read-only helper)
- `interfaces/*`

## 1) Mental Model

`twamm_v3` splits responsibilities into two layers:

- **Hub (`GhostRouter`)**
  - Custodies market funds.
  - Routes taker swaps through three layers:
    1. global ghost netting across engines,
    2. taker intercept against remaining ghost,
    3. Uniswap V4 fallback.
  - Provides market-level spot price via oracle mode.

- **Spoke (`TwapEngine`)**
  - Maintains TWAMM stream state and O(1)-style accounting.
  - Never escrows user funds itself.
  - Commands vault transfers through router (`pullMarketFunds` / `pushMarketFunds`).

Think of the engine as deterministic accounting + entitlement tracking, and the router as custody + execution.

## 2) Key Terms

- **Market**: identified by `bytes32 marketId` (derived from V4 pool id).
- **Direction (`zeroForOne`)**:
  - `true`: sell token0, buy token1.
  - `false`: sell token1, buy token0.
- **Ghost balance**:
  - Accrued not-yet-cleared sell inventory.
  - `streamGhostT0` for token0 ghost, `streamGhostT1` for token1 ghost.
- **Sell rate**:
  - Order flow per second, scaled by `RATE_SCALER = 1e18`.
- **Earnings factor**:
  - Global per-direction cumulative accounting accumulator.
  - Used to convert flow contribution into claimable output.

## 3) Storage Layout (Current)

In `TwapEngine`:

- `states[marketId] -> TwapState`
  - `streamGhostT0`, `streamGhostT1`
  - `lastUpdateTime`, `lastClearTime`
  - `epochInterval`
- `streamPools[marketId][zeroForOne] -> StreamPool`
  - `sellRateCurrent`
  - `earningsFactorCurrent`
  - per-epoch maps:
    - `sellRateStartingAtInterval`
    - `sellRateEndingAtInterval`
    - `earningsFactorAtInterval`
- `streamOrders[marketId][orderId] -> StreamOrder`
  - market-scoped order storage (no separate `orderMarkets` map).
- `epochEventBitmap[marketId][word]` + `epochWordBitmap[marketId][summaryWord]`
  - two-level bitmap index of epochs that have start/end rate events.
  - lets accrual jump directly to event epochs instead of iterating every interval.
- `orderNonce`
  - global nonce used in `orderId` generation.

## 4) Router Oracle Modes

`GhostRouter` supports two oracle modes per market:

- **External**
  - Uses `IGhostOracle(market.oracle).getSpotPrice(marketId)`.
- **UniswapV4Spot**
  - Reads `sqrtPriceX96` from pool manager and converts to token1/token0 price scaled to `1e18`.

Spot price retrieval is centralized in:

- `getSpotPrice(marketId)`

All swap/clear paths consume price through this interface.

## 5) Lifecycle: Step by Step

## 5.1 Deploy and Wire

1. Deploy `GhostRouter`.
2. Deploy `TwapEngine` with router address and config:
   - `expirationInterval`
   - `maxDiscountBps`
   - `discountRateScaled`
3. Register engine on router: `registerEngine(engine)`.
4. Initialize market in router:
   - `initializeMarket(vanillaKey, externalOracle)` or
   - `initializeMarketWithUniswapOracle(vanillaKey)`.

## 5.2 Submit Stream

User calls:

- `submitStream(marketId, zeroForOne, duration, amountIn)`

Flow:

1. Engine validates nonzero `duration` and `amountIn`.
2. Engine asks router to pull sell token from user into vault.
3. Engine accrues market state to now (`_accrueInternal`).
4. Engine computes:
   - `nextEpoch`
   - `expiration = nextEpoch + duration`
   - `scaledSellRate = amountIn * RATE_SCALER / duration`
5. Engine schedules stream rate:
   - add at `startEpoch`
   - subtract at `expiration`
6. Engine stores `streamOrders[marketId][orderId]`.

## 5.3 Accrual and Epoch Crossing

`_accrueInternal(marketId)` does:

1. If first touch, initializes timestamps and returns.
2. Uses the epoch bitmap index to find the next epoch boundary that actually has a start/end event.
3. For each segment:
   - adds ghost based on `sellRateCurrent` and elapsed seconds.
4. On each crossed event epoch:
   - `_crossEpoch` activates starting rates and removes expiring rates.
   - snapshots `earningsFactorAtInterval[epoch]`.
   - clears consumed per-epoch schedule entries and updates bitmap membership.

This guarantees start/expiry boundaries are honored even if time jumps.
It also removes the old linear-idle behavior where accrual cost grew with every elapsed interval.

## 5.4 Clear Auction (Solver Path)

Solver calls:

- `clearAuction(marketId, zeroForOne, maxAmount, minDiscountBps)`

Flow:

1. Accrue state.
2. Compute available directional ghost.
3. Compute discount by elapsed time since last clear, capped by `maxDiscountBps`.
4. Read spot price from router.
5. Compute discounted payment:
   - full fair value at spot
   - minus discount.
6. Pull payment token from solver first.
7. Push cleared ghost token to solver.
8. Deduct ghost and record earnings for stream sellers.

Important behavior:

- If `discountedPayment == 0`, transaction reverts (`InvalidAmount`).

## 5.5 Taker Swap Through Hub

Taker calls router:

- `swap(marketId, zeroForOne, amountIn, amountOutMinimum)`

Router path:

1. Pull taker input into vault.
2. Get spot price.
3. **Layer 1**: aggregate ghost from all engines and pro-rata net opposing sides.
4. **Layer 2**: taker intercept against remaining directional ghost.
5. **Layer 3**: fallback remainder through vanilla V4 swap.
6. Enforce slippage, deliver output to taker.

## 5.6 Claim Earnings

User calls:

- `claimTokens(marketId, orderId)`

Flow:

1. Accrue once.
2. Load market-scoped order.
3. Build settlement preview via `_previewOrderSettlementAfterAccrual`.
4. Execute claim via `_claimTokensAfterAccrual`:
   - update `earningsFactorLast`
   - router pushes output token to order owner.

## 5.7 Cancel Order

User calls:

- `cancelOrder(marketId, orderId)`

Flow:

1. Accrue once.
2. Validate ownership and existence.
3. Build same settlement preview used by claim path.
4. Claim pending earnings through `_claimTokensAfterAccrual`.
5. If expired: delete order, refund `0`.
6. Else:
   - if started, remove current/ending sell rate and compute remaining refund.
   - if not started, remove starting/ending scheduled rate and refund full scheduled principal.
7. Delete order and push refund token through router.

## 5.8 Force Settle

Router-only call:

- `forceSettle(marketId, zeroForOne)`

Flow:

1. Accrue.
2. If ghost exists and stream is active:
   - router executes AMM settlement (`settleGhost`)
   - engine records proceeds as earnings
   - directional ghost is zeroed.

Used by liquidation/emergency flow to crystallize ghost value.

## 6) Accounting Formulas

- `scaledSellRate = amountIn * RATE_SCALER / duration`
- `ghostAccrual = sellRateCurrent * deltaSeconds / RATE_SCALER`

- Auction fair payment:
  - if `zeroForOne`: `clearAmount * spotPrice / 1e18`
  - else: `clearAmount * 1e18 / spotPrice`

- Discounted payment:
  - `fullPayment - fullPayment * discountBps / 10_000`

- Earnings factor increment:
  - `earnings * (Q96 * RATE_SCALER) / sellRateCurrent`

- Order earnings:
  - `sellRate * (effectiveEF - effectiveEFL) / (Q96 * RATE_SCALER)`

## 7) Earnings Capping Rules

`_computeEarnings` applies two protections:

- **Expiry cap**
  - once `lastUpdateTime >= expiration`, cap effective factor at expiry snapshot.
  - this cap is applied even when snapshot is zero.
- **Deferred-start floor**
  - before start epoch, effective baseline equals current effective factor (no pre-start earnings).
  - after start, floor by start-epoch snapshot.

## 8) Access Control and Safety

- Router methods `pull/push/settleGhost` are `onlyEngine`.
- Engine cross-engine methods (`syncAndFetchGhost`, `applyNettingResult`, `takeGhost`, `forceSettle`) are router-gated.
- External state mutating user flows use `nonReentrant`.
- Clear path pulls payment before payout.
- Stream-closing epochs auto-settle residual directional ghost before sell rate goes to zero,
  preventing stranded proceeds from becoming unclaimable.
- Market-scoped order mapping prevents cross-market order confusion.
- Engine constructor is fail-fast:
  - `ghostRouter` must be non-zero.
  - `expirationInterval` must be non-zero.
  - `maxDiscountBps` must be `<= 10_000`.
- Router engine registry is fail-fast:
  - zero-address and non-contract engines are rejected.
  - owner can deregister unhealthy engines.
- Router engine calls in netting/intercept paths are fault-isolated with `try/catch`:
  failed engines are skipped and failures are emitted as events instead of reverting all swaps.

## 9) Read APIs

- `TwapEngineLens.getOrder(marketId, orderId)`
- `TwapEngineLens.getStreamState(marketId)`
- `TwapEngineLens.getStreamPool(marketId, zeroForOne)`
- `TwapEngineLens.getCancelOrderStateCommitted(marketId, orderId)`
- `TwapEngineLens.getCancelOrderStateExact(marketId, orderId)`
- `TwapEngineLens.getCancelOrderState(marketId, orderId)` (backward-compatible alias of committed view)

Note:

- `getStreamState` computes pending accrual with current sell rate and elapsed time.
- It is a practical read helper, not a full epoch-step simulator.
- `getCancelOrderStateCommitted` reflects committed engine state (`lastUpdateTime`).
- `getCancelOrderStateExact` simulates cancel preview at current block timestamp without mutating state.

## 10) Operational Checklist

Before production rollout:

1. Validate oracle mode per market and fallback behavior.
2. Validate token ordering (`token0 < token1`) in market init.
3. Configure `expirationInterval`, `discountRateScaled`, `maxDiscountBps` conservatively.
4. Load test long idle periods and bitmap event density behavior.
5. Monitor ghost levels and clear cadence.

## 11) Testing Coverage (Current)

`contracts/test/twamm_v3` includes:

- unit tests for stream lifecycle and access checks,
- integration tests for hub-spoke swap/claim flow,
- fuzz tests for bounded refunds and takeGhost limits,
- stateful invariant tests for:
  - router balance coverage vs modeled liabilities,
  - order metadata consistency.

Typical run:

```bash
forge test --match-path "test/twamm_v3/*.t.sol" -vv
```

## 12) Practical Notes

- Claims on wrong market/order pair return `0` because orders are market-scoped.
- Cancel on wrong market/order pair reverts `OrderDoesNotExist`.
- `clearAuction` and intercept/netting are spot-price based; oracle quality is critical.
- `takeGhost` rounds `inputConsumed` up for non-zero fills to avoid free dust extraction.

## 13) Event Surface

`twamm_v3` now emits a broad operational event stream for monitoring and indexing:

- Engine:
  - `StreamSubmitted`
  - `AuctionCleared`
  - `TokensClaimed`
  - `OrderCancelled`
  - `GhostSettled` (includes reason codes for epoch-close, last-order-cancel, force-settle)
  - `NettingApplied`
  - `GhostTaken`
  - `ForceSettled`
- Router:
  - `EngineRegistered`
  - `EngineDeregistered`
  - `EngineCallFailed`
  - `MarketInitialized`
  - `OracleModeUpdated`
  - `GlobalNettingExecuted`
  - `GhostSettledViaAMM`
  - `SwapExecuted`

