# RLD Protocol - Local Fork Deployment Guide

## Overview

This document describes the complete process for deploying the RLD Protocol on a local Anvil fork of Ethereum mainnet. The deployment creates a functional market with live Aave interest rate data.

---

## Prerequisites

### Required Software

- **Foundry** (forge, anvil, cast)
- **Node.js** (v18+) and npm
- **Python 3.12+** with pip

### Environment Setup

1. **Contracts Environment** (`/contracts/.env`):

```bash
PRIVATE_KEY=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80
MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/YOUR_API_KEY
```

2. **Backend Environment** (`/backend/.env`):

```bash
API_KEY=your_api_key
```

---

## Deployment Steps

### Step 1: Start Anvil Fork

Kill any existing Anvil process and start a fresh fork on a specific block:

```bash
pkill -9 -f anvil
source /home/ubuntu/RLD/contracts/.env
anvil --fork-url $MAINNET_RPC_URL --fork-block-number 24335184 --host 0.0.0.0 > /home/ubuntu/RLD/anvil.log 2>&1 &
```

**Block Selection Notes:**

- Block 24335184 has ~4.9% Aave USDC variable borrow rate
- Different blocks will have different rates, affecting the Index Price
- Use `cast block latest` to get the current block number

**Verify Anvil is running:**

```bash
curl -s -X POST http://localhost:8545 -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
```

---

### Step 2: Deploy RLD Protocol

Deploy all core contracts:

```bash
cd /home/ubuntu/RLD/contracts
source .env
forge script script/DeployRLDFull.s.sol:DeployRLDFull \
  --rpc-url http://localhost:8545 \
  --broadcast
```

**Deployed Contracts:**
| Contract | Description |
|----------|-------------|
| `RLDCore` | Core market logic and state |
| `RLDMarketFactory` | Market deployment factory |
| `PrimeBroker` | Position management |
| `RLDAaveOracle` | Index price oracle (Aave rates) |
| `UniswapV4SingletonOracle` | Mark price oracle (TWAP) |
| `DutchLiquidationModule` | Liquidation auctions |
| `StandardFundingModel` | Funding rate calculations |
| `TWAMM` | Time-weighted AMM hook |

---

### Step 3: Update CreateTestMarket Script

The `CreateTestMarket.s.sol` script needs correct contract addresses. Update these constants with addresses from `broadcast/DeployRLDFull.s.sol/1/run-latest.json`:

```solidity
address constant CORE = <NEW_CORE_ADDRESS>;
address constant FACTORY = <NEW_FACTORY_ADDRESS>;
address constant LIQUIDATION_MODULE = <NEW_LIQUIDATION_MODULE>;
address constant V4_ORACLE = <NEW_V4_ORACLE>;
address constant AAVE_ORACLE = <NEW_AAVE_ORACLE>;
```

**Important: Use checksummed addresses** (proper case) to avoid compiler errors.

---

### Step 4: Create Test Market

```bash
cd /home/ubuntu/RLD/contracts
source .env
forge script script/CreateTestMarket.s.sol:CreateTestMarket \
  --rpc-url http://localhost:8545 \
  --broadcast
```

**Current Market Parameters:**

```solidity
DeployParams({
    underlyingPool: 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2,  // Aave V3 Pool
    underlyingToken: USDC,      // 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48
    collateralToken: aUSDC,     // 0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c
    curator: deployer,
    positionTokenName: "Wrapped RLD LP aUSDC",
    positionTokenSymbol: "wRLPaUSDC",
    minColRatio: 1.5e18,        // 150%
    maintenanceMargin: 1.1e18,  // 110%
    liquidationCloseFactor: 0.5e18,  // 50%
    spotOracle: address(0),     // Disabled for testing
    rateOracle: AAVE_ORACLE,    // Index price source
    poolFee: 500,               // 0.05%
    tickSpacing: 5
})
```

---

### Step 5: Extract Market ID

Get the Market ID from factory events:

