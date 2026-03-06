# Clearing & Arbitrage

Clearing is how ghost balances — tokens accrued by streaming and limit orders — get converted into real, settled balances. This is **Layer 3** of the [3-layer matching engine](./design-evolution#three-layers--better-for-everyone).

## Why Clearing Exists

Layers 1 (netting) and 2 (JIT fill) handle ghost balances **for free**, but they're opportunistic:

- **Netting** requires opposing streams flowing in both directions
- **JIT fills** require incoming swaps that want to buy the ghost token

When no one is swapping or streaming in the opposite direction, ghost balances accumulate. Clearing provides a guaranteed way to settle them — through a **permissionless Dutch auction** where anyone can purchase ghost balances at a growing discount.

## How the Auction Works

After each clear, the discount resets to **0%** and grows linearly over time:

```
  Last clear                                         Cap reached
     │                                                    │
     ▼                                                    ▼
     0%────── 0.6% ────── 1.2% ────── 3% ───────────────  5%
     │         │            │          │                  │
     t=0      12s          24s        60s               100s
           (1 block)                                  (capped)
```

The longer ghost balances sit uncleared, the more profitable it becomes to clear them. This creates a **self-correcting market** — even in low-liquidity conditions, someone will eventually clear because the discount makes it worth their while.

### Default Parameters

| Parameter     | Default      | Meaning                             |
| ------------- | ------------ | ----------------------------------- |
| Discount rate | 5 bps/second | How fast the discount grows         |
| Max discount  | 500 bps (5%) | Cap to prevent excessive extraction |

## Who Clears?

Clearing is **permissionless** — anyone can call it. In practice, arbitrage bots monitor ghost balances and clear when the discount exceeds their gas costs.

### What Happens When Someone Clears

1. Arb detects accumulated ghost balance (e.g., 1,000 wRLP)
2. Current discount has grown to 2% since the last clear
3. Arb calls `clear()` — provides the buy-side tokens at TWAP × (1 − 2%)
4. Arb receives the ghost tokens at a 2% discount — their profit
5. Streaming/limit order makers receive the proceeds as settled earnings
6. Discount resets to 0% — cycle restarts

### Front-Running Protection

When calling `clear()`, the arb specifies a **minimum discount**. If another bot front-runs their transaction:

- The front-runner clears first and resets the discount to 0%
- The original arb's transaction sees 0% discount, which is below their minimum → **tx reverts**
- The arb loses nothing (just gas) — fundamentally different from sandwich attacks where victims lose funds

The arb can simply wait for the discount to regrow and try again.

## What This Means for Users

As a regular user (placing streaming or limit orders), you don't need to think about clearing at all. It happens automatically through the competitive arb market.

What matters to you:

- **Your tokens are always safe** — ghost balances are held by the hook contract
- **Fills are recorded instantly** — even before clearing, the 3-term valuation formula accounts for your ghost balance value
- **Clearing improves precision** — settled earnings are valued at full face value, while uncleared ghost gets a conservative discount
- **No action required** — arbs handle clearing; you just claim your earnings when ready

## For Bot Operators

Clearing is a competitive arbitrage opportunity:

1. **Monitor** ghost balances via events or direct contract reads
2. **Calculate** profitability: `profit = ghostAmount × TWAP × discount − gasCost`
3. **Execute** `clear()` with an appropriate `minDiscountBps` to protect against front-running
4. **Optimize** by batching clears across multiple pools in one transaction

The discount grows continuously, so there's a natural equilibrium: more competition → clears happen sooner → lower margins, but guaranteed settlement for makers.
