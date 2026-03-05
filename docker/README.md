# RLD Docker Deployment

Complete containerized infrastructure for the RLD Protocol: simulation stack, rates indexer, Telegram bot, and production frontend.

## Architecture

```
                  ┌──────────────────────────────────────────────────┐
                  │                 HOST (OVHCloud)                  │
                  │                                                  │
  Internet ──────►│  Nginx (443/SSL) ──► /api/ ──► rates-indexer    │
                  │       │                        (port 8081)       │
                  │       └──► /* ──► frontend/dist                  │
                  │                                                  │
                  │  Nginx (8090) ──► dashboard/index.html           │
                  │       └──► /status.json (generated every 60s)   │
                  │                                                  │
                  │  ┌─── docker_default network ───────────────┐    │
                  │  │                                          │    │
                  │  │  rates-indexer ◄── monitor-bot           │    │
                  │  │       ▲              │                   │    │
                  │  │       │              ├──► Telegram API   │    │
                  │  │  indexer ◄── mm-daemon                   │    │
                  │  │       ▲       chaos-trader               │    │
                  │  │       │                                  │    │
                  │  │  deployer (one-shot)                     │    │
                  │  └──────────────────────────────────────────┘    │
                  │                                                  │
                  │  Anvil (port 8545) ◄── all containers            │
                  │  Cron: generate-status.sh (every minute)         │
                  └──────────────────────────────────────────────────┘
```

## Quick Start

### Full Stack Restart (recommended)

```bash
# One command does everything: kills Anvil, tears down containers,
# restarts Anvil from clean fork, deploys contracts, launches all services,
# waits for health, and prints a status report.
./docker/restart.sh
```

**CLI Flags:**

| Flag          | Description                                               |
| ------------- | --------------------------------------------------------- |
| `--sim-only`  | Only restart sim stack — keep rates-indexer + bot running |
| `--no-build`  | Skip Docker image rebuilds (faster if no code changes)    |
| `--keep-data` | Preserve indexer data volume across restart               |
| `--help`      | Show usage                                                |

```bash
# Quick restart after code change to daemons only
./docker/restart.sh --sim-only

# Fast restart without rebuilding images
./docker/restart.sh --no-build

# Keep indexer history across restart
./docker/restart.sh --keep-data
```

### Manual Setup (step by step)

```bash
# 1. Start Anvil fork
anvil --fork-url $MAINNET_RPC_URL --fork-block-number 21698573 --block-time 12 --host 0.0.0.0

# 2. Start persistent services (only needed once)
docker compose -f docker/docker-compose.rates.yml --env-file docker/.env up -d
docker compose -f docker/docker-compose.bot.yml --env-file docker/.env up -d

# 3. Deploy + launch simulation
cd docker && docker compose up --build -d
```

### Frontend (production)

```bash
# Build locally — Nginx serves from frontend/dist
cd frontend && npm run build

# Or containerized
docker compose -f docker/docker-compose.frontend.yml up -d --build
```

---

## Services

### Compose Files

| File                          | Services                                   | Lifecycle      |
| ----------------------------- | ------------------------------------------ | -------------- |
| `docker-compose.yml`          | deployer, indexer, mm-daemon, chaos-trader | Per simulation |
| `docker-compose.rates.yml`    | rates-indexer                              | Persistent     |
| `docker-compose.bot.yml`      | monitor-bot                                | Persistent     |
| `docker-compose.frontend.yml` | frontend                                   | Persistent     |

### Container Details

| Container       | Image                        | Port | Health           | Depends On | Description                                                                |
| --------------- | ---------------------------- | ---- | ---------------- | ---------- | -------------------------------------------------------------------------- |
| `deployer`      | `docker/deployer/Dockerfile` | —    | Exits on success | —          | Deploys protocol, oracle, market, users, router → writes `deployment.json` |
| `indexer`       | `backend/Dockerfile.indexer` | 8080 | `python urllib`  | `deployer` | Indexes simulation blocks + serves API. Auto-resets DB on restart          |
| `mm-daemon`     | `docker/daemons/Dockerfile`  | —    | `pgrep`          | `deployer` | Market maker: arb trades + oracle updates from live rates                  |
| `chaos-trader`  | `docker/daemons/Dockerfile`  | —    | `pgrep`          | `deployer` | Random trades for market activity                                          |
| `rates-indexer` | `backend/Dockerfile.rates`   | 8081 | curl             | —          | Indexes Aave V3 rates + ETH price (Uniswap V3 slot0) per block (~12s)      |
| `monitor-bot`   | `backend/Dockerfile.bot`     | 8082 | curl             | —          | Telegram bot: `/status` dashboard, hourly rate+price digests               |
| `rld-frontend`  | `frontend/Dockerfile`        | 80   | wget             | —          | Multi-stage build: Node 20 → Nginx Alpine (68MB)                           |

