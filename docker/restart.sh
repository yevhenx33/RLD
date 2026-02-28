#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# RLD Simulation — Full Stack Restart
# ═══════════════════════════════════════════════════════════════
# Cleanly tears down EVERYTHING (Anvil + all containers),
# restarts from a clean fork, and waits for full health.
#
# Usage:
#   ./docker/restart.sh              # Full restart (default)
#   ./docker/restart.sh --sim-only   # Only restart simulation stack (keep rates + bot)
#   ./docker/restart.sh --no-build   # Skip Docker image rebuilds
#   ./docker/restart.sh --keep-data  # Keep indexer data volume
#
# Requirements:
#   - docker, docker compose, anvil, cast must be in PATH
#   - docker/.env must exist with MAINNET_RPC_URL set
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Configuration ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RLD_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$SCRIPT_DIR"

COMPOSE_MAIN="$DOCKER_DIR/docker-compose.yml"
COMPOSE_RATES="$DOCKER_DIR/docker-compose.rates.yml"
COMPOSE_BOT="$DOCKER_DIR/docker-compose.bot.yml"
ENV_FILE="$DOCKER_DIR/.env"

ANVIL_LOG="/tmp/anvil.log"
ANVIL_HOST="0.0.0.0"
ANVIL_PORT=8545
ANVIL_RPC="http://localhost:$ANVIL_PORT"

# Timeouts
ANVIL_TIMEOUT=60
DEPLOYER_TIMEOUT=600     # 10 minutes — deployment takes ~5-8 min
HEALTH_TIMEOUT=120       # 2 minutes for containers to become healthy

# ─── Parse args ───────────────────────────────────────────────
SIM_ONLY=false
NO_BUILD=false
KEEP_DATA=false

for arg in "$@"; do
    case "$arg" in
        --sim-only)  SIM_ONLY=true ;;
        --no-build)  NO_BUILD=true ;;
        --keep-data) KEEP_DATA=true ;;
        --help|-h)
            echo "Usage: $0 [--sim-only] [--no-build] [--keep-data]"
            echo ""
            echo "  --sim-only   Only restart sim stack (keep rates-indexer + telegram bot)"
            echo "  --no-build   Skip Docker image rebuilds (faster if no code changes)"
            echo "  --keep-data  Preserve indexer data volume across restart"
            exit 0
            ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

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

# ─── Preflight checks ────────────────────────────────────────
header "PREFLIGHT CHECKS"

# Check required tools
for cmd in docker anvil cast; do
    if ! command -v "$cmd" &>/dev/null; then
        fail "$cmd not found in PATH"
        exit 1
    fi
done
ok "Required tools found (docker, anvil, cast)"

# Check docker compose (v2)
if ! docker compose version &>/dev/null; then
    fail "docker compose v2 not available"
    exit 1
fi
ok "Docker compose v2 available"

# Check .env file
if [ ! -f "$ENV_FILE" ]; then
    fail "$ENV_FILE not found — run: cp $DOCKER_DIR/.env.example $DOCKER_DIR/.env"
    exit 1
fi

# Load config from env files
source <(grep -E '^(MAINNET_RPC_URL|FORK_BLOCK|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|RATES_PORT|BOT_PORT|INDEXER_PORT|API_KEY)=' "$ENV_FILE" | sed 's/^/export /')

# Also load FORK_BLOCK from root .env if not in docker/.env
if [ -z "${FORK_BLOCK:-}" ] && [ -f "$RLD_ROOT/.env" ]; then
    FORK_BLOCK=$(grep -E '^FORK_BLOCK=' "$RLD_ROOT/.env" 2>/dev/null | cut -d= -f2 || echo "")
fi
FORK_BLOCK="${FORK_BLOCK:-21698573}"

if [ -z "${MAINNET_RPC_URL:-}" ]; then
    fail "MAINNET_RPC_URL not set in $ENV_FILE"
    exit 1
fi
ok "Environment loaded (fork block: $FORK_BLOCK)"

# Check if port 8545 is available (or already Anvil)
ANVIL_PID=$(pgrep -f "anvil.*--host" || true)
if [ -n "$ANVIL_PID" ]; then
    info "Existing Anvil process found (PID: $ANVIL_PID) — will be killed"
fi

# ═════════════════════════════════════════════════════════════
# STEP 1: Tear down everything
# ═════════════════════════════════════════════════════════════
header "STEP 1: TEAR DOWN"

