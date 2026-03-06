# Key Concepts

## Index Price

The fundamental pricing formula for RLD:

$$P = K \times r$$

Where:

- **K** = 100 (scaling constant)
- **r** = the lending pool's borrow rate (as a decimal)

| Borrow Rate | Index Price |
| ----------- | ----------- |
| 2%          | \$2.00      |
| 5%          | \$5.00      |
| 10%         | \$10.00     |
| 50%         | \$50.00     |

The index price has a floor at \$0.0001 (prevents division-by-zero) and a ceiling at \$100.00 (corresponding to a 100% rate).

## wRLP — The Position Token

**wRLP** (wrapped Rate-Level Position) is the ERC-20 token at the center of RLD. It's the derivative that tracks the interest rate.

- **Minting**: Deposit collateral → borrow wRLP against it (creates debt)
- **Buying**: Simply buy wRLP on the Uniswap V4 pool
- **Burning**: Return wRLP to repay debt → unlock collateral

wRLP trades on a Uniswap V4 pool against the collateral token (e.g., wRLP/aUSDC). Its **mark price** is determined by supply and demand on this pool.

## Long vs Short

|                     | Long                                      | Short                                               |
| ------------------- | ----------------------------------------- | --------------------------------------------------- |
| **Action**          | Buy wRLP on the pool                      | Deposit collateral → mint wRLP → sell it            |
| **You profit when** | Rates increase (wRLP price goes up)       | Rates decrease (wRLP price goes down)               |
| **Risk**            | wRLP price drops to zero (rates go to 0%) | Rates spike → insufficient collateral → liquidation |
| **Max loss**        | Your wRLP purchase amount                 | Your deposited collateral                           |
| **Funding**         | Pay when mark > index                     | Earn when mark > index                              |

## Normalization Factor (NF)

The **Normalization Factor** is how RLD implements continuous funding — the mechanism that keeps the mark price converging toward the index price.

### How It Works

Every position's true debt is calculated as:

$$\text{True Debt} = \text{Principal} \times NF$$

NF starts at 1.0 and changes over time:

- **Mark > Index** (longs paying): NF decreases → shorts' real debt shrinks → shorts profit
- **Mark < Index** (shorts paying): NF increases → shorts' real debt grows → longs profit

### The Formula

$$NF(t + \Delta t) = NF(t) \times e^{-\text{FundingRate} \times \frac{\Delta t}{\text{Period}}}$$

Where:

- **FundingRate** = (NormalizedMark - Index) / Index
- **Period** = 30 days (default funding period)

NF is applied **lazily** — it only updates when someone interacts with the market (opens a position, applies funding, etc.).

## Collateral & Solvency

Every short position must be **overcollateralized** — the value of your assets must exceed your debt by a required margin.

### Health Ratio

$$\text{Health} = \frac{\text{Net Worth}}{\text{True Debt} \times \text{Index Price} \times \text{Maintenance Margin}}$$

Where **Net Worth** = total value of all assets in your [Prime Broker](../architecture/prime-broker) **minus** outstanding debt:

- ERC20 token balances (collateral at 1:1, wRLP at min(Index, Mark))
- Uniswap V4 LP position values
- JTM streaming order values
- Minus debt

| Health | Status                                                           |
| ------ | ---------------------------------------------------------------- |
| > 1.0  | ✅ Healthy — position is safe                                    |
| = 1.0  | ⚠️ At maintenance margin — any adverse move triggers liquidation |
| < 1.0  | ❌ Liquidatable — anyone can liquidate for a bonus               |

### Risk Parameters

| Parameter                    | Typical Value | Purpose                                |
| ---------------------------- | ------------- | -------------------------------------- |
| **Minimum Collateral Ratio** | 120%          | Required to open a new position        |
| **Maintenance Margin**       | 109%          | Liquidation threshold                  |
| **Close Factor**             | 50%           | Max % of debt liquidatable in one call |

## Mark Price vs Index Price

RLD uses two different prices for different purposes:

| Price           | Source                     | Purpose                                           |
| --------------- | -------------------------- | ------------------------------------------------- |
| **Index Price** | Aave borrow rate → `K × r` | Fundamental value; used for funding calculations  |
| **Mark Price**  | TWAP from the V4 pool      | Market-determined value; used for solvency checks |

The **funding mechanism** drives convergence: when mark diverges from index, funding payments incentivize traders to close the gap.

## Markets

An RLD **market** is defined by three components:

1. **Underlying Pool** — the lending pool being tracked (e.g., Aave V3)
2. **Underlying Token** — the asset whose rate is tracked (e.g., USDC)
3. **Collateral Token** — what shorts deposit (e.g., aUSDC)

Each market has its own wRLP token, V4 pool, and risk parameters. Markets are created by the protocol's factory and managed by curators who can propose risk parameter changes subject to a 7-day timelock.