### Service Ordering & Resilience

The simulation stack uses three layers of defense against startup race conditions:

1. **`depends_on: service_completed_successfully`** — `indexer`, `mm-daemon`, and `chaos-trader` only start after the `deployer` container exits with code 0. This prevents services from starting with an empty or partial `deployment.json`.

2. **`wait-for-config.sh`** (daemon entrypoint) — Validates that `deployment.json` contains a non-null `rld_core` key, not just that the file exists. Polls every 2s for up to 240s.

3. **`entrypoint.py`** (indexer) — Retries on-chain market discovery up to 30 times with 10s backoff. If contracts aren't deployed yet when the indexer starts, it waits instead of crashing.

---

## Networking

All compose-managed containers share the `docker_default` network. Services communicate by **Docker service name**, not `host.docker.internal`:

| From           | To              | URL                                |
| -------------- | --------------- | ---------------------------------- |
| `monitor-bot`  | `rates-indexer` | `http://rates-indexer:8080`        |
| `mm-daemon`    | `rates-indexer` | `http://rates-indexer:8080`        |
| All containers | Anvil           | `http://host.docker.internal:8545` |

> **Note:** External ports (8080, 8081, 8082) are exposed via Docker but **blocked by UFW** to the internet. Only SSH (22), HTTP (80), and HTTPS (443) are open externally.

---

## Indexer Auto-Reset

The simulation indexer (`entrypoint.py`) automatically detects stale data on startup:

```
[3/4] Checking for stale data (simulation restart)...
⚠️  STALE DB DETECTED: indexed block 21,800,739 > chain head 21,702,736 (lag: 98,003)
🔄 Simulation was restarted — wiping DB and re-indexing from scratch
✅ DB reset complete. Will index from chain head.
```

**Logic:** If `last_indexed_block > chain_head + 1`, the Anvil fork was restarted. The indexer wipes its SQLite DB and re-indexes from the current chain head.

---

## Production Deployment (rld.fi)

The frontend is served from `/home/ubuntu/RLD/frontend/dist` by Nginx with SSL.

### Nginx Config (`/etc/nginx/sites-available/rld.fi`)

| Feature         | Config                                                        |
| --------------- | ------------------------------------------------------------- |
| SSL             | Let's Encrypt (certbot auto-renewal)                          |
| API Proxy       | `/api/` → `localhost:8081` with server-side API key injection |
| Rate Limiting   | 10 req/s, burst 20 on `/api/`                                 |
| HSTS            | `max-age=63072000; includeSubDomains; preload`                |
| CSP             | Restrict scripts/styles/connections to approved domains       |
| Sensitive Files | `.git`, `.env` → 404                                          |
| Server Version  | Hidden (`server_tokens off`)                                  |

### Rebuild & Deploy Frontend

```bash
cd frontend && npm run build
# Nginx serves from frontend/dist — no restart needed
```

### Firewall (UFW)

| Port      | Status         | Purpose                           |
| --------- | -------------- | --------------------------------- |
| 22/tcp    | ✅ Open        | SSH                               |
| 80/tcp    | ✅ Open        | HTTP → HTTPS redirect             |
| 443/tcp   | ✅ Open        | HTTPS (Nginx)                     |
| 8545      | 🔒 Docker only | Anvil (172.16.0.0/12)             |
| 8080-8082 | 🔒 Blocked     | Internal only (Docker networking) |

---

## API Endpoints

### Simulation Indexer (port 8080)

| Endpoint                  | Description                              |
| ------------------------- | ---------------------------------------- |
| `GET /`                   | Service status                           |
| `GET /health`             | Health check                             |
| `GET /config`             | Discovered contract addresses            |
| `GET /api/latest`         | Latest market/pool/broker state          |
| `GET /api/status`         | Indexer stats (last block, total events) |
| `GET /api/events?limit=N` | Recent events                            |
| `GET /api/history/market` | Market state history                     |
| `GET /api/history/pool`   | Pool state history                       |
| `GET /api/chart/price`    | Price chart data                         |
| `GET /docs`               | Swagger UI                               |

