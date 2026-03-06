# Oracles

RLD uses a **three-oracle architecture** where each oracle serves a distinct purpose. This separation prevents circular dependencies and ensures manipulation resistance.

## Oracle Types

```
  INDEX PRICE                  MARK PRICE                COLLATERAL SPOT
  ──────────                   ──────────                ───────────────
  Aave V3 Pool                 JTM Hook (TWAP)           Chainlink Feeds
       │                            │                          │
       │ getReserveData()           │ observe()                │ latestRoundData()
       ▼                            ▼                          ▼
  RLDAaveOracle              V4SingletonOracle         ChainlinkSpotOracle
       │                            │                          │
       │ P = K × r                  │ Arithmetic Mean Tick     │ Normalized to 1e18
       ▼                            ▼                          ▼
  Index Price                  Mark Price               Spot Price
  (e.g. $5.00)                (e.g. $5.15)             (e.g. $1.001)
       │                            │                          │
       │ Funding rate               │ Solvency                 │ Collateral
       │ calculation                │ checks                   │ valuation
       └────────────────┐     ┌─────┘     ┌────────────────────┘
                        ▼     ▼           ▼
                    ┌──────────────────────────┐
                    │         RLDCore           │
                    └──────────────────────────┘
```

## Index Price — RLDAaveOracle

The **fundamental value** of wRLP, derived directly from the lending rate:

$$P_{index} = \frac{r_{borrow} \times K}{10^9}$$

Where:

- `r_borrow` = Aave V3's `currentVariableBorrowRate` (in RAY, 27 decimals)
- `K` = 100 (scaling constant)
- Division by `10^9` converts from RAY to WAD (18 decimals)

### Safety Bounds

| Bound       | Value               | Purpose                                      |
| ----------- | ------------------- | -------------------------------------------- |
| Rate Cap    | 100% (1e27 RAY)     | Prevents extreme prices from rate spikes     |
| Price Floor | \$0.0001 (1e14 WAD) | Prevents division-by-zero in downstream math |

### Used For

- Funding rate calculation (`FundingRate = (Mark - Index) / Index`)
- Debt valuation (`TrueDebt = principal × NF × indexPrice`)
- JTM broker module pricing (wRLP tokens valued at index price)

## Mark Price — UniswapV4SingletonOracle

The **market-determined price** of wRLP, derived from a Time-Weighted Average Price (TWAP) over the V4 pool.

### How TWAP Works

1. The JTM hook maintains its own TWAP oracle — a ring buffer of tick observations
2. On every pool interaction, the current tick is recorded
3. The oracle computes the arithmetic mean tick over a configurable window (default: 1 hour)
4. The mean tick is converted to a price via Uniswap V4's `TickMath`

### Why TWAP, Not Spot

**Spot price** (the current tick) can be manipulated within a single block:

- Flash loan → massive swap → manipulated price → profitable liquidation → swap back

**TWAP** requires sustained price manipulation over the lookback window:

- Manipulating a 1-hour TWAP on a liquid pool would require billions in capital held across multiple blocks
- Economically infeasible for any rational attacker

### Additional Protections

The JTM hook enforces **immutable price bounds** set at market genesis:

- No swap can move the price outside `[minSqrtPrice, maxSqrtPrice]`
- Bounds are derived from the rate oracle's practical limits
- Set once per pool, cannot be overwritten

### Used For

- Solvency checks (is the position healthy?)
- JTM internal matching (netting + JIT fills use TWAP pricing)
- Funding rate calculation (mark component)

## Collateral Spot Price — ChainlinkSpotOracle

For markets where collateral isn't a stablecoin, a Chainlink price feed provides the **collateral-to-underlying exchange rate**.

### Features

- Supports direct and inverse feed configurations
- Normalizes all prices to WAD (18 decimals)
- Per-pair feed registration via mapping

### Used For

- Collateral valuation in NAV calculations
- Liquidation seize amount calculations

## Singleton Design

The `UniswapV4SingletonOracle` manages TWAP queries for **all** V4 pools from a single contract. This avoids deploying per-market oracle adapters, saving gas and simplifying management. Pools are registered via `registerPool()` (owner-only, for security).
