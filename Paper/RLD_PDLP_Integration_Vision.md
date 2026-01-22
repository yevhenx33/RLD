# RLD + PDLP Integration V2: The "Leveraged Strategy" Engine

## Executive Summary

This document outlines the **Streamlined V2 Strategy** for integrating the **Risk-Adjusted Lending Protocol (RLD)** with the **Perpetual Demand Lending Pool (PDLP)**.

Instead of a complex multi-creditor mesh, we adopt a **vertical integration model**:

- **RLD** acts as the **Bank** (Senior Lender).
- **PDLP** acts as the **Hedge Fund** (Yield Generator).
- **Prime Broker** acts as the **Unified Account** (Collateral Manager).

## 1. The Core Value Proposition

**"Leveraged Yield Farming on Delta-Neutral Strategies"**

Users can deposit stablecoins, enter complex delta-neutral yield strategies via PDLP, and then **loop** that position using RLD's credit lines to significantly enhance APY while maintaining a hedged risk profile.

---

## 2. Architecture: Hub & Spoke

### A. The Hub: PrimeBroker (User Account)

The `PrimeBroker` contract remains the central primitive. It holds assets and manages solvency.

- **Creditor**: The Broker is bound to **one** Lending Market (RLD) to prevent lien conflicts.
- **Assets**: The Broker can hold diverse assets, now including **PDLP Shares**.

### B. The Spoke: PDLP (Strategy Layer)

PDLP functions as a "Black Box" Strategy Factory.

- **Input**: USDC / ETH.
- **Output**: `dnPDLP` (Delta-Neutral Share Tokens) or `dirPDLP` (Directional Share Tokens).
- **Mechanism**: PDLP handles all complex Euler hedging, Uniswap V4 Hook interactions, and rebalancing internally.
- **Interface**: It exposes a simple ERC-20 interface, making it composable.

### C. The Spoke: RLD (Lending Layer)

RLD functions as the vanilla lending core.

- **Input**: PDLP Shares (as Collateral).
- **Output**: Stablecoins (USDC) or ETH Debt.
- **Mechanism**: RLD trusts the Prime Broker's valuation of the PDLP shares to issue credit.

---

## 3. The User Workflow (The "Loop")

### Step 1: Capital Injection

- User deposits **100k USDC** into their `PrimeBroker`.

### Step 2: Strategy Entry

- User instructs Broker to deposit 100k USDC into PDLP's **Delta-Neutral Vault**.
- Broker receives **100k `dnPDLP` shares**.
- _Performance_: These shares yield ~15% APY from V4 trading fees + Euler lending interest.

### Step 3: Loop (Leverage)

- User instructs Broker to **borrow 70k USDC** from RLD.
- **Solvency Check**:
  - Broker calls `PDLPBrokerModule.getValue()`.
  - Module reads `PDLP.getSharePrice()` -> Value $100k.
  - RLD approves loan (70% LTV).
- User instructs Broker to deposit the borrowed 70k USDC back into PDLP.
- Broker receives **70k `dnPDLP` shares**.

### Step 4: Resulting Position

- **Total Assets**: 170k `dnPDLP` (yielding 15%).
- **Total Debt**: 70k USDC (costing 5%).
- **Net Equity**: $100k.
- **Net Yield**:
  - Gross Yield: $170k \* 15% = $25.5k.
  - Interest Expense: $70k \* 5% = $3.5k.
  - Net Profit: $22k.
  - **Effective APY**: 22% (vs 15% un-leveraged).

---

## 4. Technical Implementation

### A. The PDLPBrokerModule

A specialized adapter connects the Prime Broker to PDLP.

```solidity
contract PDLPBrokerModule is IBrokerModule {
    IPDLP public immutable pdlp;

    function getValue(address broker, bytes calldata) external view returns (uint256) {
        uint256 shares = pdlp.balanceOf(broker);
        // Direct NAV check - Manipulation Resistant
        uint256 price = pdlp.getSharePrice();
        return (shares * price) / 1e18;
    }

    // Liquidation Adapter
    function unwind(uint256 amountNeeded) external returns (uint256) {
        // Withdraws from PDLP to get liquid USDC
        pdlp.withdraw(amountNeeded);
        return amountNeeded;
    }
}
```

### B. Liquidation Logic

In the event of a market crash or strategy drawdown:

1.  **Trigger**: RLD calls `broker.seize(debtAmount)`.
2.  **Route**: Broker delegates to `PDLPBrokerModule`.
3.  **Action**:
    - Module converts `dnPDLP` shares back to USDC (atomically unwinding the Euler hedge inside PDLP).
    - Broker pays RLD the debt in USDC.
    - User retains remaining equity in `dnPDLP` shares.

---

## 5. Strategic Advantages

1.  **Simplicity**: No complex multi-creditor logic ("Creditor Registry") needed. RLD is the only creditor.
2.  **Safety**: PDLP shares are "Hard" collateral with verifiable on-chain NAV, unlike speculative altcoins.
3.  **Capital Efficiency**: Users get the best of both worlds—PDLP's advanced yield strategies and RLD's diverse borrowing power.
