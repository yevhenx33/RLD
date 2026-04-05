# RLD Docker Infrastructure

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    rld_shared (Docker network)                │
│                                                               │
│  ┌─────────────────────┐  ┌────────────────────────────────┐ │
│  │ docker-compose.      │  │ docker-compose.reth.yml        │ │
│  │ infra.yml            │  │ (reth/docker-compose.reth.yml) │ │
│  │                      │  │                                │ │
│  │  rates-indexer ◄─────┼──┼─ mm-daemon (reads live rates)  │ │
│  │  (mainnet Aave/ETH)  │  │  reth (dev-mode node)          │ │
│  │                      │  │  postgres + indexer             │ │
│  │  Volume:             │  │  chaos-trader                   │ │
│  │  rld_rates-data      │  │  faucet (network_mode: host)   │ │
│  └──────────────────────┘  └────────────────────────────────┘ │
│                                                               │
│  ┌──────────────────────┐                                     │
│  │ docker-compose.      │  Host Nginx (:80/:443)              │
│  │ frontend.yml         │  └──► frontend (:3000)              │
│  │                      │                                     │
│  │  frontend (nginx)    │  Proxies:                           │
│  │   /graphql     → indexer:8080                              │
│  │   /api/rates   → rates-indexer:8080                        │
│  │   /rpc         → reth:8545                                 │
│  │   /api/faucet  → host:8088                                 │
│  └──────────────────────┘                                     │
└──────────────────────────────────────────────────────────────┘
```

## Stack Lifecycles

| Stack | Compose File | Lifecycle | Restart Impact |
|-------|-------------|-----------|----------------|
| **Infra** | `docker-compose.infra.yml` | Always-on, start once | None — never restarted |
| **Simulation** | `reth/docker-compose.reth.yml` | Restarted per simulation | Only sim services restart |
| **Frontend** | `docker-compose.frontend.yml` | Redeployed per CI push | Only frontend restarts |

## First-Time Setup

```bash
# 1. Create shared network (once)
docker network create rld_shared

# 2. Start always-on infrastructure
docker compose -f docker/docker-compose.infra.yml --env-file docker/.env up -d

# 3. Start simulation (includes genesis, deploy, user setup)
bash docker/reth/restart-reth.sh --fresh --with-users

# 4. Start frontend
docker compose -f docker/docker-compose.frontend.yml up -d

# 5. Setup host nginx (once)
sudo ln -sf /home/ubuntu/RLD/docker/nginx/rld-frontend.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Common Operations

### Restart Simulation (preserves rates + frontend)
```bash
# Fast restart from snapshot (~30s)
bash docker/reth/restart-reth.sh --from-snapshot --with-users

# Fresh restart with new genesis (~8min)
bash docker/reth/restart-reth.sh --fresh --with-users
```

### Deploy Frontend Update
```bash
cd frontend
VITE_API_BASE_URL=/api npx vite build
docker compose -f docker/docker-compose.frontend.yml up -d --force-recreate
```

### Check All Services
```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Networks}}"
```

## Environment

All stacks share `docker/.env`. Key variables:

| Variable | Used By | Description |
|----------|---------|-------------|
| `DEPLOYER_KEY` | simulation | Contract deployer private key |
| `MM_KEY` | simulation | Market-maker private key |
| `CHAOS_KEY` | simulation | Chaos trader private key |
| `WHALE_KEY` | simulation | Genesis whale (Anvil #9) |
| `MAINNET_RPC_URL` | infra | Ethereum mainnet RPC for rates |
| `FRONTEND_PORT` | frontend | Host port for frontend (default: 3000) |

## Host Nginx

The host-level nginx config is tracked in `docker/nginx/rld-frontend.conf`.
It proxies Cloudflare traffic (port 80/443) to the frontend container (port 3000).

SSL is handled by a self-signed certificate at `/etc/nginx/ssl/rld.{crt,key}`
with Cloudflare set to "Full" SSL mode.
