# Going Short

Going short means **minting and selling wRLP** against collateral — you profit when interest rates decrease.

## When to Go Short

- You expect lending rates to **decrease**
- You want to **lock in current high rates** as yield (via bonds)
- You want to **earn funding** when mark > index

## Step by Step

### 1. Create a PrimeBroker

If you haven't already, create a broker account. See [Getting Started](./getting-started).

### 2. Deposit Collateral

1. Navigate to your broker dashboard
2. Click **Deposit** and select your collateral token (e.g., aUSDC)
3. Approve the token transfer
4. Your collateral appears in the broker's NAV

### 3. Mint wRLP Debt

1. Click **Mint** or use the combined **Short** action
2. Enter the amount of wRLP to mint
3. The system checks: will your position remain above the **minimum collateral ratio** (typically 120%)?
4. Approve — wRLP is minted into your broker

### 4. Sell wRLP

**Option A — Spot sell:**

1. Sell wRLP immediately on the V4 pool
2. USDC proceeds go back to your broker as additional collateral

**Option B — JTM streaming sell (recommended for bonds):**

1. Submit a streaming sell order
2. Gradual execution with better average pricing

### 5. Monitor Your Position

| Metric           | Watch For                                           |
| ---------------- | --------------------------------------------------- |
| **Health Ratio** | Must stay above 1.0 — add collateral if approaching |
| **NF**           | If mark < index, NF increases → your debt grows     |
| **Index Price**  | Rate increases → index rises → debt value grows     |

### 6. Close — Repay Debt

1. Buy back wRLP on the V4 pool (or let your JTM order fill)
2. Burn the wRLP to repay debt
3. Withdraw your collateral

## Leveraged Short

For experienced users, `LeverageShortExecutor` performs an atomic leveraged short:

1. Deposit collateral → mint wRLP → sell on V4 → redeposit USDC proceeds
2. All in one transaction via ephemeral operator signature
3. Effective leverage depends on collateral ratio

### Example — 3× Leveraged Short

| Step | Action    | Amount                                           |
| ---- | --------- | ------------------------------------------------ |
| 1    | Deposit   | 10,000 aUSDC                                     |
| 2    | Mint wRLP | ~6,000 wRLP (\$30,000 notional at \$5)           |
| 3    | Sell wRLP | +30,000 USDC → redeposit                         |
| 4    | Position  | \$40,000 collateral, \$30,000 debt (~1.33 ratio) |

If rates drop 20% (index \$5 → \$4):

- Debt value: \$30,000 → \$24,000
- **Profit: \$6,000 (60% on \$10,000 initial)** — 3× leveraged return

If rates rise 10% (index \$5 → \$5.50):

- Debt value: \$30,000 → \$33,000
- Health drops — add collateral or risk liquidation

## Managing Risk

### Adding Collateral

If your health ratio drops:

1. Deposit more collateral into your broker
2. Health ratio immediately improves
3. NAV increases → position stabilizes

### Partial Close

Reduce exposure without fully closing:

1. Buy back a portion of wRLP
2. Burn it against your debt
3. Reduces both debt and required collateral

### Health Alerts

Monitor your health ratio. Key thresholds:

| Health    | Action                                          |
| --------- | ----------------------------------------------- |
| > 2.0     | Comfortable — no action needed                  |
| 1.5 - 2.0 | Monitor closely — consider adding collateral    |
| 1.0 - 1.5 | Warning — add collateral immediately            |
| < 1.0     | Liquidatable — anyone can liquidate for a bonus |
