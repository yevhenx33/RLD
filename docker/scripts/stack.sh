#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/home/ubuntu/RLD"
ENV_FILE="$ROOT_DIR/docker/.env"
COMPOSE_INFRA="$ROOT_DIR/docker/docker-compose.infra.yml"
COMPOSE_SIM="$ROOT_DIR/docker/reth/docker-compose.reth.yml"
COMPOSE_FRONTEND="$ROOT_DIR/docker/docker-compose.frontend.yml"

usage() {
  cat <<'EOF'
Usage: docker/scripts/stack.sh <command> [args]

Commands:
  up             Start canonical runtime stacks (infra, simulation, frontend)
  down           Stop canonical runtime stacks
  restart        Restart canonical runtime stacks
  ps             Show status for canonical runtime stacks
  logs <service> Follow logs for service from canonical stacks
  smoke [args]   Run API contract smoke checks (acceptance gate)

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

cmd="${1:-}"
case "$cmd" in
  up)
    docker network create rld_shared 2>/dev/null || true
    compose_cmd "$COMPOSE_INFRA" up -d
    compose_cmd "$COMPOSE_SIM" up -d
    compose_cmd "$COMPOSE_FRONTEND" up -d
    ;;
  down)
    compose_cmd "$COMPOSE_FRONTEND" down
    compose_cmd "$COMPOSE_SIM" down
    compose_cmd "$COMPOSE_INFRA" down
    ;;
  restart)
    "$0" down
    "$0" up
    ;;
  ps)
    echo "=== infra ==="
    compose_cmd "$COMPOSE_INFRA" ps
    echo
    echo "=== simulation ==="
    compose_cmd "$COMPOSE_SIM" ps
    echo
    echo "=== frontend ==="
    compose_cmd "$COMPOSE_FRONTEND" ps
    ;;
  logs)
    service="${2:-}"
    if [ -z "$service" ]; then
      echo "Missing service name for logs command."
      usage
      exit 1
    fi
    compose_cmd "$COMPOSE_INFRA" logs -f "$service" 2>/dev/null \
      || compose_cmd "$COMPOSE_SIM" logs -f "$service" 2>/dev/null \
      || compose_cmd "$COMPOSE_FRONTEND" logs -f "$service"
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
