# Reth Dev Node

Persistent, disk-backed replacement for Anvil. Eliminates memory leaks from long-running Anvil forks.

## Quick Start

```bash
# Full rebuild (Anvil deploy → state dump → Reth genesis → boot)
./docker/reth/restart-reth.sh --fresh

# Fast restart (reuse existing genesis.json)
./docker/reth/restart-reth.sh --skip-genesis

# With user setup (creates brokers + deposits on Reth so indexer captures events)
./docker/reth/restart-reth.sh --skip-genesis --with-users
```

## Architecture

```
Anvil fork (temporary)          Reth (persistent)
┌─────────────────────┐        ┌──────────────────────┐
│ 1. Fork mainnet     │        │ 4. Boot with genesis  │
│ 2. Deploy protocol  │──dump──│ 5. Start indexer      │
│ 3. Dump state       │        │ 6. Start daemons      │
└─────────────────────┘        └──────────────────────┘
```

## Configuration

| Setting | Default | Env Override |
|---------|---------|-------------|
| Block time | **1s** | `RETH_BLOCK_TIME=5` |
| RPC port | 8545 | `RETH_PORT=8546` |
| Data dir | `~/.local/share/reth-dev` | `RETH_DATADIR=/path` |
| Genesis timestamp | Current real-world time | — |

Production security defaults:

- `INDEXER_ADMIN_TOKEN` must be set for simulation indexer reset protection.
- `DB_PASSWORD` must be set explicitly (no weak fallback).
- `RETH_HTTP_CORS_DOMAIN` should be explicit browser origins only.

## USDC Faucet

Genesis includes **$10B USDC** on a single faucet address we control.

| | Value |
|---|---|
| Address | `0xa0Ee7A142d267C1f36714E4a8F75612F20a79720` |
| Key | `WHALE_KEY` in `docker/.env` |
| Balance | $10,000,000,000 USDC |
| Source | Anvil account #9 — balance injected via `convert_state.py` |

**Fund any account:**
```bash
source docker/.env
USDC=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48

# Send $1M USDC to recipient
cast send "$USDC" "transfer(address,uint256)" \
    "$RECIPIENT" 1000000000000 \
    --private-key "$WHALE_KEY" --rpc-url http://localhost:8545
```

## Accounts (from `docker/.env`)

All accounts get 10,000 ETH in genesis. Keys are Anvil/Hardhat defaults.

| Name | Address | Key Var |
|------|---------|---------|
| Deployer / User A | `0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266` | `DEPLOYER_KEY` |
| User B | `0x70997970C51812dc3A010C7d01b50e0d17dc79C8` | `USER_B_KEY` |
| User C | `0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC` | `USER_C_KEY` |
| Market Maker | `0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65` | `MM_KEY` |
| Chaos Trader | `0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc` | `CHAOS_KEY` |
| USDC Faucet | `0xa0Ee7A142d267C1f36714E4a8F75612F20a79720` | `WHALE_KEY` |

## Scripts

| Script | Purpose |
|--------|---------|
| `restart-reth.sh` | Main entry point — full lifecycle |
| `docker/deployer/deploy_protocol_snapshot.py` | One-shot deployer used during Anvil snapshot bootstrap |
| `convert_state.py` | Anvil JSON → Reth genesis format |
| `verify_protocol_e2e.py` | Read-only verification of on-chain/indexer deployment wiring |
| `setup_simulation.py` | Post-genesis SimFunder + LP/MM/CHAOS setup on Reth |
| `deploy_pool_live_index_with_liquidity.py` | Standalone V4 pool deploy/liquidity smoke script |

## GhostRouter Solver Requirement

For GhostRouter markets, execution quality is not "set and forget". A solver must evaluate:

1. Direct vanilla V4 pool swap.
2. `GhostRouter.swap` (netting/intercept first, then pool fallback).

Recent benchmark snapshot (`contracts/test/dex/GasBench.RouterExecutionProfiles.t.sol`):

- No engines: direct `142,698` gas vs router `176,566` gas.
- Idle TWAP engine: direct `142,698` gas vs router `244,146` gas.
- Active passive TWAP inventory: direct `140,195` gas / out `95` vs router `228,673` gas / out `100`.

Decision rule:

- Route through router only when output improvement is worth the gas premium.
- `value(routerOut - directOut) > (routerGas - directGas) * gasPrice`

Operational policy:

- We must run our own first-party arb/route solver for production.
- External arbitrage participation is beneficial but cannot be a launch dependency.

### Reproduce the gas profile

```bash
cd contracts
forge test --match-path "test/dex/GasBench.RouterExecutionProfiles.t.sol" -vv
```

## Flags

```
./docker/reth/restart-reth.sh [OPTIONS]

  --fresh          Wipe datadir + regenerate genesis from Anvil
  --skip-genesis   Reuse existing genesis.json (fast restart)
  --no-build       Skip Docker image rebuilds
  --with-users     Create brokers + deposit collateral on Reth
```

## Indexer Integration

The indexer misses Anvil deployment events (they're not in Reth's block history). Two mechanisms compensate:

1. **Step 4e** — Seeds `block_states` at block 0 with on-chain pool data (token balances, sqrtPriceX96, tick, liquidity) via `extsload`
2. **`--with-users`** — Re-executes broker creation and deposits on Reth so the indexer captures `BrokerCreated` + `ERC20Transfer` events natively

## Troubleshooting

**Reth not starting:** Check `/tmp/reth.log` for errors. Common: port 8545 already in use.

**Zero TVL:** Run with `--with-users` or manually run `./docker/reth/05_setup_users_reth.sh`.

**Stale genesis:** Run with `--fresh` to rebuild from scratch.
