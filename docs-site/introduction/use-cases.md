# Use Cases

## 1. Synthetic Bonds — Fixed Yield

**Scenario**: You want predictable yield on your stablecoins, regardless of what happens to Aave's variable rate.

**How it works**:

1. Deposit \$10,000 aUSDC as collateral
2. `BondFactory.mintBond()` atomically:
   - Mints wRLP debt at the current rate (e.g., 5.5% → index price \$5.50)
   - Submits a JTM streaming sell order to gradually sell the wRLP over 90 days
3. Over 90 days, the TWAMM order gradually sells wRLP → USDC, locking in the **entry rate** as yield
4. At maturity: the short position has wound down — debt is perfectly cancelled by the held asset (bought back wRLP matches outstanding debt)

**Example yield calculation** (entry rate = 5.5% APY):

| Component                       | Value      |
| ------------------------------- | ---------- |
| Collateral deposited            | \$10,000   |
| Entry rate locked               | 5.5% APY   |
| Bond duration                   | 90 days    |
| Pro-rated yield (5.5% × 90/365) | ~1.36%     |
| **Net profit**                  | **~\$136** |

The user locks in 5.5% APY at the moment of bond creation. The TWAMM streaming sell gradually converts wRLP → USDC over the bond duration. At maturity, the remaining debt is perfectly cancelled — the wRLP bought back from proceeds matches the outstanding wRLP debt. The yield is simply the **pro-rated entry rate** applied to the collateral.

**Key property**: The TWAMM order [counts as collateral](../architecture/prime-broker#the-bond-problem), so bonds are naturally over-collateralized with no liquidation risk under normal conditions.

## 2. Rate Hedging — Lock in Borrowing Cost

**Scenario**: You're borrowing 1M USDC on Aave at a variable rate. You want to cap your rate at 5%.

**How it works**:

1. Go long on RLD — buy wRLP tokens
2. If rates spike to 10%, wRLP price doubles from \$5 → \$10
3. Your wRLP gain offsets your increased borrowing cost
4. Net effect: your effective borrowing cost stays near 5%

**The numbers**:

| Rates Go To | Aave Cost Change | wRLP Gain  | Net Impact          |
| ----------- | ---------------- | ---------- | ------------------- |
| 5% → 8%     | +\$30,000/yr     | +\$30,000  | Hedged ✅           |
| 5% → 3%     | -\$20,000/yr     | -\$20,000  | Still saved overall |
| 5% → 15%    | +\$100,000/yr    | +\$100,000 | Perfectly hedged ✅ |

## 3. Rate Speculation — Directional Bets

**Scenario**: You believe Aave borrow rates will increase significantly over the next month.

### Going Long (Betting Rates Go Up)

1. Deposit \$1,000 USDC
2. Buy wRLP on the V4 pool at \$5.00 (200 wRLP)
3. Rates increase from 5% → 8%
4. wRLP price rises to ~\$8.00
5. Sell wRLP for \$1,600
6. **Profit: \$600 (60%)**

### Going Short with Leverage (Betting Rates Go Down)

1. Deposit \$10,000 aUSDC
2. Mint 800 wRLP (debt at \$5.00 = \$4,000)
3. Sell wRLP on V4 pool for \$4,000 USDC
4. Effective position: \$14,000 collateral, \$4,000 debt (~3.5:1)
5. Rates decrease from 5% → 3%
6. Index price drops to \$3.00, buy back 800 wRLP for \$2,400
7. **Profit: \$1,600**

## 4. Volatility Trading

Interest rates correlate strongly with crypto market volatility:

- **Bull markets**: high demand for leverage → rates spike
- **Bear markets**: deleveraging → rates collapse
- **Black swan events**: extreme rate volatility

RLD lets you take positions on this volatility directly — a more capital-efficient way to express volatility views than options.

## 5. Credit-Default Swaps (CDS)

**Scenario**: You want insurance against Aave becoming insolvent.

**How it works**:

- During a protocol default, rates typically spike to 100% (borrowers rush to withdraw)
- If you're long wRLP with a \$5 entry price, and rates spike to 100% (\$100 price), your position gains **20×**
- This acts as parametric insurance — the payout is triggered automatically by the rate spike, not by a governance vote

**Key design**: CDS markets use an **isolated clearinghouse** with uncorrelated collateral (ETH/stETH instead of aUSDC) to avoid circular dependency.

## Use Case Comparison

| Use Case           | Direction     | Duration      | Key Feature                      |
| ------------------ | ------------- | ------------- | -------------------------------- |
| Synthetic Bonds    | Short + TWAMM | 30-90 days    | Fixed yield, no liquidation risk |
| Rate Hedging       | Long          | Ongoing       | Offsets variable rate exposure   |
| Speculation        | Long or Short | Days to weeks | Leveraged rate bets              |
| Volatility Trading | Long or Short | Event-driven  | Rates as vol proxy               |
| CDS                | Long          | Ongoing       | Parametric insurance             |
