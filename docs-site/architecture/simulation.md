# RLD Simulation Environment - Developer Onboarding Guide

This document provides a comprehensive guide for setting up and running the RLD simulation environment, including the orchestrator system and comprehensive indexer.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Quick Start](#quick-start)
3. [Architecture Overview](#architecture-overview)
4. [Orchestrator System](#orchestrator-system)
5. [Comprehensive Indexer](#comprehensive-indexer)
6. [API Reference](#api-reference)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Software

```bash
# Node.js (for Foundry/Anvil)
node --version  # v18+

# Foundry (Forge, Cast, Anvil)
foundryup
forge --version
cast --version
anvil --version

# Python 3.12+
python3 --version

# Python dependencies
pip install web3 fastapi uvicorn python-dotenv requests
```

### Environment Setup

The simulation uses environment variables stored in `/home/ubuntu/RLD/.env`. Key variables:

| Variable     | Description                                        |
| ------------ | -------------------------------------------------- |
| `RPC_URL`    | Local Anvil RPC (default: `http://localhost:8545`) |
| `RLD_CORE`   | RLDCore contract address                           |
| `WAUSDC`     | Wrapped aUSDC token address                        |
| `WRLP`       | Wrapped RLP token address                          |
| `MARKET_ID`  | Active market identifier                           |
| `USER_A_KEY` | LP Provider private key                            |
| `USER_B_KEY` | Long trader private key                            |
| `USER_C_KEY` | JTM user private key                             |
| `MM_KEY`     | Market maker bot private key                       |
| `CHAOS_KEY`  | Chaos trader private key                           |

---

## Quick Start

### One-Command Launch

```bash
cd /home/ubuntu/RLD
./scripts/orchestrator.sh
```

This single command:

1. Kills any existing processes
2. Starts Anvil fork at specific block
3. Deploys all contracts
4. Starts the indexer
5. Sets up all test users
6. Starts trading daemons

### After Launch

```bash
# Check system status
cat /tmp/anvil.pid     # Anvil process ID
cat /tmp/indexer.pid   # Indexer process ID
cat /tmp/daemon.pid    # Daemon process ID

# View logs
tail -f /tmp/anvil.log
tail -f /tmp/indexer.log
tail -f /tmp/daemon.log
tail -f /tmp/chaos_trader.log

# Query API
curl http://localhost:8080/api/status
```

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RLD Simulation System                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │    Anvil     │◄───│  Contracts   │◄───│   Trading Daemons    │  │
│  │ (Local Fork) │    │  (RLDCore,   │    │  (MM Bot, Chaos)     │  │
│  │  Port: 8545  │    │   V4 Pool)   │    │                      │  │
│  └──────┬───────┘    └──────────────┘    └──────────────────────┘  │
│         │                                                           │
│         ▼                                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Comprehensive Indexer                        │  │
│  │  • Polls blocks continuously                                  │  │
│  │  • Captures Swap, Transfer, PositionModified events          │  │
│  │  • Stores to SQLite (comprehensive_state.db)                 │  │
│  └──────────────────────────────────────────────────────────────┘  │
│         │                                                           │
│         ▼                                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                     Indexer API                               │  │
│  │  • REST endpoints on port 8080                                │  │
│  │  • /api/status, /api/latest, /api/events, /api/chart/price   │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Orchestrator System

The orchestrator (`scripts/orchestrator.sh`) runs 5 phases sequentially:

### Phase 1: Infrastructure

```bash
scripts/infra/kill_all.sh      # Kill existing anvil, indexer, daemons
scripts/infra/start_anvil.sh   # Start Anvil fork at block 21698573
```

**What happens:**

- All existing processes are terminated
- Anvil starts forking mainnet at a specific block
- PID saved to `/tmp/anvil.pid`
- Logs written to `/tmp/anvil.log`

### Phase 2: Deploy Protocol & Market

```bash
scripts/infra/deploy_protocol.sh   # Deploy RLDCore, Factory, JTM Hook
scripts/infra/deploy_market.sh     # Deploy waUSDC, wRLP, Broker Factory
```

**Contracts Deployed:**
| Contract | Description |
|----------|-------------|
| `RLDCore` | Core lending/borrowing engine |
| `RLDMarketFactory` | Factory for creating markets |
| `JTMHook` | Time-weighted AMM hook for V4 |
| `waUSDC` | Wrapped Aave USDC |
| `wRLP` | Wrapped RLD Position token |
| `BrokerFactory` | Factory for user brokers |

**Environment Updated:**

- All contract addresses written to `.env`
- `MARKET_ID` generated from pool parameters

### Phase 3: Start Indexer

```bash
scripts/infra/start_indexer.sh
```

**What happens:**

- Starts `run_comprehensive_indexer.py` with `--run` flag
- Polls new blocks every 12 seconds
- Captures all contract events
- Stores to `backend/comprehensive_state.db`

### Phase 4: Setup Users

Five user types are initialized:

| User                     | Script            | Collateral | Action                     |
| ------------------------ | ----------------- | ---------- | -------------------------- |
| **LP Provider** (User A) | `lp_provider.sh`  | $100M      | Provides V4 liquidity      |
| **Long Trader** (User B) | `long_user.sh`    | $100K      | Opens long position        |
| **JTM User** (User C)  | `twamm_user.sh`   | $100K      | Creates 1-hour JTM order |
| **MM Bot**               | `mm_bot.sh`       | $10M       | Market making bot          |
| **Chaos Trader**         | `chaos_trader.sh` | $10M       | Random trading bot         |

**Each user setup:**

1. Funds wallet with USDC
2. Deposits to Aave → gets aUSDC
3. Wraps aUSDC → waUSDC
4. Creates broker (for LPs/MMs)
5. Executes initial position

### Phase 5: Start Daemons

```bash
scripts/infra/start_daemons.sh   # Starts funding/oracle daemon
scripts/infra/start_chaos.sh     # Starts chaos trader daemon
```

**Daemons running:**
| Daemon | PID File | Log File | Function |
|--------|----------|----------|----------|
| Combined Daemon | `/tmp/daemon.pid` | `/tmp/daemon.log` | Updates funding rates, oracles |
| Chaos Trader | - | `/tmp/chaos_trader.log` | Random swaps every few blocks |

---

## Comprehensive Indexer

### Overview

The indexer continuously monitors the blockchain and captures:

- **Market State**: Normalization factor, total debt, index price
- **Pool State**: Tick, liquidity, mark price, sqrtPriceX96
- **Events**: Swap, Transfer, Approval, PositionModified, ModifyLiquidity
- **Transactions**: All interactions with tracked contracts

### Key Files

```
backend/
├── scripts/
│   ├── comprehensive_indexer.py     # Main indexer logic
│   └── run_comprehensive_indexer.py # CLI runner
├── comprehensive_indexer_db.py      # Database operations
├── comprehensive_state.db           # SQLite database
└── indexer_api.py                   # REST API
```

### Database Schema

**block_state** - Market state per block

```sql
block_number, market_id, normalization_factor, total_debt,
last_update_timestamp, index_price, block_timestamp
```

**pool_state** - V4 pool state per block

```sql
block_number, pool_id, token0, token1, sqrt_price_x96,
tick, liquidity, mark_price, fee_growth_global0, fee_growth_global1
```

**events** - All captured events

```sql
id, block_number, tx_hash, log_index, event_name,
contract_address, market_id, data (JSON), timestamp
```

**transactions** - Raw transaction data

```sql
block_number, tx_hash, tx_index, from_address, to_address,
value, gas_used, gas_price, input_data, method_id, method_name
```

### Decimal Standards

| Field                  | Decimals      | Division |
| ---------------------- | ------------- | -------- |
| `waUSDC` amounts       | 6             | `÷ 1e6`  |
| `wRLP` amounts         | 6             | `÷ 1e6`  |
| `normalization_factor` | 18            | `÷ 1e18` |
| `index_price`          | 18            | `÷ 1e18` |
| `mark_price`           | Already float | N/A      |
| `tick`                 | Integer       | N/A      |

### Event Parsing

Events are captured using topic signatures:

```python
EVENT_TOPICS = {
    "Transfer": Web3.keccak(text="Transfer(address,address,uint256)"),
    "Approval": Web3.keccak(text="Approval(address,address,uint256)"),
    "PositionModified": Web3.keccak(text="PositionModified(bytes32,address,int256,int256)"),
    "Swap": Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"),
    "ModifyLiquidity": Web3.keccak(text="ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)"),
}
```

### Running Manually

```bash
cd /home/ubuntu/RLD/backend

# Single snapshot
python3 scripts/run_comprehensive_indexer.py

# Continuous indexing
python3 scripts/run_comprehensive_indexer.py --run

# From specific block
python3 scripts/run_comprehensive_indexer.py --run --from-block 21700000

# Fresh start (clear DB first)
rm comprehensive_state.db
python3 scripts/run_comprehensive_indexer.py --run
```

---

## API Reference

Base URL: `http://localhost:8080`

### GET /api/status

Returns indexer status and statistics.

```bash
curl http://localhost:8080/api/status
```

Response:

```json
{
  "last_indexed_block": 21700500,
  "db_path": "/home/ubuntu/RLD/backend/comprehensive_state.db",
  "total_block_states": 1500,
  "total_events": 450
}
```

### GET /api/latest

Returns the latest indexed state.

```bash
curl http://localhost:8080/api/latest
```

Response includes `market_states`, `pool_states`, and `broker_positions`.

### GET /api/events

Query historical events with filters.

```bash
# All events
curl "http://localhost:8080/api/events?limit=10"

# Filter by event type
curl "http://localhost:8080/api/events?event_name=Swap&limit=5"

# Filter by block range
curl "http://localhost:8080/api/events?from_block=21700000&to_block=21700100"
```

Response:

```json
[
  {
    "id": 123,
    "block_number": 21700050,
    "tx_hash": "0x...",
    "log_index": 4,
    "event_name": "Swap",
    "contract_address": "0x...",
    "market_id": "0x...",
    "event_data": {
      "amount0": "196693493856",
      "amount1": "-44894176890",
      "tick": -14625
    },
    "block_timestamp": 1737794638
  }
]
```

### GET /api/history/market

Get historical market state data.

```bash
curl "http://localhost:8080/api/history/market?limit=100"
```

### GET /api/history/pool

Get historical pool state data.

```bash
curl "http://localhost:8080/api/history/pool?limit=100"
```

### GET /api/chart/price

Get price data formatted for charting.

```bash
curl "http://localhost:8080/api/chart/price?limit=500"
```

---

## Troubleshooting

### Common Issues

**1. Anvil not starting**

```bash
# Check if port is in use
lsof -i :8545

# Kill existing process
pkill -f anvil
```

**2. Indexer not capturing events**

```bash
# Check if indexer is running
ps aux | grep comprehensive_indexer

# Check logs
tail -50 /tmp/indexer.log

# Restart with fresh DB
rm backend/comprehensive_state.db
./scripts/infra/start_indexer.sh
```

**3. API returning empty arrays**

```bash
# Check if API is running
curl http://localhost:8080/api/status

# Restart API
pkill -f indexer_api.py
cd /home/ubuntu/RLD/backend
python3 indexer_api.py > /tmp/api.log 2>&1 &
```

**4. Missing PositionModified events**

If indexer started after user setup, those events won't be captured.

```bash
# Restart entire simulation
./scripts/orchestrator.sh
```

### Useful Commands

```bash
# Kill everything
./scripts/infra/kill_all.sh

# Check all processes
ps aux | grep -E "(anvil|indexer|daemon|chaos)"

# Query database directly
cd /home/ubuntu/RLD/backend
sqlite3 comprehensive_state.db "SELECT COUNT(*) FROM events"
sqlite3 comprehensive_state.db "SELECT event_name, COUNT(*) FROM events GROUP BY event_name"

# Monitor real-time logs
tail -f /tmp/indexer.log /tmp/daemon.log /tmp/chaos_trader.log
```

### Process Cleanup

```bash
# Full cleanup
pkill -f anvil
pkill -f comprehensive_indexer
pkill -f indexer_api
pkill -f daemon.py
pkill -f chaos_daemon.py
rm /tmp/*.pid
```

---

## Directory Structure

```
/home/ubuntu/RLD/
├── .env                          # Environment variables (auto-generated)
├── contracts/                    # Solidity contracts
│   ├── src/                      # Source code
│   ├── script/                   # Deployment scripts
│   └── deployments.json          # Deployed addresses
├── scripts/
│   ├── orchestrator.sh           # Main launcher
│   ├── infra/                    # Infrastructure scripts
│   │   ├── kill_all.sh
│   │   ├── start_anvil.sh
│   │   ├── deploy_protocol.sh
│   │   ├── deploy_market.sh
│   │   ├── start_indexer.sh
│   │   ├── start_daemons.sh
│   │   └── start_chaos.sh
│   ├── scenarios/                # User setup scripts
│   │   ├── lp_provider.sh
│   │   ├── long_user.sh
│   │   ├── twamm_user.sh
│   │   ├── mm_bot.sh
│   │   └── chaos_trader.sh
│   └── utils/                    # Helper scripts
└── backend/
    ├── scripts/
    │   ├── comprehensive_indexer.py
    │   └── run_comprehensive_indexer.py
    ├── comprehensive_indexer_db.py
    ├── comprehensive_state.db
    ├── indexer_api.py
    └── requirements.txt
```

---

## Next Steps

After the simulation is running:

1. **Monitor the system**: Watch logs and API responses
2. **Query events**: Use the API to analyze trading activity
3. **Modify parameters**: Edit user amounts in scenario scripts
4. **Add new users**: Create new scenario scripts following existing patterns
5. **Extend indexer**: Add new event types to capture

For questions or issues, check the logs first, then the troubleshooting section above.
