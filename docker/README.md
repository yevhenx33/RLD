# RLD Simulation Stack

One-command setup for the full RLD simulation environment.

## Prerequisites

- Docker & Docker Compose
- Anvil running externally (Foundry)

## Full System Launch

### 1. Persistent Setup (Run Once)

Start the Aave V3 Rates Indexer (background service). This survives simulation restarts.

**(Requires `MAINNET_RPC_URL` in `.env`)**

```bash
docker compose -f docker-compose.rates.yml up -d
```

### 2. Simulation Cycle

Use the helper script to restart Anvil, deploy contracts, and launch the RLD stack.

```bash
# Tears down old stack, restarts Anvil (clean fork), and re-deploys everything
./restart.sh
```

_(Alternatively, run `docker compose up --build` manually if Anvil is already ready)_

## What Happens

| Phase | Container      | Description                                                                                       |
| ----- | -------------- | ------------------------------------------------------------------------------------------------- |
| 1     | `deployer`     | Deploys protocol, market, oracle, 5 users, swap router → writes `/config/deployment.json` → exits |
| 2     | `indexer`      | Reads config, starts indexing blocks + API on port 8080                                           |
| 3     | `mm-daemon`    | Market maker daemon (arb + oracle updates)                                                        |
| 4     | `chaos-trader` | Random trades for market activity                                                                 |

## Endpoints

| URL                            | Description              |
| ------------------------------ | ------------------------ |
| `http://localhost:8080/health` | Indexer health + lag     |
| `http://localhost:8080/config` | All discovered addresses |
| `http://localhost:8080/docs`   | Swagger UI               |

## Operations

```bash
# View logs
docker compose logs -f indexer
docker compose logs -f mm-daemon
docker compose logs -f chaos-trader

# Restart from scratch
docker compose down -v && docker compose up --build

# Rebuild just daemons
docker compose build mm-daemon chaos-trader && docker compose up -d
```

## Configuration

See `.env.example` for all available variables. Key ones:

| Variable       | Default                     | Description               |
| -------------- | --------------------------- | ------------------------- |
| `RPC_URL`      | `host.docker.internal:8545` | Anvil RPC endpoint        |
| `INDEXER_PORT` | `8080`                      | Host port for indexer API |
| `DEPLOYER_KEY` | Anvil key #0                | Deploys all contracts     |
| `MM_KEY`       | Anvil key #3                | Market maker private key  |
| `CHAOS_KEY`    | Anvil key #4                | Chaos trader private key  |

## Standalone Rates Indexer

To simulate realistic rates that persist across restarts, run the standalone Aave V3 rates indexer:

```bash
# 1. Configure Alchemy RPC in .env
#   MAINNET_RPC_URL=https://eth-mainnet.g.alchemy.com/v2/...

# 2. Start Rates Indexer (port 8081)
docker compose -f docker-compose.rates.yml up -d

# 3. Verify
curl http://localhost:8081/rates?limit=1&symbol=USDC
```

This service scrapes live Aave V3 rates from mainnet and serves them to the `mm-daemon`. The `mm-daemon` will automatically use this service if `API_URL` is configured (default in `.env`).
