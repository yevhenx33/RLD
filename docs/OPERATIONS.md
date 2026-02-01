# RLD Protocol - Operations Guide

Step-by-step documentation for local development workflows.

---

## Table of Contents

1. [Local Deployment](#1-local-deployment)
2. [Open Position (aUSDC Market)](#2-open-position-ausdc-market)
3. [Mint & LP with waUSDC](#3-mint--lp-with-wausdc)

---

## 1. Local Deployment

**Script**: [`./scripts/deploy_local.sh`](file:///home/ubuntu/RLD/scripts/deploy_local.sh)

Deploys the entire RLD protocol to a local Anvil mainnet fork.

### Prerequisites

- `PRIVATE_KEY` set in `contracts/.env`
- Foundry installed

### Command

```bash
cd /home/ubuntu/RLD
./scripts/deploy_local.sh
```

### What it does

| Step | Description                                                                                     |
| ---- | ----------------------------------------------------------------------------------------------- |
| 1    | Starts Anvil mainnet fork (block 21671578)                                                      |
| 2    | Compiles contracts                                                                              |
| 3    | Runs `Deploy.s.sol` to deploy: RLDCore, RLDMarketFactory, DutchLiquidationModule, RLDAaveOracle |
| 4    | Runs `CreateTestMarket.s.sol` to create aUSDC/wRLP market                                       |
| 5    | Saves addresses to `contracts/deployments.json`                                                 |

### Output

```
✓ Anvil started (PID: XXXX)
✓ RLD Core deployed
✓ Market created
```

### Key Addresses (after deployment)

| Contract              | Address                                      |
| --------------------- | -------------------------------------------- |
| RLDCore               | `0xe267f2cCA951A26e16F12F42E03AD30eeD30F10a` |
| PrimeBrokerFactory    | `0x0DCbfF67e7ae2d22D62DdED30f8f32A0Be5689C8` |
| Position Token (wRLP) | `0xfC7045175e14D22B064657E7a5A0382f9081e90b` |

---

## 2. Open Position (aUSDC Market)

**Script**: [`./scripts/open_position.sh`](file:///home/ubuntu/RLD/scripts/open_position.sh)

Opens a position in the original aUSDC market: deposits collateral and mints wRLP debt.

### Prerequisites

- Local fork running (step 1 complete)

### Command

```bash
cd /home/ubuntu/RLD
./scripts/open_position.sh
```

### What it does

| Step | Action              | Details                                                 |
| ---- | ------------------- | ------------------------------------------------------- |
| 1    | Acquire aUSDC       | Impersonate USDC whale → supply to Aave → receive aUSDC |
| 2    | Prime TWAMM         | Advance time 2 hours for oracle                         |
| 3    | Create Broker       | Call `PrimeBrokerFactory.createBroker()`                |
| 4    | Transfer Collateral | Send aUSDC to broker                                    |
| 5    | Mint Debt           | Call `broker.modifyPosition(0, wRLPAmount)`             |

### Parameters (configurable in script)

```bash
COLLATERAL_AMOUNT=10000000   # 10M aUSDC
DEBT_AMOUNT=200000           # 200k wRLP
```

### Output

```
✓ Position opened with 200k wRLP at 50x collateralization
  Broker: 0x...
  wRLP Balance: 200000
```

---

## 3. Mint & LP with waUSDC

**Script**: [`./scripts/mint_and_lp_wrapped.sh`](file:///home/ubuntu/RLD/scripts/mint_and_lp_wrapped.sh)

Full flow: deploys waUSDC wrapper, opens position, and provides V4 concentrated liquidity.

### Prerequisites

- Local fork running
- waUSDC wrapper + market deployed

### Commands (Full Fresh Start)

```bash
# Step A: Start local fork
./scripts/deploy_local.sh

# Step B: Deploy waUSDC wrapper + market
cd contracts && source .env
forge script script/DeployWrappedMarket.s.sol --rpc-url http://localhost:8545 --broadcast -v

# Step C: Mint & LP
cd ..
./scripts/mint_and_lp_wrapped.sh
```

### What `mint_and_lp_wrapped.sh` does

| Step  | Action             | Details                             |
| ----- | ------------------ | ----------------------------------- |
| 1/10  | Acquire aUSDC      | Whale → Aave supply → 10M aUSDC     |
| 2/10  | Wrap aUSDC         | aUSDC → waUSDC (non-rebasing)       |
| 3/10  | Prime TWAMM        | Advance time 2 hours                |
| 4/10  | Create Broker      | New broker for waUSDC market        |
| 5/10  | Deposit Collateral | Transfer waUSDC to broker           |
| 6/10  | Mint wRLP          | 500k wRLP debt                      |
| 7/10  | Withdraw for LP    | 100k waUSDC + 100k wRLP             |
| 8/10  | Approve V4         | Permit2 + PositionManager approvals |
| 9/10  | Query Pool         | Verify pool state                   |
| 10/10 | Add Liquidity      | V4 concentrated LP (price 2-20)     |

### Parameters (configurable)

```bash
COLLATERAL_AMOUNT=10000000   # 10M waUSDC
DEBT_AMOUNT=500000           # 500k wRLP
LP_AMOUNT=100000             # 100k each for LP
```

### Key Addresses (waUSDC Market)

| Contract       | Address                                      |
| -------------- | -------------------------------------------- |
| waUSDC Wrapper | `0xcb68357b50A5e759E9C530f172A8174EfA1E350D` |
| wRLP Token     | `0x9ed4F4724b521326a9d9d2420252440bD05556c4` |
| Market ID      | `0x9adc509a...`                              |
| Broker Factory | `0x9554b52516f306360a239746F70f88c23D187b63` |

### Output

```
✓ V4 LP Position Created!
  Token ID: 148253
  Tick Range: [6930, 29960]
  Price Range: waUSDC/wRLP = [2, 20]
```

---

## Quick Reference

### All Scripts

| Script                   | Purpose                       |
| ------------------------ | ----------------------------- |
| `deploy_local.sh`        | Start Anvil + deploy protocol |
| `open_position.sh`       | Open position in aUSDC market |
| `mint_and_lp_wrapped.sh` | Full waUSDC mint + V4 LP flow |

### Forge Scripts

| Script                      | Purpose                       |
| --------------------------- | ----------------------------- |
| `DeployWrappedMarket.s.sol` | Deploy waUSDC + create market |
| `AddLiquidityWrapped.s.sol` | Add V4 LP with waUSDC         |
| `CheckPoolState.s.sol`      | Query V4 pool state           |

### Environment Variables

```bash
# Required in contracts/.env
PRIVATE_KEY=0x...  # Anvil deployer key
```