# 1a. Stop main simulation stack
step "1a" "Stopping simulation stack..."
DOWN_FLAGS=""
if [ "$KEEP_DATA" = false ]; then
    DOWN_FLAGS="-v"
fi
docker compose -f "$COMPOSE_MAIN" --env-file "$ENV_FILE" down $DOWN_FLAGS 2>/dev/null || true
ok "Simulation stack stopped"

# 1b. Stop rates + bot if full restart
if [ "$SIM_ONLY" = false ]; then
    step "1b" "Stopping rates-indexer and bot..."
    docker compose -f "$COMPOSE_RATES" --env-file "$ENV_FILE" down 2>/dev/null || true
    docker compose -f "$COMPOSE_BOT" --env-file "$ENV_FILE" down 2>/dev/null || true
    ok "Rates-indexer and bot stopped"
else
    info "Skipping rates/bot (--sim-only mode)"
fi

# 1c. Kill any orphaned standalone containers that may hold ports
step "1c" "Cleaning orphaned containers..."
ORPHANS=("rld-indexer-fixed")
for c in "${ORPHANS[@]}"; do
    if docker ps -a --format '{{.Names}}' | grep -q "^${c}$"; then
        docker stop "$c" 2>/dev/null || true
        docker rm "$c" 2>/dev/null || true
        ok "Removed orphan: $c"
    fi
done

# 1d. Prune dangling images
step "1d" "Pruning dangling Docker images..."
PRUNED=$(docker image prune -f 2>/dev/null | tail -1)
ok "$PRUNED"

# 1e. Clean old Anvil state snapshots (keeps only the most recent)
step "1e" "Cleaning old Anvil state snapshots..."
CLEANUP_SCRIPT="$SCRIPT_DIR/scripts/cleanup-anvil-snapshots.sh"
if [ -x "$CLEANUP_SCRIPT" ]; then
    "$CLEANUP_SCRIPT" --force 2>&1 | sed 's/^/    /'
else
    ANVIL_TMP="$HOME/.foundry/anvil/tmp"
    if [ -d "$ANVIL_TMP" ]; then
        SNAPSHOT_COUNT=$(find "$ANVIL_TMP" -maxdepth 1 -name 'anvil-state-*' -type d | wc -l)
        if [ "$SNAPSHOT_COUNT" -gt 1 ]; then
            LATEST=$(ls -dt "$ANVIL_TMP"/anvil-state-* | head -1)
            ls -dt "$ANVIL_TMP"/anvil-state-* | tail -n +2 | xargs rm -rf
            ok "Removed $((SNAPSHOT_COUNT - 1)) old snapshot(s), kept $(basename "$LATEST")"
        else
            ok "No old snapshots to clean"
        fi
    fi
fi

# 1f. Verify ports are free
step "1f" "Checking port availability..."
PORTS_TO_CHECK=("$ANVIL_PORT:Anvil")
PORTS_TO_CHECK+=("${INDEXER_PORT:-8080}:Indexer")
if [ "$SIM_ONLY" = false ]; then
    PORTS_TO_CHECK+=("${RATES_PORT:-8081}:Rates")
    PORTS_TO_CHECK+=("${BOT_PORT:-8082}:Bot")
fi

ALL_PORTS_FREE=true
for port_entry in "${PORTS_TO_CHECK[@]}"; do
    PORT="${port_entry%%:*}"
    NAME="${port_entry##*:}"
    # Skip Anvil port since we're about to start it
    if [ "$PORT" = "$ANVIL_PORT" ]; then continue; fi
    if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
        PID=$(ss -tlnp 2>/dev/null | grep ":${PORT} " | grep -oP 'pid=\K\d+' | head -1)
        PROC=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
        fail "Port $PORT ($NAME) is in use by $PROC (PID $PID)"
        ALL_PORTS_FREE=false
    fi
done
if [ "$ALL_PORTS_FREE" = true ]; then
    ok "All required ports are free"
fi

# ═════════════════════════════════════════════════════════════
# STEP 2: Start Anvil
# ═════════════════════════════════════════════════════════════
header "STEP 2: START ANVIL"

step "2a" "Killing existing Anvil process..."
pkill -f "anvil" 2>/dev/null || true
sleep 2
ok "Anvil processes killed"