```bash
cd /home/ubuntu/RLD/backend
python3 << 'EOF'
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('http://localhost:8545'))
factory_addr = Web3.to_checksum_address("0x11d51B9bec07CdCB55E845E14BB9784C11D8A6AC")
event_sig = w3.keccak(text="MarketDeployed(bytes32,address,address,address,address,address)")
logs = w3.eth.get_logs({'address': factory_addr, 'fromBlock': 0, 'toBlock': 'latest', 'topics': [event_sig]})
for log in logs:
    print(f"Market ID: 0x{log['topics'][1].hex()}")
EOF
```

---

### Step 6: Clean Databases

Clear old market data from SQLite databases:

```bash
cd /home/ubuntu/RLD/backend
python3 << 'EOF'
import sqlite3
for db in ["market_state.db", "simulations.db"]:
    conn = sqlite3.connect(db)
    for table in ["markets", "market_risk_params", "market_state_snapshots", "state_indexer_state"]:
        try:
            conn.execute(f"DELETE FROM {table}")
        except: pass
    conn.commit()
    conn.close()
print("✅ Databases cleaned")
EOF
```

---

### Step 7: Restart Backend

```bash
pkill -f "uvicorn api:app"
cd /home/ubuntu/RLD/backend
uvicorn api:app --host 0.0.0.0 --port 8000 --reload > /tmp/backend.log 2>&1 &
```

Wait for startup (~5-6 seconds), then verify:

```bash
curl -s http://localhost:8000/simulations/enriched | python3 -m json.tool
```

---

### Step 8: Register Market

Register the market with the indexer:

```bash
curl -X POST "http://localhost:8000/market/register?market_id=<MARKET_ID>"
```

**Verify registration:**

```bash
curl -s "http://localhost:8000/simulation/<MARKET_ID>/enriched" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f\"Market: {d.get('positionTokenSymbol')}\")
print(f\"Index Price: {d.get('prices', {}).get('index_price_display')}\")
print(f\"Min Col Ratio: {d.get('risk_params', {}).get('display_minColRatio')}%\")
"
```

---

### Step 9: Start Frontend

```bash
cd /home/ubuntu/RLD/frontend
npm run dev
```

Access at: http://localhost:5173/simulation

---

## Troubleshooting

### Index Price Shows $0.0001

The oracle is hitting the minimum floor. Check:

1. `underlyingToken` should be **USDC** (not aUSDC)
2. The oracle queries `getReserveData(underlyingToken)` from Aave
3. aUSDC is not a reserve - USDC is

### Market Creation Fails with "Invalid SpotOracle"

Both `RLDMarketFactory.sol` and `RLDCore.sol` validate spotOracle != address(0).
To allow spotOracle=address(0), comment out these checks:

- `RLDMarketFactory.sol` line 373
- `RLDCore.sol` line 127

### Backend Returns 400 on Market Registration

The backend may be using old contract addresses. Restart it after redeploying.

---

## Quick Reference

### Key Addresses (Block 24335184 Fork)

| Contract               | Address                                      |
| ---------------------- | -------------------------------------------- |
| RLDCore                | `0x62e5c8AA289a610bd16d38fF49e46B038623B29f` |
| RLDMarketFactory       | `0x11d51B9bec07CdCB55E845E14BB9784C11D8A6AC` |
| RLDAaveOracle          | `0x475102156b26305510F56234C6c9D21130FCFC4a` |
| Aave V3 Pool (Mainnet) | `0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2` |
| USDC (Mainnet)         | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` |
| aUSDC (Mainnet)        | `0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c` |

### Index Price Formula

Per RLD paper (Section 2.1):

```
Index Price = K × Aave Variable Borrow Rate
```

Where K = 100, so:

- 5% rate → $5.00 index price
- 4.9% rate → $4.90 index price
- Minimum floor: $0.0001

---

## Automation Script

Use `scripts/deploy_local.sh` to automate the entire process.
