#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/ubuntu/RLD"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PORT="${DASHBOARD_LIVE_PORT:-8091}"
INTERVAL="${DASHBOARD_LIVE_INTERVAL_SEC:-1.0}"

exec "$PYTHON_BIN" "$ROOT_DIR/docker/dashboard/live_status_server.py" \
  --host 127.0.0.1 \
  --port "$PORT" \
  --interval-sec "$INTERVAL" \
  --status-path "$ROOT_DIR/docker/dashboard/status.json"
