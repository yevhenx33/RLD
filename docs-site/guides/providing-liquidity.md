# Providing Liquidity

You can earn trading fees by providing concentrated liquidity on the Uniswap V4 wRLP/collateral pool. If you provide liquidity through your PrimeBroker, the LP position also counts toward your [cross-margin NAV](../architecture/prime-broker#cross-margin).

## How V4 Concentrated Liquidity Works

Unlike full-range liquidity, V4 (like V3) allows you to concentrate your capital within a specific **tick range**:

- Your liquidity only earns fees when the price is within your range
- Narrower range = more capital efficiency = more fees per dollar, but higher impermanent loss risk
- If the price moves outside your range, your position becomes 100% one token

## Adding Liquidity

### Via the Frontend

1. Navigate to the **Pool** page
2. Select the wRLP/USDC market
3. Choose your tick range:
   - **Wide range**: lower risk, lower fee APR
   - **Narrow range**: higher risk, higher fee APR
   - **Full range**: simplest, lowest capital efficiency
4. Enter the amounts of wRLP and USDC
5. Click **Add Liquidity** and approve

### Through Your PrimeBroker

When adding liquidity through your broker, the LP position is stored inside the broker and automatically counts toward your NAV via the `UniswapV4BrokerModule`:

| Component              | How It's Valued                            |
| ---------------------- | ------------------------------------------ |
| Token0 amount in range | `getAmountsForLiquidity()` at current tick |
| Token1 amount in range | Same computation                           |
| Uncollected fees       | Accrued fee amounts                        |
| wRLP component         | Valued at index price (not mark)           |

This means you can:

1. Deposit collateral → mint wRLP → LP with the wRLP
2. The LP position counts as collateral for your short
3. Earn trading fees while maintaining your position

## Fee Income

LP fees are generated from:

- Regular V4 swaps through the pool
- JTM Layer 2 JIT fills (takers still pay pool fees)

The fee tier is set at market creation and applies to all swaps. Fees accrue to LP positions proportionally to their liquidity share within the active tick range.

## Impermanent Loss (IL)

As with any AMM LP position, providing liquidity carries IL risk:

- If wRLP price moves significantly in either direction, your position shifts composition
- Price moving up → you hold more USDC, less wRLP
- Price moving down → you hold more wRLP, less USDC
- IL is only realized when you withdraw at a different price than entry

### IL vs Fee Income

The trade-off is straightforward:

- Higher trading volume → more fees → can offset IL
- Lower volatility → less IL → net positive from fees
- Higher volatility → more IL → may exceed fee income

## Tick Range Strategy

| Strategy       | Range               | Fee APR | IL Risk | Best For                |
| -------------- | ------------------- | ------- | ------- | ----------------------- |
| **Full Range** | All ticks           | Low     | Low     | Passive, Set-and-forget |
| **Wide**       | ±50% around current | Medium  | Medium  | Moderate activity       |
| **Narrow**     | ±10% around current | High    | High    | Active management       |

> **Tip**: For RLD markets, rates tend to mean-revert within a range. A range of ±30% around the current index price captures most trading activity while limiting IL.

## Removing Liquidity

1. Navigate to your LP position on the dashboard
2. Click **Remove Liquidity**
3. Choose the percentage to withdraw (partial or full)
4. Approve — tokens and accrued fees return to your wallet or broker

If removing from your broker, ensure the remaining NAV still satisfies your maintenance margin.
