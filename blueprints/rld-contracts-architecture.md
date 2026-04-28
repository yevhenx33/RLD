# RLD Contracts Architecture Blueprint

This blueprint defines the architecture, invariants, and review standard for the non-DEX contract surface under `contracts/src/rld`, `contracts/src/periphery`, and `contracts/src/shared`. Treat it as the source of truth for future work on RLD Core, PrimeBroker accounts, factories, valuation modules, liquidation, funding, settlement, wrappers, adapters, and production periphery flows.

For Ghost Router, TWAP engine, limit engine, and DEX-native routing details, use `blueprints/dex-architecture.md`.

## Scope

Covered:

- `contracts/src/rld/core/*`
- `contracts/src/rld/broker/*`
- `contracts/src/rld/modules/*`
- `contracts/src/rld/tokens/*`
- `contracts/src/rld/periphery/*`
- `contracts/src/periphery/*`
- `contracts/src/shared/*`
- Tests under `contracts/test/rld`, `contracts/test/integration`, and any periphery-focused tests.

Out of production scope unless explicitly reintroduced:

- `contracts/src/periphery/BasisTradeFactory.sol`
- `contracts/src/periphery/SimFunder.sol`
- mock or simulation-only oracles

Do not use this blueprint to justify changes to runtime infrastructure, Docker, ClickHouse, frontend, or DEX internals except where contract integrations directly require it.

## Architecture Summary

RLD separates protocol debt accounting from asset custody:

- `RLDCore` tracks markets, debt principal, normalization factors, bad debt, liquidation, global settlement, and solvency checks.
- `PrimeBroker` is a user-owned smart margin account. It holds all user assets and reports NAV to Core.
- `PrimeBrokerFactory` deploys broker clones and mints broker ownership NFTs.
- `RLDMarketFactory` deploys a market bundle: broker factory, verifier, position token, V4 pool, oracle registration, and Core market registration.
- `PositionToken` is the ERC20 debt token (`wRLP`) minted and burned by Core.
- Modules provide valuation, funding, liquidation, settlement, oracle, verifier, and lending-adapter functionality.
- Periphery contracts provide convenience flows over the primitives. They must not weaken the primitive invariants.
- Shared contracts define interfaces, math libraries, wrappers, and deployment constants.

Mental model:

```text
NFT owner / operator
        |
        v
   PrimeBroker  <---->  Periphery routers / factories
        |
        | reports NAV, modifies debt through lock()
        v
     RLDCore  <---->  Funding / liquidation / settlement modules
        |
        | mints / burns debt token
        v
  PositionToken (wRLP)

Market deployment:
RLDMarketFactory -> PositionToken + PrimeBrokerFactory + BrokerVerifier + V4 pool + Core market
```

Core is accounting. Broker is custody. Modules are stateless or tightly-scoped helpers. Periphery is convenience, not authority.

## Core Concepts

- `MarketId`: deterministic identifier computed from `(collateralToken, underlyingToken, underlyingPool)`.
- Collateral token: yield-bearing or wrapped asset held by brokers, e.g. `waUSDC`.
- Underlying token: base lending-market asset, e.g. `USDC`.
- Position token: `wRLP`, an ERC20 representation of debt principal.
- Normalization factor: debt multiplier that turns principal into true debt.
- Broker NAV: total value reported by `PrimeBroker.getNetAccountValue()` in collateral-token terms.
- Net worth: `broker NAV - debt value`, used by Core solvency logic.
- Global settlement: terminal mode for CDS markets that blocks regular mutation and invalidates withdrawal queues.
- Withdrawal queue: delayed collateral withdrawal path for debt-bearing CDS brokers.

## Core Responsibilities

`RLDCore` is responsible for:

- Creating markets only through the trusted factory.
- Storing immutable market addresses and configurable risk parameters.
- Managing lazy funding updates through market funding modules.
- Tracking debt principal by `(marketId, broker)`.
- Minting and burning `PositionToken` to match principal changes.
- Enforcing solvency after every locked position mutation.
- Running permissionless liquidation.
- Registering and socializing bad debt.
- Entering global settlement when called by the configured settlement module.
- Invalidating broker withdrawal queues during settlement.

`RLDCore` must not:

- Hold user collateral as the primary custody layer.
- Trust arbitrary accounts as brokers.
- Accept zero or invalid market-critical addresses.
- Let position mutations bypass the lock/solvency pattern.
- Let normalization factor collapse to zero.
- Treat test-only operational hooks as production controls.

