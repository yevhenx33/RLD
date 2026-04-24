#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# restart-reth.sh — Full Stack Deploy via Reth
# ═══════════════════════════════════════════════════════════════
# Single command that:
#   1. Tears down everything
#   2. Starts temporary Anvil fork → deploys protocol
#   3. Dumps Anvil state → converts to Reth genesis
#   4. Kills Anvil → starts Reth (disk-backed, no memory leak)
#   5. Launches docker services (indexer, daemons)
#
# Usage:
#   ./docker/reth/restart-reth.sh              # Full run
#   ./docker/reth/restart-reth.sh --no-build   # Skip Docker rebuilds
#   ./docker/reth/restart-reth.sh --skip-genesis # Reuse existing genesis
#   ./docker/reth/restart-reth.sh --fresh      # Wipe Reth datadir
#   ./docker/reth/restart-reth.sh --with-bots  # Start mm/chaos bots too
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Paths ────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
RLD_ROOT="$(dirname "$DOCKER_DIR")"

COMPOSE_ANVIL="$DOCKER_DIR/docker-compose.yml"        # Existing Anvil compose (for deployer)
COMPOSE_RETH="$SCRIPT_DIR/docker-compose.reth.yml"     # Reth services (no deployer)
COMPOSE_INFRA="$DOCKER_DIR/docker-compose.infra.yml"
ENVIO_COMPOSE="$RLD_ROOT/data-pipeline/docker-compose.yml"
ENV_FILE="$DOCKER_DIR/.env"
DEPLOY_JSON="$DOCKER_DIR/deployment.json"
GENESIS_FILE="$SCRIPT_DIR/genesis.json"
DEPLOY_SNAPSHOT="$SCRIPT_DIR/deployment-snapshot.json"

RETH_PORT="${RETH_PORT:-8545}"
RETH_RPC="http://localhost:$RETH_PORT"
RETH_PROJECT="${COMPOSE_PROJECT_NAME:-reth}"

ANVIL_PORT=8545
ANVIL_RPC="http://localhost:$ANVIL_PORT"
ANVIL_LOG="/tmp/anvil.log"

DEPLOYER_TIMEOUT=600
LOCK_FILE="/tmp/rld-restart-reth.lock"
ANVIL_PID_FILE="/tmp/rld-restart-reth.anvil.pid"
ANVIL_PID=""
CURRENT_PHASE="bootstrap"
CLEANUP_DONE=false

# ─── Parse args ───────────────────────────────────────────────
NO_BUILD=false
SKIP_GENESIS=false
FRESH=false
WITH_USERS=false
WITH_BOTS=false
SKIP_E2E=false
FROM_SNAPSHOT=false

for arg in "$@"; do
    case "$arg" in
        --no-build)       NO_BUILD=true ;;
        --skip-genesis)   SKIP_GENESIS=true ;;
        --fresh)          FRESH=true ;;
        --with-users)     WITH_USERS=true ;;
        --with-bots)      WITH_BOTS=true ;;
        --skip-e2e)       SKIP_E2E=true ;;
        --from-snapshot)  FROM_SNAPSHOT=true; SKIP_GENESIS=true ;;
        --help|-h)
            echo "Usage: $0 [--no-build] [--skip-genesis] [--fresh] [--with-users] [--with-bots] [--skip-e2e] [--from-snapshot]"
            echo ""
            echo "  --no-build       Skip Docker image rebuilds"
            echo "  --skip-genesis   Reuse existing genesis.json (skip Anvil deploy)"
            echo "  --fresh          Wipe Reth datadir before starting"
            echo "  --with-users     Run full user setup (LP + MM + CHAOS) on Reth"
            echo "  --with-bots      Start mm-daemon and chaos-trader after deployment"
            echo "  --skip-e2e       Skip read-only protocol e2e verification"
            echo "                   (SimFunder faucet prep still runs)"
            echo "  --from-snapshot  Restore genesis from latest snapshot (fast restart)"
            exit 0
            ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# Auto-set --skip-genesis if genesis already exists (unless --fresh)
if [ "$FRESH" = true ]; then
    SKIP_GENESIS=false
elif [ "$SKIP_GENESIS" = false ] && [ -f "$GENESIS_FILE" ]; then
    SKIP_GENESIS=true
fi

# ─── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
DIM='\033[2m'
NC='\033[0m'