### Rates Indexer (port 8081, proxied at `/api/`)

| Endpoint                                 | Description                                  |
| ---------------------------------------- | -------------------------------------------- |
| `GET /`                                  | Service status                               |
| `GET /rates?symbol=USDC&limit=N`         | Historical spot rates (hourly from clean DB) |
| `GET /eth-prices?limit=N`                | ETH price history (hourly, default)          |
| `GET /eth-prices?limit=N&resolution=RAW` | ETH price (block-level, ~12s, from raw DB)   |

---

## Environment Variables

### `docker/.env` (primary config)

| Variable              | Description                                             | Secret?         |
| --------------------- | ------------------------------------------------------- | --------------- |
| `RPC_URL`             | Anvil RPC (default: `http://host.docker.internal:8545`) | No              |
| `MAINNET_RPC_URL`     | **Unrestricted** Alchemy key for server-side use        | Yes             |
| `ETH_PRICE_GRAPH_URL` | The Graph API URL for Uniswap V3 ETH/USDC pool data     | Yes             |
| `RESERVE_RPC_URL`     | Infura RPC for reserve/fallback mainnet access          | Yes             |
| `DEPLOYER_KEY`        | Anvil key #0 (deploy contracts)                         | Simulation only |
| `USER_A_KEY`          | Anvil key #0 (LP provider)                              | Simulation only |
| `USER_B_KEY`          | Anvil key #1 (long user)                                | Simulation only |
| `USER_C_KEY`          | Anvil key #2 (TWAMM user)                               | Simulation only |
| `MM_KEY`              | Anvil key #3 (market maker)                             | Simulation only |
| `CHAOS_KEY`           | Anvil key #4 (chaos trader)                             | Simulation only |
| `TELEGRAM_BOT_TOKEN`  | Telegram bot auth token                                 | Yes             |
| `TELEGRAM_CHAT_ID`    | Telegram chat for reports                               | No              |
| `API_KEY`             | Rates API auth key                                      | Yes             |

### Root `.env` (protocol addresses + frontend vars)

| Variable               | Description                                            |
| ---------------------- | ------------------------------------------------------ |
| `FORK_BLOCK`           | Anvil fork block number (default: `21698573`)          |
| `MAINNET_RPC_URL`      | **Unrestricted** Alchemy key (same as docker/.env)     |
| `VITE_MAINNET_RPC_URL` | **Origin-restricted** Alchemy key (frontend on rld.fi) |
| `VITE_API_BASE_URL`    | API endpoint (`https://rld.fi/api`)                    |
| `RLD_CORE`, `WAUSDC`…  | Protocol contract addresses (auto-updated by deployer) |

> **API Key Strategy:** Two Alchemy keys are used:
>
> - **Unrestricted** (`MAINNET_RPC_URL`) — for Anvil fork + rates indexer (server-side only)
> - **Origin-restricted to `rld.fi`** (`VITE_MAINNET_RPC_URL`) — for the frontend (exposed to browsers)
>
> Only `VITE_`-prefixed vars are exposed to the browser. The rates API key is injected server-side by Nginx's `proxy_set_header X-API-Key`.

---

## Operations

### Common Tasks

```bash
# Full clean restart (Anvil + all containers)
./docker/restart.sh

# Restart sim only (keep rates + bot)
./docker/restart.sh --sim-only

# Fast restart (no image rebuild)
./docker/restart.sh --sim-only --no-build
```

### Monitoring

```bash
# View all containers
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Follow logs
docker compose -f docker/docker-compose.yml logs -f indexer
docker compose -f docker/docker-compose.yml logs -f mm-daemon
docker compose -f docker/docker-compose.bot.yml logs -f monitor-bot

# Check indexer lag
curl -s http://localhost:8080/api/status | python3 -m json.tool

# Check Anvil block
cast block-number --rpc-url http://localhost:8545
```

### Infrastructure Dashboard (port 8090)

A real-time infrastructure dashboard served by Nginx on port 8090. Auto-refreshes every **12 seconds** (1 Ethereum block time).

```bash
# Access locally
open http://localhost:8090

# Regenerate status data manually
sudo /home/ubuntu/RLD/docker/scripts/generate-status.sh

# View generation logs
tail -f /home/ubuntu/RLD/logs/status-gen.log
```

**Dashboard sections:**

