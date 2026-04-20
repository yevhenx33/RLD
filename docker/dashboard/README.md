# RLD Dashboard Runbook

Infrastructure status dashboard served from `docker/dashboard`.

## Access

- Public: `https://rld.fi/dashboard/`
- Local: `http://127.0.0.1:8090/`

## Data Sources

`docker/scripts/generate-status.sh` writes:
- `docker/dashboard/status.json`
- `docker/dashboard/history.json`

Collected signals include:
- system health (cpu/memory/disk/load)
- docker container status/health
- launch-critical service probes (`8080`, `8081`, `8083`, `rld.fi`)
- reth RPC status and node metrics
- indexer-driven simulation market metrics (`/api/status`, `/api/market-info`)
- rates + simulation DB integrity checks
- backup status (`backups/last_backup.json`)
- automation flags (`status_job_scheduled`, `backup_job_scheduled`)

## Cron Baseline

```cron
* * * * * /home/ubuntu/RLD/docker/scripts/generate-status.sh >> /home/ubuntu/RLD/logs/status-gen.log 2>&1
0 * * * * /home/ubuntu/RLD/docker/scripts/collect-logs.sh >> /home/ubuntu/RLD/logs/cron.log 2>&1
0 3 * * * /home/ubuntu/RLD/docker/scripts/backup-databases.sh >> /home/ubuntu/RLD/logs/backup-cron.log 2>&1
```

## Non-Launch Pipeline Metrics

`gen_pipeline_stats.py` is a compatibility shim for legacy cron paths and marks data-pipeline metrics as non-launch-critical.

## Nginx Setup

Dashboard nginx config:
- `docker/dashboard/nginx-dashboard.conf` (listens on `8090`)

Host routing:
- `docker/nginx/rld-frontend.conf` routes `/dashboard/` to `127.0.0.1:8090`

## Troubleshooting

```bash
# Regenerate status immediately
bash /home/ubuntu/RLD/docker/scripts/generate-status.sh

# Verify automation flags
python3 - <<'PY'
import json
print(json.load(open('/home/ubuntu/RLD/docker/dashboard/status.json'))['automation'])
PY

# Check dashboard nginx config
sudo nginx -t
```