header()  { echo -e "\n${BLUE}═══ $1 ═══${NC}\n"; }
step()    { echo -e "${YELLOW}[$1] $2${NC}"; }
ok()      { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()    { echo -e "${RED}  ✗ $1${NC}"; }
info()    { echo -e "${CYAN}  ℹ $1${NC}"; }
warn()    { echo -e "${YELLOW}  ⚠ $1${NC}"; }
dim()     { echo -e "${DIM}    $1${NC}"; }

# Running bots without provisioning users leaves bot wallets empty.
if [ "$WITH_BOTS" = true ] && [ "$WITH_USERS" = false ]; then
    WITH_USERS=true
    info "--with-bots implies --with-users (provision MM/Chaos balances)"
fi

compose_cid() {
    local compose_file="$1"
    local service="$2"
    docker compose -f "$compose_file" --env-file "$ENV_FILE" ps -q "$service" 2>/dev/null | head -1
}

service_container() {
    local service="$1"
    docker ps --filter "label=com.docker.compose.service=$service" --format '{{.Names}}' | head -1
}

envio_health_ok() {
    local base_url="$1"
    curl -sf --connect-timeout 2 --max-time 4 "${base_url}/healthz" >/dev/null 2>&1
}

envio_ready_ok() {
    local base_url="$1"
    curl -sf --connect-timeout 2 --max-time 4 "${base_url}/readyz" >/dev/null 2>&1
}

ensure_envio_api() {
    local base_url="$1"
    local max_attempts="${2:-45}"

    # Ensure the canonical Envio GraphQL service is up (idempotent).
    if [ -f "$ENVIO_COMPOSE" ]; then
        docker compose -f "$ENVIO_COMPOSE" up -d graphql_api >/dev/null 2>&1 || true
    fi

    for i in $(seq 1 "$max_attempts"); do
        if envio_health_ok "$base_url"; then
            return 0
        fi

        # Self-heal common failure mode (EMFILE / stuck listener).
        if [ "$i" -eq 10 ]; then
            if docker ps -a --format '{{.Names}}' | awk '$0=="rld_graphql_api"{found=1} END{exit !found}'; then
                warn "Envio API unhealthy; restarting rld_graphql_api"
                docker restart rld_graphql_api >/dev/null 2>&1 || true
            fi
        fi

        # Last recovery attempt: re-up the compose service.
        if [ "$i" -eq 20 ] && [ -f "$ENVIO_COMPOSE" ]; then
            warn "Envio API still unavailable; re-creating graphql_api service"
            docker compose -f "$ENVIO_COMPOSE" up -d --force-recreate graphql_api >/dev/null 2>&1 || true
        fi

        [ "$i" -lt "$max_attempts" ] && sleep 1
    done

    return 1
}

ensure_envio_api_ready() {
    local base_url="$1"
    local max_attempts="${2:-45}"

    for i in $(seq 1 "$max_attempts"); do
        if envio_ready_ok "$base_url"; then
            return 0
        fi
        [ $((i % 15)) -eq 0 ] && dim "Waiting for rates readiness at ${base_url}/readyz ... (${i}/${max_attempts}s)"
        [ "$i" -lt "$max_attempts" ] && sleep 1
    done

    return 1
}

volume_name() {
    local short_name="$1"
    echo "${RETH_PROJECT}_${short_name}"
}

kill_pid_if_running() {
    local pid="${1:-}"
    local label="${2:-process}"
    if [ -z "$pid" ] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
        return 0
    fi
    if kill -0 "$pid" 2>/dev/null; then
        warn "Stopping $label (pid=$pid)"
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
}

acquire_run_lock() {
    if ! command -v flock &>/dev/null; then
        fail "flock not found in PATH"
        exit 1
    fi
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        fail "Another restart-reth run is already active (lock: $LOCK_FILE)"
        exit 1
    fi
    ok "Acquired run lock"
}

cleanup_trap() {
    local exit_code=$?
    if [ "$CLEANUP_DONE" = true ]; then
        return
    fi
    CLEANUP_DONE=true

    kill_pid_if_running "${ANVIL_PID:-}" "temporary Anvil"
    if [ -f "$ANVIL_PID_FILE" ]; then
        local tracked_pid
        tracked_pid=$(cat "$ANVIL_PID_FILE" 2>/dev/null || echo "")
        kill_pid_if_running "$tracked_pid" "tracked Anvil"
        rm -f "$ANVIL_PID_FILE"
    fi

    if [ "$exit_code" -ne 0 ]; then
        warn "restart-reth aborted in phase '${CURRENT_PHASE}' (exit=$exit_code)"
    fi
}

wait_for_service_ready() {
    local service="$1"
    local timeout_seconds="${2:-90}"
    local i
    local cid=""
    local state="missing"
    local health="none"

    for i in $(seq 1 "$timeout_seconds"); do
        cid="$(compose_cid "$COMPOSE_RETH" "$service")"
        if [ -n "$cid" ]; then
            state=$(docker inspect --format '{{.State.Status}}' "$cid" 2>/dev/null || echo "unknown")
            health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "none")
            if [ "$state" = "running" ] && { [ "$health" = "none" ] || [ "$health" = "healthy" ]; }; then
                ok "$service ready (state=$state, health=$health)"
                return 0
            fi
        else
            state="missing"
            health="none"
        fi
        [ $((i % 15)) -eq 0 ] && dim "Waiting for $service... (state=$state health=$health)"
        sleep 1
    done

    fail "$service failed readiness check (state=$state health=$health)"
    docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" logs "$service" --tail 30 2>&1 || true
    return 1
}

acquire_run_lock
trap cleanup_trap EXIT INT TERM

# ═════════════════════════════════════════════════════════════
# PREFLIGHT
# ═════════════════════════════════════════════════════════════
CURRENT_PHASE="preflight"
header "PREFLIGHT CHECKS"

for cmd in docker cast jq curl python3 tar flock sha256sum; do
    command -v "$cmd" &>/dev/null || { fail "$cmd not found in PATH"; exit 1; }
done
ok "Required tools found"

docker compose version &>/dev/null || { fail "docker compose v2 not available"; exit 1; }
ok "Docker compose v2 available"

[ ! -f "$ENV_FILE" ] && { fail "$ENV_FILE not found"; exit 1; }

