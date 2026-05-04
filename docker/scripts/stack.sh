#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/ubuntu/RLD"
ENV_FILE="$ROOT_DIR/docker/.env"
DATA_ENV_FILE="$ROOT_DIR/backend/analytics/.env"
COMPOSE_CLICKHOUSE="$ROOT_DIR/docker/docker-compose.clickhouse.yml"
COMPOSE_DATA="$ROOT_DIR/backend/analytics/docker-compose.yml"
COMPOSE_INFRA="$ROOT_DIR/docker/docker-compose.infra.yml"
COMPOSE_SIM="$ROOT_DIR/docker/reth/docker-compose.reth.yml"
COMPOSE_FRONTEND="$ROOT_DIR/docker/docker-compose.frontend.yml"
COMPOSE_DOCS="$ROOT_DIR/docker/docker-compose.docs.yml"

usage() {
  cat <<'EOF'
Usage: docker/scripts/stack.sh <command> [args]

Commands:
  up             Start canonical runtime stacks
  down           Stop canonical runtime stacks
  restart        Restart canonical runtime stacks
  ps             Show status for canonical runtime stacks
  logs <service> Follow logs for service from canonical stacks
  smoke [args]   Run API contract smoke checks (acceptance gate)

Canonical stack order:
  clickhouse -> analytics -> infra -> simulation -> frontend -> docs

Notes:
  - This command is for steady-state runtime control only.
  - Genesis/bootstrap workflows still use docker/reth/restart-reth.sh.
EOF
}

compose_cmd() {
  local compose_file="$1"
  shift
  docker compose -f "$compose_file" --env-file "$ENV_FILE" "$@"
}

compose_data_cmd() {
  local compose_file="$1"
  shift
  docker compose -f "$compose_file" --env-file "$DATA_ENV_FILE" "$@"
}

cmd="${1:-}"
case "$cmd" in
  up)
    docker network create rld_shared 2>/dev/null || true
    compose_cmd "$COMPOSE_CLICKHOUSE" up -d
    compose_data_cmd "$COMPOSE_DATA" up -d
    compose_cmd "$COMPOSE_INFRA" up -d
    compose_cmd "$COMPOSE_SIM" up -d
    compose_cmd "$COMPOSE_FRONTEND" up -d
    compose_cmd "$COMPOSE_DOCS" up -d
    ;;
  down)
    compose_cmd "$COMPOSE_DOCS" down
    compose_cmd "$COMPOSE_FRONTEND" down
    compose_cmd "$COMPOSE_SIM" down
    compose_cmd "$COMPOSE_INFRA" down
    compose_data_cmd "$COMPOSE_DATA" down
    compose_cmd "$COMPOSE_CLICKHOUSE" down
    ;;
  restart)
    "$0" down
    "$0" up
    ;;
  ps)
    for name in clickhouse analytics infra simulation frontend docs; do
      echo "=== $name ==="
      case "$name" in
        clickhouse) compose_cmd "$COMPOSE_CLICKHOUSE" ps ;;
        analytics) compose_data_cmd "$COMPOSE_DATA" ps ;;
        infra) compose_cmd "$COMPOSE_INFRA" ps ;;
        simulation) compose_cmd "$COMPOSE_SIM" ps ;;
        frontend) compose_cmd "$COMPOSE_FRONTEND" ps ;;
        docs) compose_cmd "$COMPOSE_DOCS" ps ;;
      esac
      echo
    done
    ;;
  logs)
    service="${2:-}"
    if [ -z "$service" ]; then
      echo "Missing service name for logs command."
      usage
      exit 1
    fi
    compose_cmd "$COMPOSE_CLICKHOUSE" logs -f "$service" 2>/dev/null \
      || compose_data_cmd "$COMPOSE_DATA" logs -f "$service" 2>/dev/null \
      || compose_cmd "$COMPOSE_INFRA" logs -f "$service" 2>/dev/null \
      || compose_cmd "$COMPOSE_SIM" logs -f "$service" 2>/dev/null \
      || compose_cmd "$COMPOSE_FRONTEND" logs -f "$service" 2>/dev/null \
      || compose_cmd "$COMPOSE_DOCS" logs -f "$service"
    ;;
  smoke)
    shift || true
    python3 "$ROOT_DIR/docker/scripts/smoke_api_contracts.py" "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
