# Getting Started

> **Testnet Simulation**: RLD is currently available in a **testnet simulation** environment. To get started:
>
> 1. Click the **Faucet** button to request test funds
> 2. Deploy a broker and deposit collateral via the modal window
> 3. You're ready for perps trading and LP provision
>
> **For bonds**, you don't need a broker account — the `BondFactory` creates one automatically.

## Prerequisites

To use RLD Protocol, you need:

1. **A Web3 wallet** — MetaMask, Rabby, or any WalletConnect-compatible wallet
2. **Collateral tokens** — aUSDC (or the specific collateral token for your target market)
3. **ETH for gas** — Standard transaction fees on the deployment chain

## Connecting to RLD

1. Navigate to the RLD frontend
2. Click **Connect Wallet** in the top right
3. Select your wallet provider and approve the connection
4. Ensure you're on the correct network

## Creating a PrimeBroker Account

Before trading, you need a **PrimeBroker** — your smart contract wallet that holds all your assets:

1. Click **Create Account** on the dashboard
2. Approve the transaction (deploys your broker as a minimal proxy clone)
3. Your broker address appears in the header — this is also your NFT token ID

> **Note**: Creating a broker is permissionless and costs ~50k gas. You only need one per market.

## Understanding Your Dashboard

Once your broker is created, the dashboard shows:

| Panel                | What It Shows                                                          |
| -------------------- | ---------------------------------------------------------------------- |
| **Account Overview** | Your broker address, NFT ownership, total net worth                    |
| **Positions**        | Active positions: collateral deposited, debt outstanding, health ratio |
| **JTM Orders**       | Active streaming and limit orders: progress, earnings, time remaining  |
| **LP Positions**     | V4 liquidity positions: tick range, fees earned, current value         |
| **Operations**       | Transaction history: deposits, trades, JTM orders, liquidations        |

### Key Metrics

- **Net Worth**: Total value of all assets minus debt — ERC20 + LP + JTM orders - debt
- **Health Ratio**: Net Worth / (Debt × NF × IndexPrice × MaintenanceMargin). Stay above 1.0.
- **Debt**: Your outstanding wRLP obligation (principal × NF)
- **Index/Mark Price**: Current rate-derived price vs market price

## Next Steps

| Goal                    | Guide                                        |
| ----------------------- | -------------------------------------------- |
| Bet on rates going up   | [Going Long](./going-long)                   |
| Bet on rates going down | [Going Short](./going-short)                 |
| Earn fixed yield        | [Synthetic Bonds](./synthetic-bonds)         |
| Earn LP fees            | [Providing Liquidity](./providing-liquidity) |