source <(grep -E '^(MAINNET_RPC_URL|FORK_BLOCK|DEPLOYER_KEY|USER_A_KEY|USER_B_KEY|USER_C_KEY|MM_KEY|CHAOS_KEY|INDEXER_PORT|DB_PORT|RATES_PORT|RATES_API_BASE_URL|RATES_API_PORT|ENVIO_API_URL|ENVIO_API_PORT|ENVIO_PORT|API_URL|RATES_API_URL|INDEXER_ADMIN_TOKEN|INDEXER_ALLOW_UNSAFE_ADMIN_RESET|MIN_BORROW_APY|MAX_BORROW_APY|MAX_RATE_AGE_SECONDS|REQUIRE_RATE_TIMESTAMP|RATES_TIMEOUT)=' "$ENV_FILE" | sed 's/^/export /')
if [ -z "${FORK_BLOCK:-}" ] && [ -f "$RLD_ROOT/.env" ]; then
    FORK_BLOCK=$(grep -E '^FORK_BLOCK=' "$RLD_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "")
fi
FORK_BLOCK="${FORK_BLOCK:-24660000}"
RATES_API_PORT="${RATES_API_PORT:-${ENVIO_API_PORT:-${ENVIO_PORT:-5000}}}"
RATES_API_BASE_URL="${RATES_API_BASE_URL:-${ENVIO_API_URL:-${API_URL:-${RATES_API_URL:-http://localhost:${RATES_API_PORT}}}}}"
# Backward-compatible aliases for one migration window.
API_URL="${API_URL:-$RATES_API_BASE_URL}"
ENVIO_API_URL="${ENVIO_API_URL:-$RATES_API_BASE_URL}"
RATES_API_URL="${RATES_API_URL:-$RATES_API_BASE_URL}"
ENVIO_API_PORT="${ENVIO_API_PORT:-$RATES_API_PORT}"
ENVIO_PORT="${ENVIO_PORT:-$RATES_API_PORT}"
ACTIVE_RATE_API_URL="${RATES_API_BASE_URL:-http://localhost:${RATES_API_PORT}}"
if [ -z "${INDEXER_ADMIN_TOKEN:-}" ] && [ "${INDEXER_ALLOW_UNSAFE_ADMIN_RESET:-false}" != "true" ]; then
    fail "INDEXER_ADMIN_TOKEN is required (set INDEXER_ALLOW_UNSAFE_ADMIN_RESET=true only for local unsafe workflows)"
    exit 1
fi

if [ "$SKIP_GENESIS" = true ]; then
    ok "Will reuse existing genesis.json (--skip-genesis)"
else
    [ -z "${MAINNET_RPC_URL:-}" ] && { fail "MAINNET_RPC_URL not set in $ENV_FILE"; exit 1; }
    command -v anvil &>/dev/null || { fail "anvil not found — needed to generate genesis"; exit 1; }
    ok "Anvil + mainnet RPC available for genesis generation"
fi

# ═════════════════════════════════════════════════════════════
# STEP 1: TEAR DOWN
# ═════════════════════════════════════════════════════════════
CURRENT_PHASE="teardown"
header "STEP 1: TEAR DOWN"

step "1a" "Stopping simulation stack..."
# Only tear down the simulation compose — infra (Envio + monitor-bot) and frontend are independent
docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" down 2>/dev/null || true
docker compose -f "$COMPOSE_ANVIL" --env-file "$ENV_FILE" down -v 2>/dev/null || true
# Ensure shared network exists (infra + frontend depend on it)
docker network create rld_shared 2>/dev/null || true
ok "Simulation stack stopped (infra + frontend untouched)"

step "1b" "Cleaning tracked temporary processes..."
if [ -f "$ANVIL_PID_FILE" ]; then
    STALE_ANVIL_PID=$(cat "$ANVIL_PID_FILE" 2>/dev/null || echo "")
    kill_pid_if_running "$STALE_ANVIL_PID" "stale tracked Anvil"
    rm -f "$ANVIL_PID_FILE"
else
    dim "No tracked temporary Anvil PID found"
fi
ok "Process cleanup complete"

step "1c" "Clearing deployment.json..."
echo '{}' > "$DEPLOY_JSON"
ok "deployment.json cleared"

if [ "$FRESH" = true ]; then
    step "1d" "Wiping Reth data (--fresh)..."
    # Remove postgres volume too for a fully clean state
    docker volume rm "$(volume_name "reth-datadir")" "$(volume_name "postgres-data-reth")" 2>/dev/null || true
    rm -f "$GENESIS_FILE" "$DEPLOY_SNAPSHOT"
    ok "Clean slate (Docker volumes + genesis removed)"
fi

# ═════════════════════════════════════════════════════════════
# STEP 2: GENERATE GENESIS (via temporary Anvil deploy)
# ═════════════════════════════════════════════════════════════
if [ "$SKIP_GENESIS" = false ]; then
    CURRENT_PHASE="generate-genesis"
    header "STEP 2: GENERATE GENESIS"

    # ── 2a. Start temporary Anvil ──
    step "2a" "Starting Anvil fork (block $FORK_BLOCK)..."
    mkdir -p /tmp/anvil-state
    nohup anvil \
        --fork-url "$MAINNET_RPC_URL" \
        --fork-block-number "$FORK_BLOCK" \
        --chain-id 31337 \
        --host 0.0.0.0 \
        --port "$ANVIL_PORT" \
        --code-size-limit 100000 \
        --dump-state /tmp/anvil-state/state.json \
        > "$ANVIL_LOG" 2>&1 &
    ANVIL_PID=$!
    echo "$ANVIL_PID" > "$ANVIL_PID_FILE"

    for i in $(seq 1 60); do
        if cast block-number --rpc-url "$ANVIL_RPC" > /dev/null 2>&1; then
            ok "Anvil ready at block $(cast block-number --rpc-url "$ANVIL_RPC") (PID: $ANVIL_PID)"
            break
        fi
        [ $((i % 15)) -eq 0 ] && dim "Waiting... (${i}/60s)"
        sleep 1
    done
    cast block-number --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || { fail "Anvil failed to start"; tail -20 "$ANVIL_LOG"; exit 1; }

    cast rpc anvil_setChainId 31337 --rpc-url "$ANVIL_RPC" > /dev/null 2>&1

    # ── 2b. Deploy protocol via docker compose ──
    step "2b" "Deploying protocol on Anvil (via docker compose)..."
    dim "This takes ~5-8 min..."

    BUILD_FLAG=""
    [ "$NO_BUILD" = false ] && BUILD_FLAG="--build"
    # Only bring up services needed by host-run deploy orchestrator.
    docker compose -f "$COMPOSE_ANVIL" --env-file "$ENV_FILE" up -d $BUILD_FLAG postgres indexer 2>&1 | tail -3

    # Deployer now pulls index rate from the unified REST oracle endpoint.
    ENVIO_DEFAULT_URL="http://localhost:${RATES_API_PORT}"
    LIVE_RATE_API_URL="${RATES_API_BASE_URL}"
    if ensure_envio_api "$LIVE_RATE_API_URL" 20; then
        ok "Live rate API reachable at ${LIVE_RATE_API_URL}"
    else
        if [ "$LIVE_RATE_API_URL" != "$ENVIO_DEFAULT_URL" ] && ensure_envio_api "$ENVIO_DEFAULT_URL" 45; then
            warn "Primary live-rate API unavailable at ${LIVE_RATE_API_URL}; falling back to ${ENVIO_DEFAULT_URL}"
            LIVE_RATE_API_URL="$ENVIO_DEFAULT_URL"
            ok "Live rate API reachable at ${LIVE_RATE_API_URL}"
        else
            fail "Live rate API unavailable at ${LIVE_RATE_API_URL} and fallback ${ENVIO_DEFAULT_URL} (required for live index rate). Failing fast."
            kill "$ANVIL_PID" 2>/dev/null || true
            exit 1
        fi
    fi
    ACTIVE_RATE_API_URL="$LIVE_RATE_API_URL"
    if ensure_envio_api_ready "$LIVE_RATE_API_URL" 45; then
        ok "Live rate API readiness passed at ${LIVE_RATE_API_URL}/readyz"
    else
        fail "Live rate API is reachable but not ready at ${LIVE_RATE_API_URL}/readyz"
        kill "$ANVIL_PID" 2>/dev/null || true
        exit 1
    fi

    # Run deployment orchestrator on host to avoid container→host RPC routing
    # issues observed in this environment (host.docker.internal timeouts).
    step "2b.1" "Running host deployment orchestrator..."
    INDEXER_RESET_URL="http://localhost:${INDEXER_PORT:-8080}/admin/reset"
    RATES_API_BASE_URL="$LIVE_RATE_API_URL" \
    API_URL="$LIVE_RATE_API_URL" \
    ENVIO_API_URL="$LIVE_RATE_API_URL" \
    RATES_API_URL="$LIVE_RATE_API_URL" \
    REQUIRE_LIVE_RATE=1 \
    INDEXER_RESET_URL="$INDEXER_RESET_URL" \
    INDEXER_ADMIN_TOKEN="${INDEXER_ADMIN_TOKEN:-}" \
    DEPLOYMENT_JSON_OUT="$DEPLOY_JSON" \
    RPC_URL="$ANVIL_RPC" \
    FORK_BLOCK="$FORK_BLOCK" \
    DEPLOYER_KEY="$DEPLOYER_KEY" \
    DEPLOY_BOND_FACTORY=true \
    python3 "$DOCKER_DIR/deployer/deploy_protocol_snapshot.py" || {
        fail "Host deploy orchestrator failed"
        kill "$ANVIL_PID" 2>/dev/null || true
        exit 1
    }
    ok "Host deploy orchestrator completed"

    # ── 2c. Warm up external contracts ──
    step "2c" "Warming up contract cache..."
    for addr in \
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" \
        "0x43506849D7C04F9138D1A2050bbF3A0c054402dd" \
        "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c" \
        "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2" \
        "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e" \
        "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497" \
        "0x000000000004444c5dc75cB358380D2e3dE08A90" \
        "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e" \
        "0x000000000022D473030F116dDEE9F6B43aC78BA3" \
        "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"; do
        cast code "$addr" --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
    done
    # Warm Aave internals
    cast call "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2" \
        "getReserveData(address)" "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" \
        --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
    # Warm deployed protocol contracts
    for key in rld_core ghost_router twap_engine twap_engine_lens twamm_hook wausdc position_token broker_factory broker_router swap_router mock_oracle; do
        addr=$(jq -r ".$key // empty" "$DEPLOY_JSON" 2>/dev/null)
        [ -n "$addr" ] && [ "$addr" != "null" ] && cast code "$addr" --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
    done
    ok "Contract cache warmed"

    # ── 2d. Dump Anvil state ──
    step "2d" "Dumping Anvil state..."
    cast rpc anvil_dumpState --rpc-url "$ANVIL_RPC" 2>/dev/null > /tmp/anvil-dump-raw.txt

    python3 -c "
import json, gzip

raw = open('/tmp/anvil-dump-raw.txt').read().strip()
if raw.startswith('\"') and raw.endswith('\"'):
    raw = raw[1:-1]
if raw.startswith('0x'):
    raw = raw[2:]

raw_bytes = bytes.fromhex(raw)
if raw_bytes[:2] == b'\x1f\x8b':
    data = json.loads(gzip.decompress(raw_bytes))
else:
    data = json.loads(raw_bytes)

json.dump(data, open('/tmp/anvil-dump.json', 'w'))
accounts = data.get('accounts', data)
print(f'  {len(accounts)} accounts dumped')
"
    rm -f /tmp/anvil-dump-raw.txt
    ok "State dumped"

    # ── 2e. Convert to genesis ──
    step "2e" "Converting to Reth genesis..."
    FUND_ARGS=""
    for VAR in DEPLOYER_KEY USER_A_KEY USER_B_KEY USER_C_KEY MM_KEY CHAOS_KEY; do
        [ -n "${!VAR:-}" ] && FUND_ARGS="$FUND_ARGS ${!VAR}"
    done
    WAUSDC_ADDR=$(jq -r '.wausdc // empty' "$DEPLOY_JSON" 2>/dev/null || echo "")
    SIM_FUNDER_ADDR=$(jq -r '.sim_funder // empty' "$DEPLOY_JSON" 2>/dev/null || echo "")
    SIMFUNDER_WAUSDC_RESERVE="${SIMFUNDER_WAUSDC_RESERVE:-1000000000000000}" # 1B waUSDC (6 decimals)

    WAUSDC_PATCH_ARGS=""
    if [ -n "$WAUSDC_ADDR" ] && [ "$WAUSDC_ADDR" != "null" ] && [ -n "$SIM_FUNDER_ADDR" ] && [ "$SIM_FUNDER_ADDR" != "null" ]; then
        WAUSDC_PATCH_ARGS="--wausdc-address $WAUSDC_ADDR --wausdc-reserve-address $SIM_FUNDER_ADDR --wausdc-reserve-amount $SIMFUNDER_WAUSDC_RESERVE"
        dim "waUSDC reserve patch: $SIM_FUNDER_ADDR <= $SIMFUNDER_WAUSDC_RESERVE"
    fi

    python3 "$SCRIPT_DIR/convert_state.py" \
        --input /tmp/anvil-dump.json \
        --output "$GENESIS_FILE" \
        --chain-id 31337 \
        --fund-keys $FUND_ARGS \
        --anvil-rpc "$ANVIL_RPC" \
        $WAUSDC_PATCH_ARGS \
        --patch-contracts \
            0x000000000004444c5dc75cB358380D2e3dE08A90 \
            0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e \
            0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203 \
            0x7ffe42c4a5deea5b0fec41c94c136cf115597227 \
            0xd1428ba554f4c8450b763a0b2040a4935c63f06c \
            0x66a9893cc07d91d95644aedd05d03f95e1dba8af \
            0x000000000022D473030F116dDEE9F6B43aC78BA3 \
            0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2 \
            0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c \
            0x4c9EDD5852cd905f086C759E8383e09bff1E68B3 \
            0x6c3ea9036406852006290770BEdFcAbA0e23A0e8 \
            0x02950460E2b9529D0E00284A5fA2d7bDF3fA4d72 \
            0x383E6b4437b59fff47B619CBA855CA29342A8559 \
            0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb \
            0xE6212D05cB5aF3C821Fef1C1A233a678724F9E7E \
            0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC \
            0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341 \
            0x64b761D848206f447Fe2dd461b0c635Ec39EbB27

    rm -f /tmp/anvil-dump.json
    GENESIS_SIZE=$(du -h "$GENESIS_FILE" | cut -f1)
    ok "Genesis: $GENESIS_FILE ($GENESIS_SIZE)"

    # ── 2f. Save deployment snapshot ──
    step "2f" "Saving deployment snapshot..."
    cp "$DEPLOY_JSON" "$DEPLOY_SNAPSHOT"
    # Patch block numbers for Reth (all state baked into genesis = block 0)
    python3 -c "
import json
d = json.load(open('$DEPLOY_SNAPSHOT'))
d['deploy_block'] = 0
d['fork_block'] = 0
d['session_start_block'] = 0
d['deploy_timestamp'] = 0
json.dump(d, open('$DEPLOY_SNAPSHOT', 'w'), indent=2)
print(f'  Patched deploy_block/fork_block → 0 for Reth')
"
    ok "Deployment config saved (block numbers patched for Reth)"
    cp "$DEPLOY_SNAPSHOT" "$DEPLOY_JSON"

    # ── 2g. Tear down Anvil stack ──
    step "2g" "Tearing down Anvil stack..."
    docker compose -f "$COMPOSE_ANVIL" --env-file "$ENV_FILE" down -v 2>/dev/null || true
    kill "$ANVIL_PID" 2>/dev/null || true
    wait "$ANVIL_PID" 2>/dev/null || true
    ANVIL_PID=""
    rm -f "$ANVIL_PID_FILE"
    ok "Anvil stack stopped"

    # ── 2h. Save genesis snapshot ──
    step "2h" "Saving genesis snapshot..."
    SNAPSHOT_DIR="$SCRIPT_DIR/snapshots"
    mkdir -p "$SNAPSHOT_DIR"
    SNAPSHOT_NAME="genesis-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$SNAPSHOT_DIR/$SNAPSHOT_NAME" \
        -C "$SCRIPT_DIR" genesis.json deployment-snapshot.json 2>/dev/null
    ok "Snapshot saved: snapshots/$SNAPSHOT_NAME ($(du -h "$SNAPSHOT_DIR/$SNAPSHOT_NAME" | cut -f1))"
    SNAPSHOT_SHA=$(sha256sum "$SNAPSHOT_DIR/$SNAPSHOT_NAME" | awk '{print $1}')
    echo "$SNAPSHOT_SHA  $SNAPSHOT_NAME" > "$SNAPSHOT_DIR/$SNAPSHOT_NAME.sha256"
    dim "  checksum: $SNAPSHOT_SHA"
    # Keep last 3 snapshots
    ls -t "$SNAPSHOT_DIR"/genesis-*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f
    for checksum_file in "$SNAPSHOT_DIR"/genesis-*.tar.gz.sha256; do
        [ -e "$checksum_file" ] || continue
        [ -f "${checksum_file%.sha256}" ] || rm -f "$checksum_file"
    done
    SNAPSHOT_COUNT=$(ls "$SNAPSHOT_DIR"/genesis-*.tar.gz 2>/dev/null | wc -l)
    dim "  $SNAPSHOT_COUNT snapshot(s) retained"

else
    CURRENT_PHASE="restore-genesis"
    header "STEP 2: GENESIS (SKIPPED)"

    # Auto-restore from snapshot if genesis is missing
    SNAPSHOT_DIR="$SCRIPT_DIR/snapshots"
    if [ ! -f "$GENESIS_FILE" ] || [ "$FROM_SNAPSHOT" = true ]; then
        LATEST_SNAPSHOT=$(ls -t "$SNAPSHOT_DIR"/genesis-*.tar.gz 2>/dev/null | head -1)
        if [ -n "$LATEST_SNAPSHOT" ]; then
            step "2r" "Restoring from snapshot: $(basename "$LATEST_SNAPSHOT")..."
            CHECKSUM_FILE="${LATEST_SNAPSHOT}.sha256"
            if [ -f "$CHECKSUM_FILE" ]; then
                if ! (cd "$SNAPSHOT_DIR" && sha256sum -c "$(basename "$CHECKSUM_FILE")" >/dev/null 2>&1); then
                    fail "Snapshot checksum validation failed for $(basename "$LATEST_SNAPSHOT")"
                    exit 1
                fi
                ok "Snapshot checksum verified"
            else
                warn "No checksum file found for $(basename "$LATEST_SNAPSHOT")"
            fi
            tar -xzf "$LATEST_SNAPSHOT" -C "$SCRIPT_DIR"
            ok "Genesis restored from snapshot"
        else
            fail "No genesis.json and no snapshots found — run with --fresh first"
            exit 1
        fi
    else
        ok "Reusing existing genesis.json ($(du -h "$GENESIS_FILE" | cut -f1))"
    fi

    if [ ! -f "$DEPLOY_SNAPSHOT" ]; then
        fail "deployment-snapshot.json not found — run without --skip-genesis first"
        exit 1
    fi
    cp "$DEPLOY_SNAPSHOT" "$DEPLOY_JSON"
    ok "deployment.json restored from snapshot (deploy_block=0)"
fi

# ═════════════════════════════════════════════════════════════
# STEP 3: START RETH (Docker)
# ═════════════════════════════════════════════════════════════
CURRENT_PHASE="start-reth"
header "STEP 3: START RETH"

# Wipe reth datadir volume if genesis was just regenerated
if [ "$SKIP_GENESIS" = false ]; then
    docker volume rm "$(volume_name "reth-datadir")" 2>/dev/null || true
fi

step "3a" "Starting Reth container..."
docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" up -d reth 2>&1 | tail -5

# Wait for Reth to be ready (healthcheck + RPC verification)
step "3b" "Waiting for Reth RPC..."
for i in $(seq 1 60); do
    if cast block-number --rpc-url "$RETH_RPC" > /dev/null 2>&1; then
        BLOCK=$(cast block-number --rpc-url "$RETH_RPC")
        ok "Reth ready at block $BLOCK (took ${i}s)"
        break
    fi
    if [ $((i % 10)) -eq 0 ]; then
        dim "Waiting... (${i}/60s)"
    fi
    sleep 1
done

if ! cast block-number --rpc-url "$RETH_RPC" > /dev/null 2>&1; then
    fail "Reth failed to start after 60s"
    docker compose -f "$COMPOSE_RETH" logs reth --tail 30 2>&1
    exit 1
fi

# Verify deployed contracts
step "3c" "Verifying protocol contracts..."
ZERO_ADDR="0x0000000000000000000000000000000000000000"
VERIFY_KEYS="rld_core ghost_router twap_engine twamm_hook"
FOUND_VERIFY=false
for key in $VERIFY_KEYS; do
    VERIFY_ADDR=$(jq -r ".$key // empty" "$DEPLOY_JSON")
    [ -z "$VERIFY_ADDR" ] && continue
    [ "$VERIFY_ADDR" = "null" ] && continue
    [ "${VERIFY_ADDR,,}" = "${ZERO_ADDR,,}" ] && continue

    CODE_LEN=$(cast code "$VERIFY_ADDR" --rpc-url "$RETH_RPC" 2>/dev/null | wc -c)
    if [ "$CODE_LEN" -gt 4 ]; then
        ok "Verified $key at $VERIFY_ADDR"
        FOUND_VERIFY=true
    else
        fail "Contract $key at $VERIFY_ADDR has no code — genesis incomplete"
        exit 1
    fi
done

if [ "$FOUND_VERIFY" = false ]; then
    fail "No protocol contract addresses found in deployment.json"
    exit 1
fi

# Verify critical Uniswap V4 dependencies expected in genesis snapshot.
step "3d" "Verifying Uniswap V4 dependency contracts..."
for addr in \
    "0x000000000004444c5dc75cB358380D2e3dE08A90" \
    "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e" \
    "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203" \
    "0x7ffe42c4a5deea5b0fec41c94c136cf115597227" \
    "0xd1428ba554f4c8450b763a0b2040a4935c63f06c" \
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af" \
    "0x000000000022D473030F116dDEE9F6B43aC78BA3"; do
    CODE_LEN=$(cast code "$addr" --rpc-url "$RETH_RPC" 2>/dev/null | wc -c)
    if [ "$CODE_LEN" -gt 4 ]; then
        ok "Verified V4 dependency at $addr"
    else
        fail "Missing V4 dependency code at $addr — regenerate genesis with --fresh"
        exit 1
    fi
done

# ═════════════════════════════════════════════════════════════
# STEP 4: LAUNCH SERVICES
# ═════════════════════════════════════════════════════════════
CURRENT_PHASE="launch-services"
header "STEP 4: LAUNCH SERVICES"

BUILD_FLAG=""
[ "$NO_BUILD" = false ] && BUILD_FLAG="--build"

step "4a" "Starting indexer + postgres..."
docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" up -d $BUILD_FLAG postgres indexer 2>&1 | tail -5

step "4b" "Waiting for indexer health..."
INDEXER_URL="http://localhost:${INDEXER_PORT:-8080}"
STATUS="missing"
for i in $(seq 1 120); do
    INDEXER_CID="$(compose_cid "$COMPOSE_RETH" indexer)"
    if [ -n "$INDEXER_CID" ]; then
        STATUS=$(docker inspect --format '{{.State.Health.Status}}' "$INDEXER_CID" 2>/dev/null || echo "starting")
    fi
    if [ "$STATUS" = "healthy" ]; then
        ok "Indexer healthy (${i}s)"
        break
    fi
    [ $((i % 20)) -eq 0 ] && dim "Waiting for indexer... ($STATUS)"
    sleep 1
done
if [ "$STATUS" != "healthy" ]; then
    fail "Indexer failed health check (status=$STATUS)"
    docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" logs indexer --tail 50 2>&1 || true
    exit 1
fi

# Seed the indexer DB — this is what the deployer does in Anvil mode.
# The indexer reads deployment.json from its mounted /config/deployment.json
# and seeds the markets table so /config returns 200 (not 503).
step "4c" "Seeding indexer DB (POST /admin/reset)..."
RESET_HEADERS=()
if [ -n "${INDEXER_ADMIN_TOKEN:-}" ]; then
    RESET_HEADERS=(-H "X-Admin-Token: ${INDEXER_ADMIN_TOKEN}")
fi
RESET_STATUS="000"
for i in $(seq 1 30); do
    RESET_STATUS=$(curl -sf -X POST "${RESET_HEADERS[@]}" "$INDEXER_URL/admin/reset" -o /dev/null -w '%{http_code}' 2>/dev/null || echo "000")
    if [ "$RESET_STATUS" = "200" ]; then
        ok "Indexer DB seeded successfully"
        break
    fi
    [ $((i % 10)) -eq 0 ] && dim "Indexer reset returned $RESET_STATUS, retrying..."
    sleep 2
done
if [ "$RESET_STATUS" != "200" ]; then
    fail "Indexer reset failed after retries (status=$RESET_STATUS)"
    docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" logs indexer --tail 50 2>&1 || true
    exit 1
fi

# Verify /config now returns 200
step "4d" "Verifying /config endpoint..."
CONFIG_STATUS="000"
for i in $(seq 1 15); do
    CONFIG_STATUS=$(curl -sf -o /dev/null -w '%{http_code}' "$INDEXER_URL/config" 2>/dev/null || echo "000")
    if [ "$CONFIG_STATUS" = "200" ]; then
        ok "/config returns 200 — daemons will connect"
        break
    fi
    sleep 2
done
if [ "$CONFIG_STATUS" != "200" ]; then
    fail "/config did not become ready (status=$CONFIG_STATUS)"
    docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" logs indexer --tail 50 2>&1 || true
    exit 1
fi

# ── 4e. Seed initial pool state ──────────────────────────────
# Anvil deployment events (LP deposit, pool init) are lost in the genesis
# dump — only contract state was carried over, not event history. The
# indexer's carry-forward logic needs at least one block_states row with
# real token balances; otherwise TVL starts from 0 and goes negative.
step "4e" "Seeding initial pool state from on-chain data..."

POOL_MANAGER="0x000000000004444c5dc75cB358380D2e3dE08A90"
MARKET_ID=$(jq -r '.market_id' "$DEPLOY_JSON")
POOL_ID=$(jq -r '.pool_id' "$DEPLOY_JSON")
WAUSDC=$(jq -r '.wausdc' "$DEPLOY_JSON")
POS_TOKEN=$(jq -r '.position_token' "$DEPLOY_JSON")

if [ -n "$MARKET_ID" ] && [ "$MARKET_ID" != "null" ]; then
    # Read token balances in PoolManager
    T0_BAL=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$POOL_MANAGER" --rpc-url "$RETH_RPC" 2>/dev/null | awk '{print $1}')
    T1_BAL=$(cast call "$POS_TOKEN" "balanceOf(address)(uint256)" "$POOL_MANAGER" --rpc-url "$RETH_RPC" 2>/dev/null | awk '{print $1}')

    # Determine token order (token0 = lower address)
    WAUSDC_LOWER=$(echo "$WAUSDC" | tr '[:upper:]' '[:lower:]')
    POS_LOWER=$(echo "$POS_TOKEN" | tr '[:upper:]' '[:lower:]')
    if [[ "$WAUSDC_LOWER" < "$POS_LOWER" ]]; then
        TOKEN0_BAL="$T0_BAL"; TOKEN1_BAL="$T1_BAL"
    else
        TOKEN0_BAL="$T1_BAL"; TOKEN1_BAL="$T0_BAL"
    fi

    # Read pool Slot0 via extsload (V4 PoolManager uses extsload, not getters)
    # Pool mapping is at storage slot 6 in PoolManager
    SLOT0_KEY=$(cast keccak "$(cast abi-encode 'f(bytes32,uint256)' "$POOL_ID" 6)")
    SLOT0_RAW=$(cast call "$POOL_MANAGER" "extsload(bytes32)(bytes32)" "$SLOT0_KEY" --rpc-url "$RETH_RPC" 2>/dev/null)

    # Read liquidity from slot+3
    LIQ_SLOT=$(python3 -c "print(hex(int('${SLOT0_KEY}', 16) + 3))")
    LIQ_RAW=$(cast call "$POOL_MANAGER" "extsload(bytes32)(bytes32)" "$LIQ_SLOT" --rpc-url "$RETH_RPC" 2>/dev/null)

    # Decode all pool state with a single Python script
    POOL_STATE=$(python3 -c "
slot0 = int('${SLOT0_RAW}', 16)
liq = int('${LIQ_RAW}', 16)
t0 = int('${TOKEN0_BAL}')
t1 = int('${TOKEN1_BAL}')
wausdc_lower = '${WAUSDC_LOWER}'
pos_lower = '${POS_LOWER}'

# Decode packed Slot0: sqrtPriceX96 (160 bits) | tick (24) | protocolFee (24) | lpFee (24)
sqrt_price = slot0 & ((1 << 160) - 1)
tick_raw = (slot0 >> 160) & ((1 << 24) - 1)
tick = tick_raw if tick_raw < (1 << 23) else tick_raw - (1 << 24)

# Compute mark price
raw_price = (sqrt_price / (2**96))**2
if wausdc_lower < pos_lower:
    mark = 1.0/raw_price if raw_price > 0 else 0
else:
    mark = raw_price

# Token balances stay as raw integers (block_states stores raw 6-dec values;
# snapshot.py divides by 1e6 for display)
print(f'{sqrt_price}|{tick}|{mark}|{liq}|{t0}|{t1}')
" 2>/dev/null)

    SQRT_PRICE=$(echo "$POOL_STATE" | cut -d'|' -f1)
    TICK=$(echo "$POOL_STATE" | cut -d'|' -f2)
    MARK_PRICE=$(echo "$POOL_STATE" | cut -d'|' -f3)
    LIQUIDITY=$(echo "$POOL_STATE" | cut -d'|' -f4)
    T0_SCALED=$(echo "$POOL_STATE" | cut -d'|' -f5)
    T1_SCALED=$(echo "$POOL_STATE" | cut -d'|' -f6)
    PG_CID="$(compose_cid "$COMPOSE_RETH" postgres)"

    if [ -n "$T0_SCALED" ] && [ -n "$T1_SCALED" ] && [ -n "$SQRT_PRICE" ] && [ -n "$PG_CID" ]; then
        docker exec "$PG_CID" psql -U rld -d rld_indexer -c "
            INSERT INTO block_states
              (market_id, block_number, block_timestamp,
               sqrt_price_x96, tick, mark_price, liquidity,
               token0_balance, token1_balance,
               fee_growth_global0, fee_growth_global1,
               normalization_factor, total_debt)
            VALUES (
              '${MARKET_ID}', 0, 0,
              '${SQRT_PRICE}', ${TICK}, ${MARK_PRICE}, '${LIQUIDITY}',
              ${T0_SCALED}, ${T1_SCALED},
              '0', '0',
              1.0, 0)
            ON CONFLICT (market_id, block_number) DO UPDATE SET
              sqrt_price_x96 = EXCLUDED.sqrt_price_x96,
              tick           = EXCLUDED.tick,
              mark_price     = EXCLUDED.mark_price,
              liquidity      = EXCLUDED.liquidity,
              token0_balance = EXCLUDED.token0_balance,
              token1_balance = EXCLUDED.token1_balance,
              normalization_factor = EXCLUDED.normalization_factor;
        " > /dev/null 2>&1

        ok "Pool state seeded: token0=${T0_SCALED} token1=${T1_SCALED} tick=${TICK} mark=${MARK_PRICE}"
    else
        warn "Could not read on-chain pool state — TVL may be inaccurate"
    fi
else
    warn "No market_id in deployment.json — skipping pool state seed"
fi

# ── 4f. E2E protocol verification ─────────────────────────────
if [ "$SKIP_E2E" = true ]; then
    info "E2E protocol verification skipped (--skip-e2e)"
else
    step "4f" "Running full protocol e2e verification..."
    python3 "$SCRIPT_DIR/verify_protocol_e2e.py" \
        --rpc-url "$RETH_RPC" \
        --indexer-url "$INDEXER_URL" \
        --deployment-json "$DEPLOY_JSON"
fi

# ── 4f.1 Optional user setup (LP/MM/CHAOS) ───────────────────
if [ "$WITH_USERS" = true ]; then
    step "4f.1" "Running full simulation user setup (--with-users)..."
    python3 "$SCRIPT_DIR/setup_simulation.py"
else
    step "4f.1" "Ensuring SimFunder reserve for faucet..."
    python3 "$SCRIPT_DIR/setup_simulation.py" --sim-funder-only --prime-wausdc-reserve
    info "User setup skipped (use --with-users when you want LP/MM/CHAOS accounts)"
fi

# ── 4g. Start runtime services ────────────────────────────────
if [ "$WITH_BOTS" = true ]; then
    step "4g" "Starting mm-daemon + chaos-trader + faucet..."
    if ! ensure_envio_api_ready "$ACTIVE_RATE_API_URL" 30; then
        FALLBACK_RATE_API_URL="http://localhost:${RATES_API_PORT}"
        if [ "$ACTIVE_RATE_API_URL" != "$FALLBACK_RATE_API_URL" ] && ensure_envio_api_ready "$FALLBACK_RATE_API_URL" 30; then
            warn "Primary rates readiness unavailable at ${ACTIVE_RATE_API_URL}; falling back to ${FALLBACK_RATE_API_URL}"
            ACTIVE_RATE_API_URL="$FALLBACK_RATE_API_URL"
        else
            fail "Rates API readiness failed at ${ACTIVE_RATE_API_URL}/readyz — refusing to start bots"
            exit 1
        fi
    fi
    docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" up -d $BUILD_FLAG mm-daemon chaos-trader faucet 2>&1 | tail -5
    ok "Trading bots + faucet started"
else
    step "4g" "Starting faucet (bots deferred)..."
    docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" up -d $BUILD_FLAG faucet 2>&1 | tail -5
    ok "Faucet started"
    info "MM/Chaos bots skipped (use --with-bots when ready)"
fi

step "4h" "Waiting for runtime services readiness..."
wait_for_service_ready faucet 90 || exit 1
if [ "$WITH_BOTS" = true ]; then
    wait_for_service_ready mm-daemon 90 || exit 1
    wait_for_service_ready chaos-trader 90 || exit 1
fi

# ═════════════════════════════════════════════════════════════
# STATUS REPORT
# ═════════════════════════════════════════════════════════════
header "STATUS REPORT"

RETH_BLOCK=$(cast block-number --rpc-url "$RETH_RPC" 2>/dev/null || echo "?")

echo -e "${MAGENTA}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║           RLD SIMULATION — RETH MODE 🦀 (Docker)         ║${NC}"
echo -e "${MAGENTA}╠═══════════════════════════════════════════════════════════╣${NC}"

docker compose -f "$COMPOSE_RETH" --env-file "$ENV_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null | while IFS= read -r line; do
    echo "$line" | grep -q "NAME" && continue
    NAME=$(echo "$line" | awk '{print $1}')
    STATUS_TEXT=$(echo "$line" | cut -d' ' -f2-)
    ICON="✅"
    echo "$STATUS_TEXT" | grep -q "unhealthy\|Exited" && ICON="❌"
    printf "${MAGENTA}║${NC}  %-28s %s %-22s${MAGENTA}║${NC}\n" "$NAME" "$ICON" "$STATUS_TEXT"
done

echo -e "${MAGENTA}╠═══════════════════════════════════════════════════════════╣${NC}"
printf "${MAGENTA}║${NC}  %-8s  %-5s  %-36s${MAGENTA}║${NC}\n" "Block" "" "$RETH_BLOCK"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✅ All systems operational (Docker-managed, auto-restart enabled)${NC}"
echo ""
echo -e "${DIM}Commands:${NC}"
echo "  Logs:     docker compose -f $COMPOSE_RETH --env-file $ENV_FILE logs -f"
echo "  Reth log: docker compose -f $COMPOSE_RETH --env-file $ENV_FILE logs -f reth"
echo "  Stop:     docker compose -f $COMPOSE_RETH --env-file $ENV_FILE down"
echo "  Restart:  $0 --skip-genesis   (fast, reuses genesis)"
echo "  Rebuild:  $0 --fresh          (full fresh genesis)"
echo "  Anvil:    legacy path removed (use reth flows in docker/reth/)"
