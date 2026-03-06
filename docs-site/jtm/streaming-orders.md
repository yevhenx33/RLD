# Streaming Orders (TWAP)

Streaming orders sell tokens at a constant rate over a specified duration — like a drip feed. They're designed for large position entry/exit where a single swap would cause too much price impact, and they're the execution mechanism behind [synthetic bonds](../guides/synthetic-bonds).

## How It Works

You deposit tokens and choose a duration. The system calculates a **sell rate** (tokens per second) and streams them into the ghost balance over time:

```
  You deposit 10,000 wRLP for 30 days
  Sell rate = 10,000 / 2,592,000 seconds ≈ 0.00386 wRLP/sec

  Day 0          Day 10         Day 20         Day 30
   │               │              │              │
   ▼               ▼              ▼              ▼
   ├───────────────┼──────────────┼──────────────┤
   │    Tokens stream into ghost balance         │
   │    continuously at fixed rate               │
   └─────────────────────────────────────────────┘

   Ghost filled by:
   ├── Layer 1: Netted against opposing streams (free)
   ├── Layer 2: JIT-filled when takers swap (free)
   └── Layer 3: Cleared by arbs via Dutch auction (small discount)
```

Your tokens don't sit idle — they're actively being matched through the [3-layer engine](./design-evolution#three-layers--better-for-everyone) as they accrue.

## Placing an Order

From the **Perps** page:

1. Select **TWAP Order** in the Action menu
2. Choose direction (sell wRLP or sell waUSDC)
3. Enter the total amount to sell
4. Set the duration (must align to 1-hour epochs)
5. Review the sell rate and submit

### Duration Alignment

Durations must be multiples of the **expiration interval** (1 hour / 3,600 seconds). Examples:

| Duration | Epochs |
| -------- | ------ |
| 1 hour   | 1      |
| 6 hours  | 6      |
| 1 day    | 24     |
| 7 days   | 168    |
| 30 days  | 720    |
| 365 days | 8,760  |

### Deferred Start

Orders don't go live the moment you submit them — they **wait for the next epoch boundary**. This means your order's actual start and expiration are snapped to the 1-hour grid:

```
  Example: You submit a 1-hour order at 3:33 PM

  3:33 PM           4:00 PM                      5:00 PM
     │                 │                           │
     │  submitted      │      order goes LIVE      │ order EXPIRES
     │  (pending)      │      streaming begins     │ claim earnings
     └─────────────────┼───────────────────────────┤
                       │◄── actual 1-hour order ──►│
```

Your tokens are transferred at submission, but streaming only begins at the next epoch. The dashboard shows a **Pending** status until the order goes live.

### Order Identity

Each order is uniquely identified by your address + direction + expiration epoch. This means you can have **one order per direction per epoch**. To place multiple simultaneous orders, use different durations so they expire in different epochs.

## Order Lifecycle

```
  submitOrder()
       │
       ▼
  ┌──────────┐     Tokens stream as ghost balance
  │  Active  │───► Ghost filled via netting / JIT / auction
  └────┬─────┘     Earnings accumulate proportionally
       │
       ├────── cancelOrder() ──► Refund unsold + claim earned
       │        (before expiry)
       │
       └────── Expiration reached
                    │
                    ▼
               sync() ──► claimTokens() ──► Tokens in your wallet
```

### While Active

- Tokens stream continuously at your sell rate
- Ghost balances are filled through the 3-layer engine
- Earnings accumulate — you can check progress anytime

### Cancellation

You can cancel before expiration:

- Unsold tokens are refunded based on time remaining
- Already-earned tokens are credited and claimable
- No penalty for early cancellation

### Claiming After Expiration

Once your order expires:

1. Call **Sync** to calculate your final earnings
2. Call **Claim** to transfer earned tokens to your wallet

Or use **Sync & Claim** to do both in one transaction.

## Monitoring Your Order

The dashboard shows:

| Metric               | What It Means                                     |
| -------------------- | ------------------------------------------------- |
| **Progress**         | Percentage of duration elapsed                    |
| **Tokens Sold**      | How much of your deposit has been streamed so far |
| **Tokens Remaining** | Unsold amount still streaming                     |
| **Earnings**         | Accumulated buy-side tokens ready to claim        |
| **Status**           | Active, Pending, Expired, or Claimed              |

## Role in Synthetic Bonds

Streaming orders are the backbone of the bond product:

1. `BondFactory.mintBond()` automatically creates a short position + streaming sell order in one transaction
2. The sell order gradually converts wRLP → waUSDC over the bond duration
3. The order's value counts toward the broker's net worth (via `JTMBrokerModule`)
4. At maturity, the order has fully unwound — debt is repaid from proceeds

This is why streaming orders are foundational to RLD — they're not just a trading feature, they're the **core infrastructure for fixed-yield bonds**.

## Common Issues

| Problem              | Cause                                           | Solution                                    |
| -------------------- | ----------------------------------------------- | ------------------------------------------- |
| Duration rejected    | Not aligned to 1-hour epochs                    | Use a multiple of 3,600 seconds             |
| Sell rate is zero    | Amount too small for the chosen duration        | Increase amount or decrease duration        |
| Order already exists | Same direction and expiration as existing order | Cancel existing or use a different duration |
