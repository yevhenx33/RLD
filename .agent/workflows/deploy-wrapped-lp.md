---
description: Deploy waUSDC market and provide V4 LP from scratch
---

# Deploy Wrapped Market & LP

This workflow deploys the waUSDC wrapper, creates a new market with waUSDC collateral, mints wRLP, and provides V4 concentrated liquidity.

## Prerequisites

- Local Anvil fork running (via `deploy_local.sh`)
- `PRIVATE_KEY` set in `contracts/.env`

## Steps

// turbo-all

### 1. Start Local Fork (if not running)

```bash
cd /home/ubuntu/RLD
./scripts/deploy_local.sh
```

Wait for "Deployment Complete" message.

### 2. Deploy waUSDC Wrapper & Create Market

```bash
cd /home/ubuntu/RLD/contracts
source .env
forge script script/DeployWrappedMarket.s.sol --rpc-url http://localhost:8545 --broadcast -v
```

This deploys:

- waUSDC wrapper at `0xcb68357b50A5e759E9C530f172A8174EfA1E350D`
- New market with waUSDC collateral

### 3. Mint wRLP & Provide V4 LP

```bash
cd /home/ubuntu/RLD
./scripts/mint_and_lp_wrapped.sh
```

This script:

1. Acquires 10M aUSDC from whale
2. Wraps to waUSDC
3. Creates broker, deposits waUSDC
4. Mints 500k wRLP debt
5. Withdraws 100k each for LP
6. Approves V4 contracts
7. Provides concentrated liquidity (price range: 2-20)

## Output

On success, you'll see:

```
✓ V4 LP Position Created!
  Token ID: 148253
  Tick Range: [6930, 29960]
  Price Range: waUSDC/wRLP = [2, 20]
```

## Addresses (after deployment)

| Contract       | Address                                      |
| -------------- | -------------------------------------------- |
| waUSDC         | `0xcb68357b50A5e759E9C530f172A8174EfA1E350D` |
| wRLP           | `0x9ed4F4724b521326a9d9d2420252440bD05556c4` |
| Broker Factory | `0x9554b52516f306360a239746F70f88c23D187b63` |
| Market ID      | `0x9adc509a91014b...`                        |