## PrimeBroker Responsibilities

`PrimeBroker` is a clone-owned smart account represented by a factory NFT.

It is responsible for:

- Holding collateral, position tokens, TWAMM claims, V4 LP NFTs, and other account assets.
- Reporting NAV through `getNetAccountValue()`.
- Entering Core `lock()` to modify debt.
- Enforcing ownership through the factory NFT owner.
- Managing bounded operators.
- Delegating heavy V4/TWAMM/operator logic to `PrimeBrokerOpsModule`.
- Supporting delayed collateral withdrawals for debt-bearing CDS brokers.
- Supporting liquidation seizure by Core.
- Supporting freeze/unfreeze for bond-like custody flows.

Broker ownership is dynamic:

- Owner is `PrimeBrokerFactory.ownerOf(uint160(broker))`.
- NFT transfer transfers account control.
- Factory transfer hooks must revoke broker operators before transfer completes.

Broker invariants:

- Every user-facing state mutation that can change balances or tracked assets must either end with `_checkSolvency()` or be explicitly blocked during debt-bearing/global-settlement modes.
- TWAMM claim, cancel, submit, and tracking mutations must preserve solvency.
- Arbitrary `execute()` cannot call Core or the broker itself.
- Frozen brokers block regular state-changing operations except liquidation and owner unfreeze.
- Operators cannot add or remove other operators.
- Operator list must stay bounded.
- Claims and withdrawals must not silently bypass global settlement restrictions.

## Market Deployment Architecture

`RLDMarketFactory` orchestrates market deployment:

1. Validate deployment parameters.
2. Compute the expected Core `MarketId`.
3. Deploy `PrimeBrokerFactory`.
4. Deploy `BrokerVerifier`.
5. Deploy and initialize `PositionToken`.
6. Initialize the V4 pool.
7. Initialize/register Ghost/TWAP oracle data if configured.
8. Register market with `RLDCore`.
9. Link `PositionToken` to the market and transfer ownership to Core.

Market deployment invariants:

- Core must be initialized exactly once before market creation.
- `PositionToken.owner()` must become Core.
- Broker verifier must point to the deployed broker factory.
- Core `MarketId` must equal the factory's precomputed id.
- Risk parameters must satisfy:
  - `minColRatio > 1e18`
  - `maintenanceMargin >= 1e18`
  - `minColRatio > maintenanceMargin`
  - `0 < liquidationCloseFactor <= 1e18`
  - `fundingPeriod` in the approved range
- Production markets should configure a real spot oracle unless an index-only liquidation mode has explicit risk signoff.

## Debt and Solvency Model

Core debt is tracked in principal units. True debt is:

```text
trueDebt = debtPrincipal * normalizationFactor / 1e18
debtValue = trueDebt * indexPrice / 1e18
```

Core solvency uses net worth:

```text
netWorth = brokerNAV - debtValue
requiredMargin = debtValue * (marginRatio - 1e18) / 1e18
solvent = netWorth >= requiredMargin
```

This is equivalent to the classic asset-ratio check but avoids double-counting `wRLP` assets and liabilities.

Required behavior:

- No debt means solvent.
- Invalid or unverifiable broker means insolvent.
- Reverting broker NAV means insolvent.
- New debt uses `minColRatio`.
- Maintenance operations use `maintenanceMargin`.
- If an operation includes both maintenance and new debt, the stricter action type wins.
- Post-lock solvency must check every touched `(marketId, broker)` pair.

Operational caveat:

- Large touched lists can increase gas significantly. Do not introduce unbounded multi-position lock paths without a cap or dedupe design.

## Funding Model

Funding updates are lazy and applied during position mutation and liquidation.

Standard funding:

- Reads mark price from market `markOracle`.
- Reads index price from market `rateOracle`.
- Computes funding rate from normalized mark-index divergence.
- Updates normalization factor with exponential decay/growth.
- Must revert rather than accepting `expWad(...) <= 0`.

CDS decay funding:

- Reads `decayRateWad` from Core config.
- Applies deterministic continuous decay.
- Must reject zero decay after time passes.
- Must reject zero current normalization factor.
- Must reject rounded zero output.

Bad debt socialization:

- Bad debt is stored as principal.
- If supply exists, bad debt is gradually added to normalization factor over `badDebtPeriod`.
- If supply is zero, bad debt remains frozen until supply reappears.
- Bad-debt changes must emit events and keep `PositionToken.totalSupply()` as the source of truth for total debt.

Funding invariants:

