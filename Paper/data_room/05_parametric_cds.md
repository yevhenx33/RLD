# Parametric Credit Default Swaps (PCDS)

## Abstract
Decentralized lending protocols manage billions in value, yet their algorithmic interest rate models natively fail to price heavy-tailed Jump-to-Default risks. Parametric Credit Default Swaps (PCDS) introduce a fully automated, zero-discretion insurance market that utilizes algorithmic rate spikes as deterministic solvency oracles, shielding depositors from catastrophic protocol insolvency.

## 1. The Economic Isomorphism of DeFi Lending

To formalize the necessity of a continuous-time CDS, we must define the economic reality of decentralized, non-recourse debt. 

Relying strictly on cryptographic escrow, DeFi lending structurally mirrors the Merton Model of Corporate Debt. Supplying liquidity to a DeFi protocol is mathematically isomorphic to holding a risk-free bond $D$ while simultaneously writing a cash-secured put option $\max(D - V_t, 0)$ to the borrower, where $V_t$ is the collateral value. 

### 1.1 The Failure of Algorithmic Rates
While algorithmic liquidators can successfully delta-hedge continuous price diffusion, modern lending pools are heavily composed of yield-bearing synthetic assets (LSTs, LRTs). These assets are exposed to Poisson jump-to-default (JTD) risk—such as a smart contract exploit—where the asset gaps instantaneously to zero.

During a JTD event, secondary market liquidity evaporates. Liquidators fail to intervene, and the passive supplier's deeply out-of-the-money put option is violently exercised. Because automated Interest Rate Models (IRMs) solely price utilization-based capital scarcity and cannot dynamically price credit spreads, passive lenders are currently underwriting systemic tail-risk uncompensated.

## 2. Unbundling Risk via Parametric CDS

The PCDS architecture resolves this market failure by unbundling the Merton identity.

A passive supplier uses a fraction of their yield to stream a continuous funding rate $F$ to purchase a Long RLP (CDS) token. Synthetically, they buy back the exact put option they implicitly sold to the borrower, entirely neutralizing their JTD exposure:

$$ \underbrace{[D - \max(D - V_t, 0)]}_{\text{Passive Lending Position}} + \underbrace{\max(D - V_t, 0)}_{\text{Protective Put}} = D $$

### 2.1 The Solvency Oracle
Rather than relying on human arbitration to declare a "hack" or "depeg", PCDS uses the lending protocol's own mathematical behavior as the oracle.

When a liquidity crisis or jump-to-default event occurs, a "bank run" inevitably strands bad debt and forces pool utilization to its deterministic absolute maximum ($U_t \to 1$). Consequently, the algorithmic interest rate mechanically spikes to its hard-capped maximum limit (e.g., 75% or 100%).

By defining the index price as a scalar of the borrowing rate ($P = K \cdot r_t$), the derivative behaves as a liquid Put Option. As the underlying protocol approaches insolvency, the derivative's payout accelerates convexly, delivering a massive 10x+ instant return that perfectly indemnifies the insured principal.

## 3. Structural Constraints and Market Safety

To guarantee solvency and preclude systemic contagion, the PCDS architecture enforces rigorous, mathematically verifiable boundaries.

### 3.1 Constraint 1: Absolute Liability Bound
The IRM is mathematically capped at a maximum rate ($r_{max}$). Therefore, the maximum intrinsic liability of the derivative is deterministically capped at $P_{max} = K \cdot r_{max}$. Underwriters must escrow exactly $P_{max}$ in collateral at minting, establishing a strict upper boundary condition that guarantees payout solvency under the absolute worst-case scenario.

### 3.2 Constraint 2: Collateral Orthogonality
To prevent recursive dependency (the "Burning House" paradox), underwriters must post strictly exogenous collateral (e.g., ETH to insure USDC). If an underwriter backed USDC insurance using Aave USDC as collateral, a USDC depeg would simultaneously trigger the payout and destroy the collateral. Orthogonality ensures that payout liquidity structurally survives the very event it insures against.

### 3.3 Symbiotic Yield Stacking
Constraint 2 allows underwriters to utilize cross-margin functionality by posting yield-bearing assets (like `wstETH` or tokenized T-Bills) as exogenous escrow. 
This establishes a highly lucrative yield-stacking loop. Underwriters accrue the endogenous native yield (e.g., 3.5% staking reward) concurrently with the systemic continuous underwriting premium ($Y_{CDS}$ of ~5-6%). This converts passive collateral into a delta-neutral, highly productive systemic backstop.
