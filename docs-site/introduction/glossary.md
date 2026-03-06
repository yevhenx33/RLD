# Glossary

## A

**aToken** — Interest-bearing token received when depositing into Aave (e.g., aUSDC). Used as collateral in RLD markets.

## B

**Bond** — A synthetic fixed-yield position created by minting wRLP and submitting a JTM streaming sell order. See [Synthetic Bonds](../guides/synthetic-bonds).

**Broker** — See [PrimeBroker](#p).

**BrokerRouter** — The primary periphery contract for everyday trading operations (deposit, long, short, close). Acts as a permanent operator on PrimeBroker accounts.

## C

**Clear** — The act of settling ghost balances in the JTM hook by purchasing them at a discounted TWAP price. Performed by arbitrageurs via `clear()`. See [Clearing & Arbitrage](../jtm/clearing-and-arbitrage).

**Close Factor** — The maximum percentage of a position's debt that can be liquidated in a single transaction (typically 50%).

**Cross-Margin** — A system where all assets in a PrimeBroker (ERC20 + LP positions + JTM orders) contribute to a single unified solvency calculation.

**Curator** — An address authorized to propose risk parameter changes for a specific market, subject to a 7-day timelock.

## D

**Dutch Auction** — A declining-price auction mechanism used in both liquidation (health-based bonus) and JTM clearing (time-based discount).

## E

**Ephemeral Operator** — An operator that is set and revoked within a single atomic transaction, used by BrokerExecutor and BondFactory for multi-step operations.

## F

**Flash Accounting** — RLD's lock pattern where solvency is checked once at the end of all operations within a `lock()` call, rather than after each individual operation. See [Flash Accounting](../architecture/flash-accounting).

**Funding Rate** — The rate at which value transfers between longs and shorts, calculated as `(NormalizedMark - Index) / Index`. Applied via the Normalization Factor.

## G

**Ghost Balance** — Tokens that have been streamed by JTM orders but not yet settled (cleared). They exist within the JTM hook contract, invisible to the AMM. See [Design Evolution](../jtm/design-evolution).

## H

**Health Ratio** — `NAV / (TrueDebt × IndexPrice × MaintenanceMargin)`. Values above 1.0 are healthy; below 1.0 is liquidatable.

**Hook** — A Uniswap V4 contract that intercepts pool lifecycle events (swaps, liquidity changes). JTM is RLD's hook. See [V4 Hooks](../jtm/v4-hooks-architecture).

## I

**Index Price** — The fundamental price of wRLP derived from the lending rate: `P = K × r` where K=100. See [Key Concepts](./key-concepts#index-price).

## J

**JIT (Just-In-Time)** — A matching technique where ghost balances fill incoming swaps directly, bypassing the AMM. Provides better execution for both makers and takers.

**JTM (JIT-TWAMM)** — The protocol's Uniswap V4 hook supporting streaming, limit, and market orders via a Ghost Balance engine with 3-layer matching. See [JTM Engine](../jtm/design-evolution).

## L

**Liquidation** — The process of closing an undercollateralized position. Anyone can liquidate and receives a health-based bonus. See [Liquidation](../protocol/liquidation).

## M

**Maintenance Margin** — The minimum collateralization ratio below which a position becomes liquidatable (typically 109%).

**Mark Price** — The market-determined price of wRLP, derived from a TWAP over the V4 pool.

**Market** — An RLD market defined by (underlying pool, underlying token, collateral token). Each market has its own wRLP, V4 pool, and risk parameters.

**MarketId** — Deterministic identifier: `keccak256(collateralToken, underlyingToken, underlyingPool)`.

## N

**NAV (Net Account Value)** — Total value of all assets in a PrimeBroker: ERC20 balances + V4 LP values + JTM order values.

**NF (Normalization Factor)** — Multiplier applied to debt principal to calculate true debt. Changes over time via funding. See [Funding Mechanism](../protocol/funding-mechanism).

**Netting** — JTM Layer 1: opposing ghost balances are matched at TWAP price with zero fees or price impact.

## O

**Operator** — An address authorized to act on behalf of a PrimeBroker owner. Can be permanent (BrokerRouter) or ephemeral (BrokerExecutor).

## P

**PrimeBroker** — A smart contract wallet (EIP-1167 clone) that holds all user assets and computes unified solvency. Identified by an NFT. See [Prime Broker](../architecture/prime-broker).

**PositionToken** — See [wRLP](#w).

## S

**Solvency** — A position is solvent when `NAV ≥ TrueDebt × IndexPrice × MaintenanceMargin`.

**Streaming Order** — A JTM TWAP order that sells tokens at a constant rate over a specified duration. See [Streaming Orders](../jtm/streaming-orders).

## T

**Timelock** — A 7-day mandatory delay between proposing and applying risk parameter changes. Provides users time to react.

**TWAP** — Time-Weighted Average Price. Used by JTM for execution pricing and by the oracle for mark price.

## V

**V4 Pool** — The Uniswap V4 pool where wRLP trades against the collateral token. Managed by the JTM hook.

## W

**wRLP** — Wrapped Rate-Level Position. The ERC-20 token representing a short position's debt in an RLD market. Minted against collateral, burned to repay debt. Trades on Uniswap V4.