step "2b" "Starting Anvil (fork block $FORK_BLOCK)..."
dim "Fork URL: ${MAINNET_RPC_URL%%\?*}..."
dim "Log file: $ANVIL_LOG"

nohup anvil \
    --fork-url "$MAINNET_RPC_URL" \
    --fork-block-number "$FORK_BLOCK" \
    --chain-id 31337 \
    --block-time 1 \
    --host "$ANVIL_HOST" \
    > "$ANVIL_LOG" 2>&1 &

ANVIL_PID=$!
dim "PID: $ANVIL_PID"

# Wait for Anvil to be ready
step "2c" "Waiting for Anvil to respond..."
for i in $(seq 1 "$ANVIL_TIMEOUT"); do
    if cast block-number --rpc-url "$ANVIL_RPC" > /dev/null 2>&1; then
        BLOCK=$(cast block-number --rpc-url "$ANVIL_RPC")
        ok "Anvil ready at block $BLOCK (took ${i}s)"
        break
    fi
    if [ $((i % 10)) -eq 0 ]; then
        dim "Still waiting... (${i}/${ANVIL_TIMEOUT}s)"
    fi
    sleep 1
done

# Verify it's actually working
if ! cast block-number --rpc-url "$ANVIL_RPC" > /dev/null 2>&1; then
    fail "Anvil failed to start after ${ANVIL_TIMEOUT}s"
    echo ""
    echo "Last 20 lines of $ANVIL_LOG:"
    tail -20 "$ANVIL_LOG" 2>/dev/null || true
    exit 1
fi

# Quick RPC test — verify the fork URL is actually working (catches 403s)
step "2d" "Verifying upstream RPC access..."
BALANCE_CHECK=$(cast balance 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 --rpc-url "$ANVIL_RPC" 2>&1) || true
if echo "$BALANCE_CHECK" | grep -qi "403\|forbidden\|whitelist\|error"; then
    fail "Upstream RPC returned an error — check MAINNET_RPC_URL in $ENV_FILE"
    echo "  Response: $BALANCE_CHECK"
    exit 1
fi
ok "Upstream RPC is accessible"

# Force chain ID to 31337 (Anvil fork inherits mainnet chain ID 1, which confuses MetaMask)
step "2e" "Setting chain ID to 31337..."
cast rpc anvil_setChainId 31337 --rpc-url "$ANVIL_RPC" > /dev/null 2>&1
ok "Chain ID set to 31337"

# ═════════════════════════════════════════════════════════════
# STEP 3: Start support services (if full restart)
# ═════════════════════════════════════════════════════════════
if [ "$SIM_ONLY" = false ]; then
    header "STEP 3: START SUPPORT SERVICES"

    step "3a" "Starting rates-indexer..."
    docker compose -f "$COMPOSE_RATES" --env-file "$ENV_FILE" up -d --build 2>&1 | tail -3
    ok "Rates-indexer started"

    step "3b" "Starting Telegram bot..."
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
        docker compose -f "$COMPOSE_BOT" --env-file "$ENV_FILE" up -d --build 2>&1 | tail -3
        ok "Telegram bot started"
    else
        warn "TELEGRAM_BOT_TOKEN not set — skipping bot"
    fi

    # Wait for rates-indexer to be healthy (deployer depends on it)
    step "3c" "Waiting for rates-indexer health..."
    for i in $(seq 1 60); do
        if curl -sf http://localhost:${RATES_PORT:-8081}/ > /dev/null 2>&1; then
            ok "Rates-indexer healthy (took ${i}s)"
            break
        fi
        sleep 2
    done
    if ! curl -sf http://localhost:${RATES_PORT:-8081}/ > /dev/null 2>&1; then
        warn "Rates-indexer not responding — deployer will use fallback rate"
    fi
else
    header "STEP 3: SUPPORT SERVICES (SKIPPED)"
    info "Rates-indexer and bot kept running (--sim-only mode)"
fi

# ═════════════════════════════════════════════════════════════
# STEP 4: Launch simulation stack
# ═════════════════════════════════════════════════════════════
header "STEP 4: DEPLOY & LAUNCH SIMULATION"

BUILD_FLAG=""
if [ "$NO_BUILD" = false ]; then
    BUILD_FLAG="--build"
fi

