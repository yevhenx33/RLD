# Liquidation

## When Liquidation Occurs

A position becomes liquidatable when its **health ratio** falls below 1.0:

$$\text{Health} = \frac{\text{Net Worth}}{\text{debtPrincipal} \times NF \times P_{index} \times \text{maintenanceMargin}} < 1.0$$

This can happen when:

- The interest rate (and thus index price) moves against your position
- Funding payments erode your margin (NF increases for shorts when mark < index)
- Collateral value drops (CDS markets where collateral is a volatile asset)

## Dutch Auction Pricing (Euler-Inspired)

RLD uses a **health-based Dutch auction** for liquidation pricing, inspired by [Euler Finance's](https://docs.euler.finance/) liquidation model. The worse the position's health, the larger the bonus for the liquidator:

$$\text{Bonus} = \text{BaseDiscount} + \text{Slope} \times (1 - \text{HealthScore})$$

Where:

- **BaseDiscount** = minimum bonus even for barely-insolvent positions (packed in bps)
- **Slope** = how aggressively the bonus scales with insolvency depth
- **MaxDiscount** = cap to prevent excessive extraction

The `DutchLiquidationModule` computes health score as:

```
HealthScore = CollateralValue / (DebtValue × maintenanceMargin)
```

A position at 80% health gets a small bonus. A position at 20% health gets a large bonus. This creates a **natural priority queue** — the most underwater positions offer the highest rewards and get cleared first.

### Liquidation Parameters (Packed)

The `liquidationParams` field is a `bytes32` packed as:

| Bits  | Field               | Example    |                |
| ----- | ------------------- | ---------- | -------------- |
| 0-15  | Base Discount (bps) | 100 = 1%   | Minimum bonus  |
| 16-31 | Max Discount (bps)  | 1000 = 10% | Cap on bonus   |
| 32-47 | Slope (×100)        | 100 = 1.0× | Scaling factor |

These are **per-market risk parameters**, tunable via 7-day curator timelock.

## Close Factor (Aave-Inspired)

Liquidators can only close a portion of a position's debt per transaction. This **close factor** is inspired by [Aave's](https://docs.aave.com/) partial liquidation model:

| Condition                      | Close Factor            | Rationale                                        |
| ------------------------------ | ----------------------- | ------------------------------------------------ |
| Net worth > 0 (not underwater) | Configurable (e.g. 50%) | Protects borrower — gives time to add collateral |
| Net worth ≤ 0 (underwater)     | **100%**                | Full liquidation to limit bad debt               |

The dynamic close factor ensures that healthy-but-insolvent positions get a chance to recover, while truly underwater positions are closed immediately to minimize protocol losses.

## Liquidation Flow

```
  Liquidator                RLDCore                  PrimeBroker
  ---------                 -------                  -----------
      |                        |                          |
      |  liquidate(id,broker,  |                          |
      |    debtToCover,minOut) |                          |
      |----------------------->|                          |
      |                        |                          |
      |                        | 1. applyFunding()        |
      |                        | 2. verify broker valid   |
      |                        |    (BrokerVerifier)      |
      |                        | 3. verify !isSolvent     |
      |                        | 4. check close factor    |
      |                        | 5. reduce debt principal |
      |                        | 6. calc seize amount     |
      |                        |   (DutchLiquidation      |
      |                        |    Module)               |
      |                        |                          |
      |                        | seize(amount,principal,  |
      |                        |       liquidator)        |
      |                        |------------------------->|
      |                        |                          |
      |                        |    Phase 1: Unlock       |
      |                        |    - cancel TWAMM order  |
      |                        |    - unwind V4 LP        |
      |                        |                          |
      |                        |    Phase 2: Sweep        |
      |                        |    - wRLP -> Core (burn) |
      |                        |    - collateral -> Liq.  |
      |                        |                          |
      |   collateral transfer  |<-------------------------|
      |<-----------------------|                          |
      |                        |                          |
      |                        | 7. burn wRLP             |
      |                        | 8. detect bad debt       |
      |                        | 9. slippage check        |
```

## Seize: Design & Priority

### Design Decision: Minimize Death Spirals

The seize mechanism is designed around one principle: **avoid market impact during liquidation**.

If the liquidator had to source all wRLP externally to repay debt, every liquidation would mean buying wRLP on the open market — driving the price up, increasing debt values for other positions, and potentially cascading into more liquidations. The protocol avoids this by:

1. **Burning existing wRLP first** — any wRLP already in the broker directly offsets debt without touching the market
2. **Unwinding positions only when necessary** — TWAMM orders and LP positions are only cancelled if the liquid balance isn't sufficient
3. **Sending collateral to the liquidator** — only after debt is covered from internal wRLP

This means the liquidator supplies less wRLP, the market sees less buy pressure, and cascade risk is minimized.

### Collateral Registration Limits

For gas efficiency, each PrimeBroker can register only **one TWAMM order** and **one V4 LP position** for solvency calculations at a time. Unregistered positions are invisible to the health check. This bounds the gas cost of `getNetAccountValue()` — which is called on every solvency check — to a predictable maximum.

### How Seize Works

When `RLDCore` calls `PrimeBroker.seize()`, the broker checks if its **liquid value** (wRLP + collateral token balances) covers the seize target:

**Step 1 — Check liquid balance.** If wRLP + collateral already covers the target, skip to sweep.

**Step 2 — Unlock TWAMM order (if needed).** Force-settle ghost balance, then cancel the order. This converts the streaming order back into wRLP (unsold) and collateral (earned). Re-check liquid value — stop if sufficient.

**Step 3 — Unwind V4 LP (if still needed).** Remove proportional liquidity from the LP position — only enough to cover the remaining shortfall.

**Step 4 — Sweep assets by priority:**

| Priority | Token          | Destination | Purpose                                                           |
| -------- | -------------- | ----------- | ----------------------------------------------------------------- |
| 1st      | **wRLP**       | RLDCore     | Burned to directly offset debt — reduces what the liquidator owes |
| 2nd      | **Collateral** | Liquidator  | Liquidator's reward (principal repayment + bonus)                 |

## Bad Debt Handling

If a position is so underwater that seized collateral cannot cover the debt, **bad debt** is created. After `seize()` completes, `RLDCore` checks if any `debtPrincipal` remains — if so, it registers the leftover as bad debt:

```
state.badDebt += pos.debtPrincipal;
pos.debtPrincipal = 0;  // clear the position entirely
```

### NF Socialization

Bad debt is **not absorbed instantly**. Instead, it is gradually socialized across all debtors in the market by increasing the normalization factor over time:

```
chunk    = badDebt × timeDelta / badDebtPeriod
NF      += chunk / totalSupply
badDebt -= chunk
```

- **`badDebtPeriod`** is a per-market configurable parameter (default **7 days**, max 90 days), tunable via curator timelock
- A minimum chunk size (`totalSupply / MIN_CHUNK_DIVISOR`) prevents infinitely slow socialization
- If chunk exceeds remaining bad debt, it's capped to clear the balance
- If `totalSupply = 0` (all positions closed), bad debt is **frozen** — it cannot be socialized until new positions are opened

The gradual spreading prevents sudden net worth shocks. A market with $100M in total debt absorbing $10k in bad debt would increase NF by ~0.01% over 7 days — negligible to individual positions.

> **For bond holders**: Synthetic bonds are designed with initial LTV of ~5% (health ratio ~20:1). Even a 20× rate spike (5% → 100%) only brings LTV to 50% — the wRLP in the TWAMM order scales with debt, acting as a natural hedge.

## Running a Liquidation Bot

Liquidation is permissionless and represents an MEV opportunity:

1. **Monitor positions** — Watch the indexer or query `isSolvent()` / `isSolventAfterFunding()` directly
2. **Calculate profitability** — The health-based bonus must exceed your gas costs
3. **Execute** — Call `RLDCore.liquidate(marketId, broker, amount, minCollateralOut)`
4. **Receive bonus** — Collateral is transferred directly to you

The `minCollateralOut` parameter provides slippage protection — the transaction reverts if the liquidator receives less collateral than expected.

### Key Rules

| Rule                    | Value                        | Purpose                                  |
| ----------------------- | ---------------------------- | ---------------------------------------- |
| **Close Factor**        | 50% (or 100% if underwater)  | Partial vs full liquidation              |
| **Min Liquidation**     | Per-market configurable      | Prevents dust liquidations               |
| **Permissionless**      | Anyone can call              | MEV-competitive liquidation market       |
| **Slippage Protection** | `minCollateralOut` parameter | Prevents sandwich attacks on liquidators |
