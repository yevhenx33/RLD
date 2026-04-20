#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# RLD Docker Log Aggregation
# ═══════════════════════════════════════════════════════════════
# Collects logs from all RLD containers into daily log files
# with automatic rotation (keeps last 7 days).
#
# Setup (cron):
#   crontab -e
#   0 * * * * /home/ubuntu/RLD/docker/scripts/collect-logs.sh
#
# Manual run:
#   ./docker/scripts/collect-logs.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

LOG_DIR="/home/ubuntu/RLD/logs"
RETENTION_DAYS=7
DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y-%m-%d_%H:%M:%S)

# Create log directory
mkdir -p "$LOG_DIR"

# Compose service names (container names are resolved dynamically)
SERVICES=(
    "reth"
    "postgres"
    "indexer"
    "mm-daemon"
    "chaos-trader"
    "faucet"
    "rates-indexer"
    "monitor-bot"
    "frontend"
)

for SERVICE in "${SERVICES[@]}"; do
    SERVICE_FILE="${SERVICE//-/_}"
    LOG_FILE="$LOG_DIR/${SERVICE_FILE}_${DATE}.log"
    SERVICE_CONTAINERS=$(docker ps --filter "label=com.docker.compose.service=${SERVICE}" --format '{{.Names}}' || true)

    [ -z "$SERVICE_CONTAINERS" ] && continue

    while IFS= read -r CONTAINER; do
        [ -z "$CONTAINER" ] && continue
        echo "--- $TIMESTAMP [$CONTAINER] ---" >> "$LOG_FILE"
        docker logs "$CONTAINER" --since 1h 2>&1 >> "$LOG_FILE" 2>/dev/null || true
    done <<< "$SERVICE_CONTAINERS"
done

# Aggregate health status
HEALTH_FILE="$LOG_DIR/health_${DATE}.log"
echo "--- $TIMESTAMP ---" >> "$HEALTH_FILE"
docker ps --format "{{.Names}}: {{.Status}}" >> "$HEALTH_FILE"

# Rotate old logs
find "$LOG_DIR" -name "*.log" -mtime +$RETENTION_DAYS -delete 2>/dev/null || true

echo "[$TIMESTAMP] Logs collected to $LOG_DIR"
