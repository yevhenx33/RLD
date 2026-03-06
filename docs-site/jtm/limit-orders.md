# Limit Orders

Ghost Balance Limit Orders use the **same engine** as [streaming orders](./streaming-orders) — ghost balances and JIT fills — but with a **price trigger** instead of time-based streaming.

## The Problem with Traditional Limit Orders

On-chain limit orders typically work by placing concentrated liquidity at a specific price. This sounds simple, but has a critical flaw:

```
  TRADITIONAL (Range Order)              JTM (Ghost Balance)

  1. Deposit ETH at $3,000               1. Deposit into hook
  2. Price crosses $3,000                2. TWAP crosses $3,000
  3. AMM converts ETH → USDC             3. JIT fill from ghost
  4. Price bounces back to $2,900        4. Price bounces back
  5. AMM converts USDC → ETH             5. Nothing happens
     Order un-fills itself!            Tokens already settled
```

In traditional range orders, the AMM can **un-fill** your order if the price bounces back — you end up exactly where you started. JTM avoids this entirely because ghost balances never enter the AMM pool.

## How It Works

### Placing a Limit Order

From the **Perps** page:

1. Select **Limit Order** in the Action menu
2. Enter the amount to sell
3. Set your **trigger price** (the TWAP price at which the order executes)
4. Submit — tokens transfer to the hook and wait as ghost balance

Your tokens sit safely in the hook's custody — invisible to the AMM, cannot be accidentally converted or un-filled.

### TWAP Gating — Why It's Manipulation-Resistant

Limit orders trigger on the **TWAP** (time-weighted average price), not the spot price. This is a key safety feature:

| Scenario         | Spot Price      | TWAP   | Limit at $3,000 | Result           |
| ---------------- | --------------- | ------ | --------------- | ---------------- |
| Flash loan spike | $3,200          | $2,810 | Not triggered   | Hook ignores ✅  |
| Sustained move   | $3,050          | $3,010 | Triggered       | Fill executes ✅ |
| Brief wick       | $3,100 → $2,800 | $2,900 | Not triggered   | Hook ignores ✅  |

Manipulating the TWAP requires sustaining an artificial price for the entire averaging window — far more expensive than a single-block flash loan attack.

### Execution

When a swap arrives and the TWAP crosses your trigger price:

1. The hook detects that your limit order conditions are met
2. Fills the taker's swap from your ghost balance at TWAP price
3. Proceeds accumulate — you receive the other side's tokens
4. The taker sees a seamless swap (they don't know part came from a limit order)

### Internal Netting

Opposing limit orders can match directly:

- **Bob** places: Sell wRLP at $5.50
- **Charlie** places: Buy wRLP at $5.60
- TWAP moves to $5.55 — both conditions met

Orders match internally at TWAP. **Zero AMM fees, zero slippage** — same netting engine as streaming orders.

### Claiming

Once your order has been filled (partially or fully):

1. Call **Claim** to receive your proceeds
2. Any unfilled portion remains active and can be cancelled

## Key Differences from Streaming Orders

|                    | Streaming                        | Limit                                  |
| ------------------ | -------------------------------- | -------------------------------------- |
| **Trigger**        | Time (constant drip)             | Price (TWAP crosses target)            |
| **Duration**       | Fixed, epoch-aligned             | Open-ended (until filled or cancelled) |
| **Use case**       | Large position entry/exit, bonds | Price-conditional execution            |
| **Fill mechanism** | Same ghost balance + JIT engine  | Same ghost balance + JIT engine        |

## Security

| Attack                | Defense                                                          |
| --------------------- | ---------------------------------------------------------------- |
| Flash loan trigger    | TWAP gating — single-block manipulation barely moves the average |
| Bounce-back (un-fill) | Ghost balances never enter the AMM — structurally impossible     |
| Orderbook spam        | Gas fees for placement + tick spacing constraints                |