- Normalization factor must never become zero.
- Normalization factor must fit in `uint128`.
- Funding models must be view-only and deterministic from Core state and oracle data.
- Do not leave test-only funding mutation hooks in production unless explicitly approved as a keeper function.

## Liquidation Architecture

Liquidation flow:

1. Apply funding.
2. Verify broker is valid and below maintenance.
3. Validate liquidation amount.
4. Cache index price and broker NAV.
5. Snapshot principal.
6. Optimistically reduce debt principal.
7. Calculate seize amount through liquidation module.
8. Ask broker to seize assets.
9. Burn `wRLP` obtained from broker and/or liquidator.
10. Register bad debt if collateral is insufficient.
11. Enforce `minCollateralOut`.
12. Emit liquidation and market-state events.

Liquidation math uses:

- Gross broker NAV for available assets.
- Net worth for health/bonus calculations.
- `min(indexPrice, spotPrice)` where the code intentionally protects the liquidator from overpaying.

Liquidation invariants:

- Liquidating a solvent broker must revert.
- Non-broker liquidation targets must revert.
- Debt principal cannot underflow.
- Close factor applies unless the broker is underwater.
- Liquidator cannot receive less than `minCollateralOut`.
- Broker-seized `wRLP` offsets token debt before liquidator `wRLP` is burned.
- Bad debt is registered only when actual assets cannot cover the calculated seize.
- Total debt must sync to `PositionToken.totalSupply()` after liquidation.

## PrimeBroker Seize Model

`PrimeBroker.seize()` is Core-only and non-reentrant.

The broker attempts to unlock liquid assets before sweeping:

1. Use direct collateral and `wRLP` balances.
2. Force-settle/cancel active TWAMM orders.
3. Unwind tracked V4 LP position if needed.
4. Transfer seized collateral to liquidator.
5. Transfer seized `wRLP` to Core for burn.

Important constraints:

- Seize must remain callable even when broker is frozen.
- Force settlement is best-effort but should use the router-level DEX path.
- Only tracked TWAMM and V4 LP positions count for automatic unlock.
- Untracked assets may remain in the broker and should not be assumed liquid.

## Periphery Architecture

Production periphery includes:

- `BrokerRouter`: user-facing deposits, long/short flows, V4 swaps, and wrapping routes.
- `BondFactory`: one-transaction bond mint/close convenience flow.
- `CDSCoverageFactory`: fixed-coverage CDS opener/closer.
- `BrokerExecutor`: signature-bound atomic multicall operator.
- `RLDV4Quoter`: deployment wrapper for V4 quote behavior.

Non-production or currently skipped:

- `BasisTradeFactory`: not production-scoped unless explicitly reintroduced.
- `SimFunder`: simulation/fork helper only.

Periphery rules:

- Periphery must not become the primary custody layer for many users unless explicitly designed to isolate accounting per user/trade.
- User-supplied `PoolKey` values must validate token pair and hook policy.
- Production pool keys should be hookless unless there is an explicit hook allowlist.
- Periphery swaps must include slippage protection for production value-bearing paths.
- Convenience flows must not weaken broker ownership, solvency, or withdrawal-queue rules.
- Factories that custody broker NFTs must track ownership and clear mappings carefully.
- Closing flows must not transfer pooled/shared collateral belonging to another user.

## BrokerRouter Rules

`BrokerRouter` is a permanent default operator on brokers when configured by market factory.

Required behavior:

- All mutating user paths require broker owner or broker operator authorization.
- Deposit route must exist and must match the broker collateral path.
- User-supplied pool keys must match broker collateral/position token pair and reject unexpected hooks.
- Router must not retain residual token balances after user operations.
- Long/short flows must use solvency-enforced broker operations.

Review checklist:

- Verify deposit route integrity:
  - underlying token
  - aToken
  - wrapped collateral token
  - Aave pool
- Verify Permit2 token matches the expected underlying token.
- Verify all swaps expose appropriate min-out or min-proceeds protection.
- Verify hookless or allowlisted V4 pool usage.

## Bond and CDS Coverage Factories

Bond factory:

- Mints isolated frozen broker bonds.
- Uses a broker short position and TWAMM buy-back order.
- Holds broker NFT unless user claims it.
- Must use hookless V4 pool ids for Ghost/TWAP markets.
- Must reject unexpected V4 hooks in user-supplied close/mint pool keys.

CDS coverage factory:

