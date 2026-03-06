# Going Long

Going long means **buying wRLP** — you profit when interest rates increase.

## When to Go Long

- You expect lending rates to **increase**
- You want to **hedge** against rising borrowing costs
- You want **parametric insurance** against protocol stress (rates spike during crises)

## Step by Step

### 1. Get Collateral

You need the pool's quote token (e.g., USDC or aUSDC) to buy wRLP.

### 2. Buy wRLP

**Option A — Open Long from the perps page:**

1. Navigate to the **Perps** page
2. Click **Open Long**
3. Enter the amount of waUSDC to spend
4. Review the swap preview — this is a simple waUSDC → wRLP swap on the V4 pool
5. Approve the transaction

**Option B — Via JTM streaming order (for large positions):**

1. Submit a JTM streaming buy order
2. Your waUSDC gradually converts to wRLP over the order duration
3. Benefit from Layer 1 netting and Layer 2 JIT fills — potentially better execution than a spot swap

### 3. Monitor Your Position

- **wRLP balance**: Check in your wallet or broker dashboard
- **Current value**: wRLP amount × current mark price
- **Funding**: If mark > index, you're **paying** funding (wRLP value slowly decreases via NF). If mark < index, you're **earning**.

### 4. Close — Sell wRLP

When you want to exit:

1. Sell wRLP on the V4 pool for USDC
2. Or submit a JTM streaming sell order for gradual exit
3. Profit = (sell price - buy price) × amount ± cumulative funding

## Example

| Step       | Action                 | Price           | Amount                |
| ---------- | ---------------------- | --------------- | --------------------- |
| Buy        | Swap 5,000 USDC → wRLP | \$5.00          | 1,000 wRLP            |
| Hold       | Rates rise 5% → 8%     | \$5.00 → \$8.00 | —                     |
| Sell       | Swap 1,000 wRLP → USDC | \$8.00          | 8,000 USDC            |
| **Result** |                        |                 | **+3,000 USDC (60%)** |

## Risks

| Risk            | Impact                            | Mitigation                                        |
| --------------- | --------------------------------- | ------------------------------------------------- |
| Rates decrease  | wRLP price drops                  | Set a stop-loss by placing a JTM limit sell order |
| Adverse funding | Mark > index → you pay            | Monitor funding rate, exit if sustained           |
| Liquidity       | Large positions may have slippage | Use JTM streaming orders for gradual entry/exit   |

## Tips

- **No broker needed** for simple longs — just hold wRLP in your wallet
- **Cross-margin**: If you deposit wRLP into a broker, it counts toward NAV alongside other assets
- **JTM orders**: For positions >$10k, streaming orders typically provide better execution than spot swaps
