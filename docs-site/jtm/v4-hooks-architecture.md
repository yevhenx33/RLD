# V4 Hooks Architecture

The JTM is built as a **Uniswap V4 hook** — a plugin that intercepts every swap on the pool and fills orders internally before they hit the AMM. This is what powers all three JTM order types (streaming, limit, market) through a single unified engine.

## How It Works (Simplified)

When someone swaps on the V4 pool, the JTM hook runs **before** the AMM processes the trade:

```
  User submits swap (e.g., buy wRLP with waUSDC)
       │
       ▼
  ┌─────────────────────────────────────────────┐
  │             JTM Hook (beforeSwap)            │
  │                                              │
  │  1. Update oracle (TWAP price)               │
  │  2. Accrue streaming orders (ghost balances)  │
  │  3. Net opposing streams against each other   │
  │  4. Check: can we fill from ghost balances?   │
  │     ├── YES → Fill at TWAP price (no AMM)    │
  │     └── NO  → Pass through to AMM            │
  │  5. Match any triggered limit orders          │
  └─────────────────┬───────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────┐
  │         V4 AMM processes remainder           │
  └─────────────────────────────────────────────┘
                    │
                    ▼
  ┌─────────────────────────────────────────────┐
  │         JTM Hook (afterSwap)                 │
  │  Enforce price bounds — revert if outside    │
  │  safe range (prevents manipulation)          │
  └─────────────────────────────────────────────┘
```

The key insight: the user sees **one seamless swap**, but part of it may have been filled internally by JTM at TWAP price — often providing better execution than the AMM alone. The AMM only handles whatever volume JTM couldn't fill internally.

## Three Order Types, One Engine

All order types flow through the same hook, using the same ghost balance mechanics:

| Order Type           | How It Works                                                                          | Best For                                |
| -------------------- | ------------------------------------------------------------------------------------- | --------------------------------------- |
| **Streaming (TWAP)** | Tokens drip into the ghost balance at a fixed rate per second over the order duration | Bond unwinds, large position entry/exit |
| **Limit**            | Tokens sit as ghost balance, matched when TWAP crosses your trigger price             | Price-conditional execution             |
| **Market**           | Ghost balance fills incoming swaps immediately via JIT                                | Instant execution at TWAP price         |

They differ only in **when** tokens enter the ghost balance — the netting, filling, and clearing logic is shared.

## Price Safety

After every swap, the hook checks that the pool price stays within a bounded range:

- **Bounds are set at market creation** and are immutable
- Derived from the practical range of the rate oracle
- Any swap that would push the price outside these bounds **reverts**
- This prevents oracle manipulation — no single trade can distort the TWAP used for funding and solvency

## Why Uniswap V4?

JTM's design is only possible on V4 — earlier versions lack the required primitives:

| What JTM Needs                 | V2/V3   | V4                            |
| ------------------------------ | ------- | ----------------------------- |
| Intercept swaps before AMM     | ❌      | ✅ Hook callbacks             |
| Fill orders without AMM impact | ❌      | ✅ BeforeSwapDelta            |
| All pools in one contract      | ❌      | ✅ Singleton PoolManager      |
| Cheap temporary storage        | ❌      | ✅ EIP-1153 transient storage |
| Built-in TWAP oracle           | Limited | ✅ Per-pool observation slots |

The **BeforeSwapDelta** mechanism is the critical innovation — it lets the hook say _"I've already filled X tokens internally"_, so the AMM only processes the remainder. This is what makes zero-AMM-impact fills possible.
