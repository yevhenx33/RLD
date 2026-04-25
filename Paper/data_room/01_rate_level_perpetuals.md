# Rate-Level Perpetuals (RLP)

## Abstract
Traditional Decentralized Finance (DeFi) interest rates are highly volatile, exhibiting algorithmic responses to utilization shocks. Rate-Level Perpetuals (RLP) transform this ephemeral concept of "yield" into a persistent, tradable asset. By indexing a derivative to the borrowing rate of an underlying lending pool, RLP creates a unified primitive for interest rate speculation, hedging, and volatility trading.

## 1. Mechanism Design

The Rate-Level Perp utilizes a Collateralized Debt Position (CDP) architecture inspired by Power Perpetuals (White et al., 2021) and specifically the Squeeth (Squared ETH) implementation by Opyn. This design allows for the creation of a fungible ERC-20 token that tracks the borrowing interest rate of a specific on-chain lending pool (e.g., Aave USDC, Morpho USDT).

### 1.1 The Index Price
The RLP Index Price transforms the instantaneous annualized borrowing rate $r_t$ into a scalar dollar-denominated value:

$$ P_{index}(t) = K \cdot r_t $$

With a constant scalar $K=100$, an interest rate of 5% ($r=0.05$) equates to an index price of $5.00. This linear scalar ensures that derivative payouts are strictly proportional to the underlying rate dynamics; if the interest rate doubles, the fundamental value of the position precisely doubles.

### 1.2 Minting and Short Exposure
Participants seeking to hedge floating-rate assets or speculate on rate mean-reversion interact with the Vault to open Short RLP positions:

1.  **Collateralization**: The user deposits eligible assets (e.g., aUSDC, ETH, sUSDe).
2.  **Minting**: The user mints a quantity $Q$ of RLP tokens, assuming a debt recorded as $Q \cdot NF(t) \cdot P_{index}(t)$.
3.  **Liquidation Thresholds**: The Vault enforces rigorous over-collateralization parameters. If the debt value exceeds the liquidation threshold (e.g., 109% Collateralization Ratio) due to a severe rate spike, the collateral is auctioned to repurchase and burn the RLP debt.

This structure creates a fungible asset tracking $P_{index}$ without requiring complex cash-funding machinery, allowing the perpetual to trade on external Automated Market Makers (AMMs) like Uniswap V4.

### 1.3 Continuous Funding via Normalization Factor
Unlike traditional perpetual swaps that require discrete cash payments, RLP employs in-kind funding through a continuously decaying global Normalization Factor ($NF$).

The instantaneous Funding Rate $F$ is a function of the divergence between the secondary market price ($P_{mkt}$) and the fundamental index price:

$$ F = \frac{P_{mkt} - P_{index}}{P_{index}} $$

The Normalization Factor updates continuously to reflect this equilibrium shift:

$$ NF(t+\Delta t) = NF(t) \cdot (1 - F \cdot \Delta t) $$

*   **Premium ($P_{mkt} > P_{index}$):** The Normalization Factor decreases. This amortizes the debt burden for Shorts, effectively transferring value from Longs (who suffer continuous decay) to Shorts.
*   **Discount ($P_{mkt} < P_{index}$):** The Normalization Factor increases. Shorts experience debt inflation, subsidizing Longs to hold the undervalued position.

## 2. Market Microstructure

### 2.1 The Mean-Reversion Advantage on Uniswap V4
The RLP trades primarily in concentrated liquidity pools on Uniswap V4. Interest rates fundamentally differ from directional spot assets (e.g., ETH) because they exhibit strong mean-reverting properties. While asset prices trend infinitely, interest rates are bounded by macroeconomic forces and algorithmic ceilings, typically oscillating within a stable equilibrium (4%–12%).

This structural mean-reversion dramatically alters the liquidity provisioning calculus. Liquidity Providers (LPs) can concentrate capital tightly around the equilibrium band with a high statistical probability of capturing continuous fee generation while mitigating the permanent Impermanent Loss (IL) typical of trending assets.

### 2.2 Volatility Trading and Cointegration
Empirical analysis demonstrates that DeFi interest rates are highly cointegrated with crypto asset volatility. Because borrowing rates effectively represent the "cost of leverage," an RLP position functions as a direct proxy for implied market volatility.

*   **Positive Convexity**: During market melt-ups, algorithmic Interest Rate Models (IRMs) exhibit extreme convexity. For example, an 83% increase in Bitcoin price can induce a 502% increase in borrowing rates as leverage demand exhausts the utilization curve.
*   **The Lag Effect**: Borrowing rates typically lag spot price movements by 7-14 days. This predictable delay creates a highly lucrative statistical arbitrage window, allowing sophisticated actors to deploy delta-neutral rate-asset straddles that capture the explosive convexity of the rate spike while neutralizing directional asset risk.
