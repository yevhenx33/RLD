# RLD Infrastructure Dashboard

Internal monitoring dashboard for the RLD Protocol infrastructure. Provides real-time visibility into system health, containers, services, databases, backups, and security posture.

**Access**: `https://rld.fi/dashboard/` (public) · `http://<server-ip>:8090` (internal)

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Browser (React 18 + Babel, JetBrains Mono)                   │
│  ┌──────────────┐ ┌──────────────┐                             │
│  │  index.html   │ │ finance.html │                             │
│  │  Infra Status │ │  Simulation  │                             │
│  └──────┬───────┘ └──────┬───────┘                             │
│         │                │                                     │
│         ▼                ▼                                     │
│     status.json     /api/* proxy                               │
└────────┬────────────────┬──────────────────────────────────────┘
         │                │
    ┌────▼────┐    ┌──────▼──────┐
    │ generate │    │ Nginx proxy │
    │-status.sh│    │ → :8080 API │
    │ (cron 1m)│    └─────────────┘
    └────┬────┘
         │ collects from:
    ┌────▼─────────────────────────┐
    │  Docker containers           │
    │  System metrics (/proc, df)  │
    │  API endpoints               │
    │  SSL cert (openssl)          │
    │  Git log                     │
    │  Database integrity          │
    │  Backup status               │
    └──────────────────────────────┘
```

## Files

| File                   | Description                                                  |
| ---------------------- | ------------------------------------------------------------ |
| `index.html`           | Main infra dashboard — single-file React app (no build step) |
| `finance.html`         | Simulation monitoring page — fetches from sim indexer API    |
| `status.json`          | Auto-generated system metrics, refreshed every 60s by cron   |
| `history.json`         | Rolling 60-sample time series for sparkline charts           |
| `nginx-dashboard.conf` | Nginx config serving the dashboard on port 8090              |

## Dashboard Panels

### Row 1 — Key Metrics

| Panel        | Source          | Description                                     |
| ------------ | --------------- | ----------------------------------------------- |
| **Uptime**   | `/proc/uptime`  | Server uptime                                   |
| **CPU Load** | `/proc/loadavg` | 1m/5m/15m load averages vs core count           |
| **Memory**   | `free -m`       | Used/total RAM with percentage badge            |
| **Disk**     | `df`            | Used/free/total with percentage badge           |
| **SSL Cert** | `openssl x509`  | Days until TLS cert expiry (reads PEM directly) |

### Row 2 — Resource Utilization & History

- **Resource bars**: Disk, memory, swap usage with color-coded thresholds
- **Sparklines**: 60-sample rolling history for CPU load, memory %, and indexed blocks

### Row 3 — Containers

Full Docker container table with: status dot, name, health, CPU%, memory, network I/O, restart count, uptime.

### Row 4 — Services & Security

- **Services**: Health check + response time for Nginx, Rates Indexer, Sim Indexer, Monitor Bot, Anvil
- **Security**: Hardening checklist (HSTS, CSP, X-Frame, firewall, SSH key, SSL)

### Row 4.5 — Database Health & Data Quality

- **Database Health**: Table-level row counts and freshness for `aave_rates.db` and `clean_rates.db`
- **Data Quality**: 7-day null value counts, corrupt row detection, sync age, missing hours

### Row 5 — Deploy, Backups, Docker, Activity

- **Last Deploy**: Git commit hash, message, author, and time ago
- **Backups**: Last backup timestamp, compressed size, DB count, retained snapshots
- **Docker**: Active images, dangling images, total image size
- **Activity Log**: Live scrolling log of health checks and events

## Data Pipeline

### `generate-status.sh`

The core data collector. Runs every minute via root crontab:

```
* * * * * /home/ubuntu/RLD/docker/scripts/generate-status.sh >/dev/null 2>&1
```

**Collects**:

- System metrics (CPU, memory, disk, swap, load, connections)
- Docker container stats and health (`docker stats`, `docker inspect`)
- API endpoint health and response times (curl to `:8080`, `:8081`, `:8082`, `:443`)
- Anvil RPC status (JSON-RPC `eth_blockNumber`)
- SSL cert expiry (openssl reading PEM directly — no certbot dependency)
- Nginx config validity (`nginx -t`)
- Git commit info (hash, message, author, time)
- Docker image stats (active, dangling, total size)
- Database integrity (inline Python via `docker exec` — row counts, freshness, corrupt rows, nulls)
- Backup status (reads `/home/ubuntu/RLD/backups/last_backup.json`)
- History tracking (appends to rolling 60-point time series)

**Output**: Writes atomically via `mktemp` + `mv` to `status.json`.

### `backup-databases.sh`

Daily database snapshot. Runs at 03:00 UTC:

```
0 3 * * * /home/ubuntu/RLD/docker/scripts/backup-databases.sh >/dev/null 2>&1
```

**Process**:

1. `VACUUM INTO` via `docker exec` (hot copy, no locking)
2. `docker cp` snapshot to host
3. Python `PRAGMA integrity_check` verification
4. `gzip` compression (~31MB for aave_rates, ~800KB for clean_rates)
5. 7-day automatic rotation
6. Writes `last_backup.json` for dashboard integration

**Storage**: `~/RLD/backups/YYYY-MM-DD/` (~32MB/day compressed)

## Nginx Configuration

The dashboard is served by a dedicated Nginx server block on port 8090:

- **Static files**: Serves `index.html`, `finance.html`, and JSON files with `no-cache` headers
- **API proxy**: `/api/*` routes proxy to the simulation indexer at `127.0.0.1:8080`
- **CORS**: `status.json` has `Access-Control-Allow-Origin: *` for cross-origin fetch
- **No access logs**: Disabled to reduce noise (internal tool)

### Setup

```bash
# Symlink config into Nginx
sudo ln -sf /home/ubuntu/RLD/dashboard/nginx-dashboard.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Frontend Architecture

Both pages are **zero-build single-file React apps**:

- React 18 via CDN (`unpkg.com`)
- Babel in-browser transpilation (JSX → JS)
- JetBrains Mono font from Google Fonts
- CSS variables for theming (dark terminal aesthetic)

### Design System

```
--bg:  #06080a    (background)
--s1:  #0c0e12    (card surface)
--g:   #00ff88    (green / healthy)
--a:   #ffb020    (amber / warning)
--r:   #ff4455    (red / critical)
--c:   #38bdf8    (cyan / info)
--p:   #a78bfa    (purple / accent)
```

### Auto-refresh

- `status.json` polled every **12 seconds**
- Clock and data age counter tick every **1 second**
- Stale data warning appears after **3 minutes** without fresh data

## Troubleshooting

| Symptom                             | Cause                                           | Fix                                                             |
| ----------------------------------- | ----------------------------------------------- | --------------------------------------------------------------- |
| Dashboard shows "INITIALIZING..."   | `status.json` missing or unreadable             | Run `sudo bash docker/scripts/generate-status.sh`               |
| "DATA 3m OLD" warning               | Cron not running or script failing              | Check `sudo crontab -l` and `tail ~/RLD/backups/backup.log`     |
| SSL shows 0 days                    | Cert file not readable                          | Verify `/etc/letsencrypt/live/rld.fi/fullchain.pem` exists      |
| Container shows "unhealthy"         | Healthcheck misconfigured                       | Check `docker inspect <name> --format='{{json .State.Health}}'` |
| Backups panel says "No backups yet" | Backup hasn't run or `last_backup.json` missing | Run `sudo bash docker/scripts/backup-databases.sh`              |
| Finance page blank                  | Sim indexer not running                         | Start simulation stack: `docker compose up -d`                  |
