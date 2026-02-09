#!/bin/bash
# generate-status.sh — Collects system metrics and writes status.json
# Run via cron every minute: * * * * * sudo /home/ubuntu/RLD/docker/scripts/generate-status.sh

set -euo pipefail

OUTPUT="/home/ubuntu/RLD/dashboard/status.json"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── System metrics ──
DISK_TOTAL=$(df -BG / | awk 'NR==2{print $2}' | tr -d 'G')
DISK_USED=$(df -BG / | awk 'NR==2{print $3}' | tr -d 'G')
DISK_FREE=$(df -BG / | awk 'NR==2{print $4}' | tr -d 'G')
DISK_PCT=$(df / | awk 'NR==2{print $5}' | tr -d '%')

MEM_TOTAL=$(free -m | awk '/Mem/{print $2}')
MEM_USED=$(free -m | awk '/Mem/{print $3}')
MEM_FREE=$(free -m | awk '/Mem/{print $7}')

LOAD=$(cat /proc/loadavg | awk '{print $1}')
UPTIME_SECS=$(awk '{print int($1)}' /proc/uptime)
CPU_CORES=$(nproc)

# ── Docker containers ──
containers_json="["
first=true
while IFS='|' read -r name status ports; do
  [ -z "$name" ] && continue
  name=$(echo "$name" | xargs)
  status=$(echo "$status" | xargs)
  # Escape ports for JSON (remove problematic chars)
  ports=$(echo "$ports" | xargs | sed 's/"/\\"/g')
  
  healthy="unknown"
  if echo "$status" | grep -q "(healthy)"; then
    healthy="healthy"
  elif echo "$status" | grep -q "(unhealthy)"; then
    healthy="unhealthy"
  elif echo "$status" | grep -q "Up"; then
    healthy="running"
  else
    healthy="stopped"
  fi

  uptime_str=$(echo "$status" | sed 's/ (healthy)//' | sed 's/ (unhealthy)//')

  if [ "$first" = true ]; then first=false; else containers_json+=","; fi
  containers_json+="{\"name\":\"$name\",\"status\":\"$healthy\",\"uptime\":\"$uptime_str\",\"ports\":\"$ports\"}"
done < <(docker ps -a --format "{{.Names}}|{{.Status}}|{{.Ports}}" 2>/dev/null || true)
containers_json+="]"

# ── SSL certificate ──
SSL_EXPIRY="unknown"
SSL_DAYS="0"
if command -v certbot &>/dev/null; then
  SSL_EXPIRY=$(sudo certbot certificates 2>/dev/null | grep "Expiry" | head -1 | awk '{print $3}' || echo "unknown")
  SSL_DAYS=$(sudo certbot certificates 2>/dev/null | grep "VALID:" | head -1 | grep -oP '\d+(?= days)' || echo "0")
fi

# ── API health checks (suppress all output, only check exit code) ──
if curl -sf -m 2 -o /dev/null http://localhost:8081/ 2>/dev/null; then rates_ok="true"; else rates_ok="false"; fi
if curl -sf -m 2 -o /dev/null http://localhost:8080/ 2>/dev/null; then sim_ok="true"; else sim_ok="false"; fi
if curl -sf -m 2 -o /dev/null http://localhost:8082/health 2>/dev/null; then bot_ok="true"; else bot_ok="false"; fi

rates_block=$(curl -sf -m 2 http://localhost:8081/ 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('last_indexed_block',''))" 2>/dev/null || echo "")

# ── Nginx ──
if sudo nginx -t >/dev/null 2>&1; then nginx_ok="true"; else nginx_ok="false"; fi

# ── Git ──
GIT_COMMIT=$(cd /home/ubuntu/RLD && git log -1 --format="%h" 2>/dev/null || echo "unknown")
GIT_MSG=$(cd /home/ubuntu/RLD && git log -1 --format="%s" 2>/dev/null | head -c 60 | sed 's/"/\\"/g' || echo "unknown")
GIT_TIME=$(cd /home/ubuntu/RLD && git log -1 --format="%ci" 2>/dev/null || echo "unknown")

# ── Docker images ──
DANGLING_COUNT=$(docker images -f "dangling=true" -q 2>/dev/null | wc -l)
DOCKER_DISK=$(docker system df --format "{{.Size}}" 2>/dev/null | head -1 || echo "0")

# ── Write JSON ──
cat > "$OUTPUT" << ENDJSON
{
  "timestamp": "$TIMESTAMP",
  "system": {
    "uptime_secs": $UPTIME_SECS,
    "load": $LOAD,
    "cpu_cores": $CPU_CORES,
    "disk": { "total_gb": $DISK_TOTAL, "used_gb": $DISK_USED, "free_gb": $DISK_FREE, "percent": $DISK_PCT },
    "memory": { "total_mb": $MEM_TOTAL, "used_mb": $MEM_USED, "available_mb": $MEM_FREE }
  },
  "containers": $containers_json,
  "services": {
    "rates_indexer": { "healthy": $rates_ok, "last_block": "$rates_block" },
    "sim_indexer": { "healthy": $sim_ok },
    "monitor_bot": { "healthy": $bot_ok },
    "nginx": { "healthy": $nginx_ok }
  },
  "ssl": { "expiry": "$SSL_EXPIRY", "days_remaining": ${SSL_DAYS:-0} },
  "git": { "commit": "$GIT_COMMIT", "message": "$GIT_MSG", "time": "$GIT_TIME" },
  "docker": { "dangling_images": $DANGLING_COUNT, "images_size": "$DOCKER_DISK" }
}
ENDJSON

echo "[$(date)] Status updated → $OUTPUT"
