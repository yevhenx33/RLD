# forge-persist

Persistent mainnet forks in one command. Free, self-hosted replacement for Anvil (leaks memory) and Tenderly Virtual Testnets ($450/mo).

```bash
# Fork mainnet — that's it
forge-persist --fork-url https://eth-mainnet.g.alchemy.com/v2/YOUR_KEY

# Fork + deploy + persist
forge-persist --fork-url $ETH_RPC_URL \
  --deploy "forge script script/Deploy.s.sol --broadcast --rpc-url http://localhost:8545"

# Resume later
forge-persist --resume
```

## What it does

```
  ┌────────────────────────────────────────┐
  │ 1. Starts temp Anvil fork     (30s)    │
  │ 2. Runs your deploy script    (varies) │
  │ 3. Dumps state → Reth genesis (5s)     │
  │ 4. Kills Anvil → boots Reth   (2s)     │
  │ 5. Serves on localhost:8545   (∞)      │
  └────────────────────────────────────────┘
```

You get a persistent, disk-backed node that:
- **Survives restarts** — state stored on disk (MDBX)
- **Never leaks memory** — flat RSS forever
- **1s block times** — fast iteration
- **Drop-in replacement** — same port, same chain ID, same RPC

## Install

```bash
# Via npx (no install needed)
npx forge-persist --fork-url $RPC

# Or clone
git clone https://github.com/your-org/forge-persist
cd forge-persist
chmod +x bin/forge-persist
./bin/forge-persist --fork-url $RPC
```

**Requirements:** [Foundry](https://book.getfoundry.sh/getting-started/installation) (`anvil`, `cast`), Python 3, `jq`. Reth is auto-installed if missing.

## Usage

```
forge-persist [OPTIONS]

FORK MODE:
  --fork-url <URL>         RPC to fork from (required first time)
  --fork-block <NUM>       Fork at specific block (default: latest)
  --deploy <CMD>           Run command on temp Anvil before migrating
  --chain-id <NUM>         Chain ID (default: 31337)

RESUME MODE:
  --resume                 Boot from saved state
  --fresh                  Wipe everything and re-fork

CONFIG:
  --port <NUM>             RPC port (default: 8545)
  --block-time <SEC>       Block interval (default: 1)
  --data-dir <PATH>        Storage path (default: .forge-persist/)
  --background             Run in background
  --fund-key <KEY>         Pre-fund with 10000 ETH (repeatable)

MANAGEMENT:
  --status                 Show node status
  --stop                   Stop background node
```

## How it works

1. **Anvil fork** — Temporary Anvil process forks mainnet at the specified block
2. **Deploy** — Your forge scripts run against Anvil (full EVM, impersonation, etc.)
3. **State dump** — `anvil_dumpState` captures all accounts, code, and storage
4. **Convert** — Python converts the dump to a Reth-compatible genesis.json
5. **Reth boot** — Kills Anvil, boots Reth with the genesis. Disk-backed, persistent.

## Comparison

|  | Anvil | Tenderly | forge-persist |
|--|-------|----------|---------------|
| Cost | Free | $450/mo | **Free** |
| Persistent | ✗ | ✓ | **✓** |
| Memory stable | ✗ | ✓ | **✓** |
| Latency | <1ms | 50-200ms | **<1ms** |
| Self-hosted | ✓ | ✗ | **✓** |
| One command | ✓ | ✗ | **✓** |

## License

MIT