step "4a" "Starting docker compose (deployer runs first)..."
docker compose -f "$COMPOSE_MAIN" --env-file "$ENV_FILE" up -d $BUILD_FLAG 2>&1 | tail -5

# Wait for deployer to complete
step "4b" "Waiting for deployer to finish (timeout: ${DEPLOYER_TIMEOUT}s)..."
dim "This deploys protocol, market, users, and router (~5-8 min)"

DEPLOYER_STARTED=$(date +%s)
LAST_LOG=""
while true; do
    ELAPSED=$(( $(date +%s) - DEPLOYER_STARTED ))

    # Check if deployer has exited
    DEPLOYER_STATUS=$(docker inspect --format '{{.State.Status}}' docker-deployer-1 2>/dev/null || echo "missing")

    if [ "$DEPLOYER_STATUS" = "exited" ]; then
        EXIT_CODE=$(docker inspect --format '{{.State.ExitCode}}' docker-deployer-1 2>/dev/null || echo "?")
        if [ "$EXIT_CODE" = "0" ]; then
            ok "Deployer completed successfully (took ${ELAPSED}s)"
            # Re-enforce chain ID (deployer's forge scripts may reset to fork's mainnet chain 1)
            cast rpc anvil_setChainId 31337 --rpc-url "$ANVIL_RPC" > /dev/null 2>&1
            break
        else
            fail "Deployer exited with code $EXIT_CODE after ${ELAPSED}s"
            echo ""
            echo "Last 30 lines of deployer logs:"
            docker logs docker-deployer-1 --tail 30 2>&1
            echo ""
            fail "Fix the issue and re-run this script"
            exit 1
        fi
    fi

    if [ "$DEPLOYER_STATUS" = "missing" ]; then
        fail "Deployer container not found — compose may have failed"
        exit 1
    fi

    if [ "$ELAPSED" -ge "$DEPLOYER_TIMEOUT" ]; then
        fail "Deployer timed out after ${DEPLOYER_TIMEOUT}s"
        echo "Last 20 lines:"
        docker logs docker-deployer-1 --tail 20 2>&1
        exit 1
    fi

    # Show progress every 30s
    if [ $((ELAPSED % 30)) -eq 0 ] && [ "$ELAPSED" -gt 0 ]; then
        CURRENT_LOG=$(docker logs docker-deployer-1 --tail 1 2>/dev/null | head -1 || echo "")
        if [ "$CURRENT_LOG" != "$LAST_LOG" ] && [ -n "$CURRENT_LOG" ]; then
            dim "[${ELAPSED}s] $CURRENT_LOG"
            LAST_LOG="$CURRENT_LOG"
        else
            dim "[${ELAPSED}s] Still running..."
        fi
    fi

    sleep 5
done

# ═════════════════════════════════════════════════════════════
# STEP 5: Verify dependent containers started
# ═════════════════════════════════════════════════════════════
header "STEP 5: VERIFY ALL CONTAINERS"

step "5a" "Checking container statuses..."
EXPECTED_RUNNING=("docker-indexer-1" "docker-mm-daemon-1" "docker-chaos-trader-1")
ALL_RUNNING=true

for container in "${EXPECTED_RUNNING[@]}"; do
    STATUS=$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || echo "missing")
    if [ "$STATUS" = "running" ]; then
        ok "$container → running"
    elif [ "$STATUS" = "created" ]; then
        warn "$container stuck in 'created' — forcing start..."
        docker start "$container" 2>/dev/null || true
        sleep 2
        STATUS=$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null || echo "missing")
        if [ "$STATUS" = "running" ]; then
            ok "$container → started successfully"
        else
            fail "$container → failed to start ($STATUS)"
            docker logs "$container" --tail 5 2>&1
            ALL_RUNNING=false
        fi
    else
        fail "$container → $STATUS"
        ALL_RUNNING=false
    fi
done

# Wait for indexer to become healthy
step "5b" "Waiting for indexer health check..."
for i in $(seq 1 "$HEALTH_TIMEOUT"); do
    if curl -sf http://localhost:${INDEXER_PORT:-8080}/ > /dev/null 2>&1; then
        ok "Indexer API healthy (took ${i}s)"
        break
    fi
    if [ $((i % 15)) -eq 0 ]; then
        dim "Still waiting... (${i}/${HEALTH_TIMEOUT}s)"
    fi
    sleep 1