| Section         | Metrics                                                 |
| --------------- | ------------------------------------------------------- |
| System          | CPU load, memory, disk, uptime, connections             |
| Containers      | Status, health, uptime for all 6 containers             |
| Services        | Health check + response time for each service endpoint  |
| Database Health | Table freshness, row counts, file sizes                 |
| Data Quality    | NULL values (7d), corrupt rows, sync age, missing hours |
| SSL & Git       | Certificate expiry, latest commit info                  |
| Activity Log    | Rolling feed of health checks, block numbers, errors    |

**How it works:**

1. `generate-status.sh` runs every minute via cron, collecting metrics from Docker, SQLite DBs, service endpoints, and system stats
2. Writes `dashboard/status.json` atomically (`mktemp` → `mv`) to prevent partial reads
3. `dashboard/index.html` (React/Babel) fetches `status.json` every 12s and renders

**Alerting thresholds (Database Health):**

| Metric      | Real-time tables (rates) | Hourly tables (eth_prices, clean_rates) |
| ----------- | ------------------------ | --------------------------------------- |
| 🟢 Fresh    | < 30 min                 | < 75 min                                |
| 🟡 Stale    | < 2 hours                | < 2.5 hours                             |
| 🔴 Critical | > 2 hours                | > 2.5 hours                             |

### Single-Service Rebuild

```bash
# Rebuild + restart just one service (no full redeploy)
docker compose -f docker/docker-compose.yml build indexer
docker compose -f docker/docker-compose.yml up -d --no-deps indexer
```

### Troubleshooting

| Symptom                           | Cause                                         | Fix                                                    |
| --------------------------------- | --------------------------------------------- | ------------------------------------------------------ |
| RPC 403 errors in daemon logs     | Alchemy API key restricted to specific origin | Use an unrestricted key in `MAINNET_RPC_URL`           |
| Port already in use on restart    | Orphaned container from previous run          | `restart.sh` handles this automatically                |
| Deployer `nonce too low`          | Rapid-fire transactions without receipt wait  | Already fixed in `deploy_all.sh`                       |
| Containers stuck in `Created`     | Deployer dependency not met                   | Fixed: `depends_on: service_completed_successfully`    |
| Services start with empty config  | `deployment.json` exists but is `{}`          | Fixed: `wait-for-config.sh` validates `rld_core` key   |
| Indexer crashes on first attempt  | Contracts not deployed when discovery runs    | Fixed: `entrypoint.py` retries 30× with 10s backoff    |
| `Cannot resolve 'indexer'` in bot | Bot on different Docker network than indexer  | Ensure both use same compose or `host.docker.internal` |
| Dashboard JSON parse error        | `status.json` read mid-write                  | Fixed: atomic writes via `mktemp` + `mv`               |
| ETH price stale by ~1 hour        | Using `1H` resolution instead of `RAW`        | Use `/eth-prices?resolution=RAW` for live price        |
| Sync age > 5min on dashboard      | `SYNC_INTERVAL` too high in daemon.py         | Set to 60s (current default)                           |
| Dashboard shows 5/6 services ok   | Stopped deployer counted in health check      | Fixed: stopped/created containers excluded from count  |

---

## CI/CD (GitHub Actions)

Automated build & deploy pipeline via `.github/workflows/deploy.yml`.

### Triggers

| Trigger             | Condition                                                          |
| ------------------- | ------------------------------------------------------------------ |
| `push` to `main`    | Only when `frontend/**`, `backend/**`, or `docker/**` files change |
| `workflow_dispatch` | Manual trigger from GitHub Actions UI                              |

> **Note:** Changes to `.github/workflows/` alone won't auto-trigger — use manual dispatch.

### Pipeline Jobs

```
push to main ──► frontend (34s) ──► deploy (29s)
                   │                    │
                   ├─ Checkout          ├─ Download build artifact
                   ├─ Node 20 + cache  ├─ SCP dist/ to server
                   ├─ npm ci           ├─ git pull --ff-only
                   ├─ npm run lint     └─ Rebuild backend if changed
                   ├─ npm run build
                   └─ Upload artifact
```

**Job 1: `frontend`** — Lint & build on `ubuntu-latest`

- Installs Node 20 with npm cache (keyed on `package-lock.json`)
- Runs ESLint (`npm run lint`) — **fails the pipeline on any error**
- Builds with Vite, injecting `VITE_API_BASE_URL` and `VITE_MAINNET_RPC_URL`
- Uploads `frontend/dist` as artifact (7-day retention)

