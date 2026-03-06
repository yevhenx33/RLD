# Funding Mechanism

## Why Funding Exists

RLD is a **perpetual** contract — it has no expiry date. Unlike futures with a settlement date that forces convergence, perpetuals need an active mechanism to keep the market price (mark) aligned with the fundamental value (index).

That mechanism is **funding**.

## How It Works

Funding transfers value between longs and shorts based on the divergence between mark and index prices:

| Condition    | Who Pays   | Who Earns   | Effect                                  |
| ------------ | ---------- | ----------- | --------------------------------------- |
| Mark > Index | Longs pay  | Shorts earn | Incentivizes selling → brings mark down |
| Mark < Index | Shorts pay | Longs earn  | Incentivizes buying → pushes mark up    |
| Mark = Index | No one     | No one      | Equilibrium                             |

## The Normalization Factor (NF)

Rather than explicit cash transfers, RLD uses the **Normalization Factor** to apply funding continuously:

$$NF(t + \Delta t) = NF(t) \times e^{-F \times \frac{\Delta t}{P}}$$

Where:

- **F** = Funding Rate = `(NormalizedMark - Index) / Index`
- **NormalizedMark** = `Mark / NF` — the mark price is divided by the current NF to make it comparable with the index price
- **P** = Funding Period (per-market, set at market creation, default **30 days** = 2,592,000 seconds)
- **Δt** = time elapsed since last update

### Funding Period

The funding period determines how quickly the normalization factor responds to mark-index divergence. It is set as an **immutable parameter** at market creation via `RLDMarketFactory` and stored in the market's config. If not specified, `StandardFundingModel` uses the default of **30 days**.

A shorter period means more aggressive convergence — the same rate divergence moves NF faster. A longer period smooths out short-term volatility but converges slower.

### What NF Does

Every position's **true debt** is:

$$\text{True Debt} = \text{debtPrincipal} \times NF$$

- NF starts at 1.0 when a market is created
- When **mark > index** (F > 0): the exponent is negative → NF **decreases** → true debt shrinks → shorts profit
- When **mark < index** (F < 0): the exponent is positive → NF **increases** → true debt grows → shorts pay

### Numeric Example

Starting conditions: NF = 1.0, Index = \$5.00, Mark = \$6.00

```
NormalizedMark = 6.00 / 1.0 = 6.00
FundingRate    = (6.00 - 5.00) / 5.00 = 0.20 (20%)
```

After 1 day (Δt = 86,400s, Period = 2,592,000s):

```
NF = 1.0 × e^(-0.20 × 86400/2592000)
   = 1.0 × e^(-0.00667)
   = 0.99335
```

A short with 1,000 wRLP principal:

- **Before**: True debt = 1,000 × 1.000 = 1,000 wRLP
- **After 1 day**: True debt = 1,000 × 0.993 = 993 wRLP
- **Profit**: ~7 wRLP worth of debt reduction

## Lazy Application

NF is **not** updated every block. Instead, it updates lazily — whenever someone interacts with the market:

- Opening or closing a position → `applyFunding()` is called automatically
- Adding liquidity → triggers funding update
- Anyone can also call `applyFunding()` directly (permissionless)

This design saves gas — the protocol doesn't need a keeper to update every block. The math accounts for arbitrary time gaps via the exponential formula.

## Funding and Bonds

For synthetic bonds, funding is part of the yield calculation:

- If the bond is short wRLP and mark > index, the bond **earns** funding (NF decreases, debt shrinks) — this increases yield
- If mark < index, the bond **pays** funding — this reduces yield but is partially offset by the TWAMM execution itself

Monte Carlo simulations show that over typical bond durations (30-90 days), funding contributes positively to bond yield in most rate regimes.