- Opens a broker holding position tokens plus a premium TWAMM stream.
- Uses coverage and premium formulas tied to index/decay assumptions.
- Must validate hookless pool keys.
- Settlement payout flows belong to settlement module, not close coverage.

Factory invariants:

- Custodied NFT ownership mappings must clear before transfer/close.
- Returning a claimed NFT to factory must restore ownership mapping only for the caller.
- Closing a frozen broker must unfreeze only when caller proves ownership.
- All residual user value must return to the legitimate owner, not to caller-controlled pooled storage.

## BrokerExecutor Rules

`BrokerExecutor` grants itself broker operator rights for one transaction using an owner signature, then executes arbitrary calls, then revokes itself.

Security requirements:

- Signature binds:
  - operator
  - active flag
  - broker
  - nonce
  - caller
  - calls commitment
  - chain id
- Reverts must bubble.
- Operator revocation must occur after successful call batch.
- Off-chain signing UX must display target addresses, selectors, values, and decoded calldata.

Operational note:

- This pattern is intentionally powerful. A bad signature is equivalent to giving temporary full operator control.

## Valuation Modules

TWAP valuation:

- Uses `getCancelOrderStateExact`.
- Prices sell-token refund and buy-token proceeds in broker valuation token terms.
- Adds discounted ghost value for active orders.
- Must not over-credit orders before start or after expiry.

V4 LP valuation:

- Reads position liquidity and pool/tick info from POSM.
- Computes token principal from current tick and liquidity.
- Adds uncollected fees using the canonical V4 position key:
  - owner: PositionManager
  - tick lower
  - tick upper
  - salt: token id
- Prices collateral at 1:1.
- Prices position token through index oracle.

Valuation invariants:

- If broker no longer owns the tracked asset, NAV contribution must be zero at the broker layer.
- Unknown tokens should not be valued.
- Rounding assumptions must be conservative or explicitly documented.
- Oracle failures should cause solvency failure rather than optimistic valuation.

## Oracle Modules

Oracle modules supply index, spot, and mark prices. They are critical to solvency and liquidation.

Production expectations:

- Prices should be WAD-scaled unless a caller explicitly documents otherwise.
- Chainlink-style feeds need:
  - feed configuration
  - positive answer checks
  - staleness checks
  - answered-in-round checks where relevant
  - L2 sequencer checks where relevant
  - sane bounds
- Aave rate oracles should cap extreme rates and enforce a non-zero floor.
- Mark/TWAP oracles should avoid stale or manipulated observations.

Do not promote `ChainlinkSpotOracle` or mock oracles to production without completing hardening.

## Shared Wrappers and Libraries

`WrappedAToken`:

- Converts rebasing aTokens into non-rebasing shares.
- First wrap locks `minimumLiquidity` shares to reduce donation/inflation attacks.
- Must reject zero aToken address.
- Must never mint zero shares for meaningful deposits after normal bootstrap.

Math libraries:

- Prefer one canonical WAD math implementation.
- `FixedPointMathLib` is the stronger canonical implementation for exponentials.
- Avoid using incomplete or experimental exponential code from `FixedPointMath.sol`.
- `LiquidityAmounts` rounds down; this is usually conservative for NAV principal but must be documented.

Transient storage:

- Requires EIP-1153/Cancun-compatible chains.
- Appropriate for tx-scoped lock and touched-position state only.
- Do not use transient storage for persistent accounting.

Deploy config:

- `RLDDeployConfig` is mainnet-centric.
- Deployment scripts targeting other chains must override addresses and should not silently reuse mainnet constants.

## Aave Adapter Rules

`AaveAdapter` is a normal-call adapter, not a delegatecall library.

Required semantics:

- Caller approves adapter for assets/aTokens used by `supply`, `withdraw`, or `repay`.
- `supply` transfers asset to adapter and supplies on behalf of caller.
- `withdraw` transfers aToken to adapter and withdraws underlying to caller.
- `borrow` borrows on behalf of caller and transfers borrowed asset to caller.
- `repay` pulls repayment asset from caller, repays on behalf of caller, and refunds unused amount.
- Borrowing requires Aave credit delegation from caller to adapter where the Aave market requires it.

Do not mix standard-call and delegatecall assumptions in adapters.

## Access Control Checklist

Core:

- Only factory creates markets.
- Only lock holder modifies positions.
- Only settlement module enters settlement or invalidates withdrawal queues.
- Curator-only risk updates.

Broker:

- Owner is factory NFT owner.
- Operators are bounded and cannot manage other operators.
- Factory can revoke all operators during NFT transfer.
- Core-only `seize` and `invalidateWithdrawalQueue`.
- Frozen brokers block regular mutation.