done
if ! curl -sf http://localhost:${INDEXER_PORT:-8080}/ > /dev/null 2>&1; then
    warn "Indexer not responding yet — may still be initializing"
fi

# Quick daemon sanity check — look for errors in last few log lines
step "5c" "Daemon health check..."
sleep 10  # Give daemons a few cycles

for container in docker-mm-daemon-1 docker-chaos-trader-1; do
    RECENT=$(docker logs "$container" --tail 5 2>&1 || echo "no logs")
    if echo "$RECENT" | grep -qi "error\|traceback\|403\|failed"; then
        warn "$container has recent errors:"
        echo "$RECENT" | head -3 | sed 's/^/    /'
    elif echo "$RECENT" | grep -qi "successful\|Index=\|Status"; then
        ok "$container looks healthy"
    else
        info "$container — waiting for first activity"
    fi
done

# ═════════════════════════════════════════════════════════════
# STEP 6: Final status report
# ═════════════════════════════════════════════════════════════
header "STATUS REPORT"

echo -e "${MAGENTA}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║           RLD SIMULATION STACK — STATUS                  ║${NC}"
echo -e "${MAGENTA}╠═══════════════════════════════════════════════════════════╣${NC}"

# Anvil
ANVIL_BLOCK=$(cast block-number --rpc-url "$ANVIL_RPC" 2>/dev/null || echo "?")
ANVIL_PID_NOW=$(pgrep -f "anvil.*--host" || echo "?")
printf "${MAGENTA}║${NC}  %-12s  %-10s  %-28s ${MAGENTA}║${NC}\n" "Anvil" "✅ UP" "Block: $ANVIL_BLOCK (PID: $ANVIL_PID_NOW)"

# Docker containers
echo -e "${MAGENTA}╠═══════════════════════════════════════════════════════════╣${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}" 2>/dev/null | while IFS= read -r line; do
    if echo "$line" | grep -q "NAMES"; then continue; fi
    NAME=$(echo "$line" | awk '{print $1}')
    STATUS_TEXT=$(echo "$line" | cut -d' ' -f2-)
    ICON="✅"
    echo "$STATUS_TEXT" | grep -q "unhealthy\|Exited" && ICON="❌"
    printf "${MAGENTA}║${NC}  %-28s %s %-22s ${MAGENTA}║${NC}\n" "$NAME" "$ICON" "$STATUS_TEXT"
done

echo -e "${MAGENTA}╠═══════════════════════════════════════════════════════════╣${NC}"

# Ports
printf "${MAGENTA}║${NC}  %-12s  %-42s ${MAGENTA}║${NC}\n" "Ports:" "Anvil=:$ANVIL_PORT  Indexer=:${INDEXER_PORT:-8080}  Rates=:${RATES_PORT:-8081}"
printf "${MAGENTA}║${NC}  %-12s  %-42s ${MAGENTA}║${NC}\n" "" "Bot=:${BOT_PORT:-8082}"

echo -e "${MAGENTA}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${DIM}Useful commands:${NC}"
echo "  Logs:    docker compose -f $COMPOSE_MAIN logs -f"
echo "  Status:  docker compose -f $COMPOSE_MAIN ps -a"
echo "  Stop:    docker compose -f $COMPOSE_MAIN down -v"
echo "  Anvil:   tail -f $ANVIL_LOG"
echo ""

# ═════════════════════════════════════════════════════════════
# STEP 7: Ensure cron job for status.json generation
# ═════════════════════════════════════════════════════════════
header "STEP 7: CRON SETUP"

STATUS_SCRIPT="$SCRIPT_DIR/scripts/generate-status.sh"
CRON_LINE="* * * * * $STATUS_SCRIPT >/dev/null 2>&1"

if sudo crontab -l 2>/dev/null | grep -qF "generate-status.sh"; then
    ok "Cron job already exists for generate-status.sh"
else
    step "7a" "Installing cron job for status.json generation..."
    (sudo crontab -l 2>/dev/null; echo "$CRON_LINE") | sudo crontab -
    ok "Cron job installed: generate-status.sh (every minute)"
fi

echo ""
if [ "$ALL_RUNNING" = true ]; then
    echo -e "${GREEN}✅ All systems operational!${NC}"
else
    echo -e "${YELLOW}⚠️  Some services had issues — check logs above.${NC}"
    exit 1
fi