**Job 2: `deploy`** — SCP + SSH to production (only on `main`)

- Downloads the `frontend-dist` artifact
- SCPs `dist/*` to `/home/ubuntu/RLD/frontend/` via `appleboy/scp-action@v0.1.7`
- SSHs into the server via `appleboy/ssh-action@v1.2.5`:
  - `git pull --ff-only` to sync config/backend changes
  - If `backend/` changed: rebuilds `indexer` + `monitor-bot` containers

### Required GitHub Secrets

Set these in **Settings → Secrets and variables → Actions**:

| Secret                 | Value                  | Notes                             |
| ---------------------- | ---------------------- | --------------------------------- |
| `DEPLOY_HOST`          | Server IP or hostname  | e.g., `203.0.113.42`              |
| `DEPLOY_USER`          | SSH username           | e.g., `ubuntu`                    |
| `DEPLOY_SSH_KEY`       | Full SSH private key   | Must include BEGIN/END lines      |
| `VITE_MAINNET_RPC_URL` | Alchemy/Infura RPC URL | Baked into frontend at build time |

### Deploy Key Setup

The deploy key is stored at `~/.ssh/deploy_key` on the server:

```bash
# Generate (already done)
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/deploy_key -N ""

# Public key is in authorized_keys
cat ~/.ssh/deploy_key.pub >> ~/.ssh/authorized_keys

# Copy the PRIVATE key into DEPLOY_SSH_KEY secret
cat ~/.ssh/deploy_key
# Copy entire output including -----BEGIN/END----- lines
```

### Troubleshooting

| Error                                | Cause                                  | Fix                                        |
| ------------------------------------ | -------------------------------------- | ------------------------------------------ |
| `exit code 128` (warning)            | npm cache git operation                | Harmless, ignore                           |
| `ssh: no key found`                  | `DEPLOY_SSH_KEY` empty or wrong format | Re-paste the full private key with headers |
| `unable to authenticate [publickey]` | Key mismatch                           | Use `~/.ssh/deploy_key`, not `id_ed25519`  |
| `Unable to resolve action`           | Invalid action version tag             | Check tags at github.com/appleboy/\*       |
| Pipeline not triggered on push       | Changed files outside `paths` filter   | Use manual `workflow_dispatch` or add path |

---

## Log Aggregation

Hourly cron collects logs from all containers into daily files:

```bash
# Setup (already installed via crontab)
0 * * * * /home/ubuntu/RLD/docker/scripts/collect-logs.sh
* * * * * sudo /home/ubuntu/RLD/docker/scripts/generate-status.sh >> /home/ubuntu/RLD/logs/status-gen.log 2>&1

# Manual run
./docker/scripts/collect-logs.sh

# View today's logs
ls -la logs/
cat logs/indexer_$(date +%Y-%m-%d).log
cat logs/health_$(date +%Y-%m-%d).log
```

Logs are rotated automatically after 7 days.

---

## Rate Limiting

| Zone   | Rate     | Burst | Scope               |
| ------ | -------- | ----- | ------------------- |
| `site` | 30 req/s | 60    | All pages (`/`)     |
| `api`  | 10 req/s | 20    | API proxy (`/api/`) |

---

## Data Pipeline

### ETH Price Sync

The rates-indexer daemon fetches ETH/USDC prices **on-chain** via the Uniswap V3 `slot0()` function at every block (~12s), alongside Aave V3 rate calls.

```
Uniswap V3 slot0() ──► aave_rates.db (eth_prices) ──► sync_clean_db.py ──► clean_rates.db (hourly_stats)
     (per block, ~12s)          (block-level)              (AVG per hour)         (hourly aggregated)
```

- **Primary source:** Uniswap V3 USDC/ETH 0.05% pool `slot0()` — real-time `sqrtPriceX96` at each block
- **Conversion:** `ETH_USD = 10¹² / (sqrtPriceX96² / 2¹⁹²)` (adjusts for USDC 6 / WETH 18 decimals)
- **Gap repair:** The Graph `poolHourDatas` query backfills missing data after crashes (startup only)
- **API resolutions:** `RAW` = block-level from `aave_rates.db`, `1H/4H/1D` = aggregated from `clean_rates.db`
- **Bot display:** Uses `RAW` resolution for live price, `1H` for 24h trend calculation