Factories and periphery:

- Market factory owner creates markets.
- Periphery actions require broker owner/operator or mapped factory ownership.
- Custody mappings must align with NFT ownership.

Modules:

- Stateless modules should have no mutable admin unless explicitly needed.
- Settlement proxy owner/operator powers are high trust and should use multisig/timelock in production.

## Technical Review Checklist

Before approving changes in this surface, verify:

- No mutation path bypasses solvency unless explicitly designed for liquidation or settlement.
- No production path pools user-specific collateral in shared contract state without per-user accounting.
- No user-supplied pool key can invoke arbitrary hooks unless allowlisted.
- No funding path can set normalization factor to zero.
- No valuation module overstates NAV through stale ownership or wrong position keys.
- No liquidation path computes bonus from gross NAV when net worth is required.
- No wrapper is vulnerable to trivial first-deposit donation inflation.
- No adapter relies on unstated delegatecall assumptions.
- No production periphery path uses `minOut = 0`.
- No mock/simulation-only helper is used in deployment scripts for production.
- No oracle is deployed without freshness/bounds strategy.
- No factory close path can transfer another user's funds.

## Mathematical Review Checklist

Check units and scales:

- Collateral balances often use token decimals, commonly 6.
- WAD prices use `1e18`.
- Aave rates use RAY `1e27`.
- `PositionToken` decimals match collateral decimals.
- `debtPrincipal` and `PositionToken.totalSupply()` are principal units.
- `normalizationFactor` is WAD.
- `debtValue = principal * normalizationFactor * indexPrice`.
- Liquidation close factor and margin ratios are WAD.
- Liquidation discount params are packed bps/slope values.

Check rounding:

- Debt repayment should not underflow principal.
- Liquidation principal-to-cover should be bounded by actual debt.
- LP valuation rounding should not over-credit by default.
- Wrapper share conversion must handle direct donations.
- Funding exponentials must reject underflow-to-zero.

## Operational Monitoring

Index and alert on:

- `MarketCreated`
- `MarketStateUpdated`
- `FundingApplied`
- `BadDebtRegistered`
- `BadDebtSocialized`
- `RiskUpdateProposed`
- `RiskUpdateApplied`
- `Liquidation`
- `GlobalSettlementEntered`
- `BrokerWithdrawalQueueInvalidated`
- broker `OperatorUpdated`
- broker `BrokerFrozen` / `BrokerUnfrozen`
- broker withdrawal queue events
- TWAMM order tracking events

Recommended alerts:

- Normalization factor near zero or unusually large.
- Sudden bad debt registration.
- Repeated liquidation failures.
- Large pending risk updates.
- Broker withdrawal queues invalidated.
- Operators added shortly before large withdrawals.
- Unexpected periphery residual balances.
- Oracle heartbeat or freshness failures.

## Testing Standard

After changes in this surface, run the smallest relevant focused suite and then affected broader suites.

Minimum for RLD/core/broker/shared changes:

```bash
cd contracts
forge test --match-path "test/rld/*.t.sol"
forge test --match-path "test/integration/*.t.sol"
```

If DEX integration or TWAMM broker behavior is touched:

```bash
cd contracts
forge test --match-path "test/dex/*.t.sol"
```

For approved audit-fix regressions:

```bash
cd contracts
forge test --match-path "test/rld/ApprovedAuditFixes.t.sol" -vv
```

Required test categories:

- Solvency after every broker mutation.
- Funding extreme exponents.
- Liquidation close factor, negative equity, bad debt, and min-out.
- V4 LP NAV principal and fees.
- TWAMM valuation and claim/cancel behavior.
- Wrapper first-deposit and donation behavior.
- Periphery pool-key validation.
- Adapter custody semantics.
- Settlement/global-settlement withdrawal queue invalidation.

## Known Skips and Non-Goals

Current user-approved skips:

- `BasisTradeFactory` is not production-scoped.
- Public `RLDCore.applyFunding` was left untouched in the approved implementation pass.
- Oracle hardening was skipped in the approved implementation pass.

If any skipped surface becomes production-scoped, run a new audit before enabling it.

## Handoff Standard

Every final response for this contract surface should include:

- What changed.
- Which modules/contracts were intentionally skipped.
- What tests were run.
- Any existing warnings.
- Any remaining operational requirement, especially oracle freshness, periphery slippage, and production deployment exclusions.

