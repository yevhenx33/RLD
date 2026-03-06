# Positions & Solvency

## Opening a Position

### Short Position (Minting)

1. **Create a PrimeBroker** — `PrimeBrokerFactory.createBroker()` (permissionless, one-time)
2. **Deposit collateral** — Transfer aUSDC (or other collateral) into your broker
3. **Mint debt** — Borrow wRLP against your collateral
4. **Swap** — Sell the minted wRLP on the V4 pool for collateral tokens
5. **Redeposit** — Deposit the swap proceeds back into your broker as additional collateral

### Long Position (Buying)

1. **Buy wRLP** directly on the Uniswap V4 pool
2. No broker needed for a simple long — you just hold wRLP tokens
3. For cross-margin benefits, deposit wRLP into a broker alongside other assets

## Position Health

Every short position must maintain sufficient collateral. Health is computed using **net worth** — not raw asset value — to prevent double-counting:

$$\text{Health} = \frac{\text{Net Worth}}{\text{debtPrincipal} \times NF \times P_{index} \times \text{maintenanceMargin}}$$

Where:

- **Net Worth** = total value of all assets in the PrimeBroker **minus** outstanding debt
- **debtPrincipal** = the original amount of wRLP minted
- **NF** = current normalization factor (grows/shrinks with funding)
- **P_index** = current index price from the rate oracle
- **maintenanceMargin** = 1.09 (109%) typically

> **Why net worth, not NAV?** If we counted raw asset value without subtracting debt, a user could mint wRLP, hold it in the broker, and the minted tokens would count as collateral for themselves — creating an infinite leverage glitch. Net worth ensures that debt always cancels out the minted tokens.

### Net Worth Components

Your net worth includes **everything** in your PrimeBroker, minus debt. There are four asset types, each decomposable into two underlying tokens:

| Component                     | Contains                                                                    | Valuation                            |
| ----------------------------- | --------------------------------------------------------------------------- | ------------------------------------ |
| **Collateral Token** (waUSDC) | Direct ERC20 balance                                                        | **1:1** face value                   |
| **Position Token** (wRLP)     | Direct ERC20 balance                                                        | **min(Index, Mark)** oracle price    |
| **TWAMM Orders**              | Collateral + position tokens (unsold wRLP, earned collateral, ghost)        | Each token valued per its rule above |
| **LP Positions**              | Collateral + position tokens (decomposed from concentrated liquidity range) | Each token valued per its rule above |

```
Net Worth = Σ all collateral tokens × 1
          + Σ all position tokens  × min(Index, Mark)
          - debt
```

The broker modules (JTMBrokerModule, V4BrokerModule) extract the collateral and position token amounts from TWAMM orders and LP positions respectively. The final solvency check only needs two totals — no per-asset-type valuation logic at the core level.

> **Why min(Index, Mark)?** Using the lower of the two prices prevents a user from inflating their net worth by manipulating the mark price above the fundamental index price.

This is the **cross-margin** advantage of the PrimeBroker — all asset types contribute to one unified net worth.
