#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Anvil Rotate (Legacy Utility)
# ═══════════════════════════════════════════════════════════════
# Reth is the canonical V2 runtime. This script is kept only for
# optional legacy Anvil workflows and exits cleanly in Reth mode.
#
# Anvil fork instances leak memory over time (500MB+/hour) because
# every storage slot accessed from mainnet is cached in-process.
# This script:
#   1. Dumps Anvil state to disk
#   2. Kills the old (bloated) process
#   3. Restarts from the state dump with a fresh, lean process
#
# Run via cron every 12 hours:
#   0 */12 * * * /home/ubuntu/RLD/docker/scripts/anvil-rotate.sh >> /tmp/anvil-rotate.log 2>&1
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$DOCKER_DIR/.env"

STATE_DIR="/tmp/anvil-state"
STATE_FILE="$STATE_DIR/state.json"
STATE_BACKUP="$STATE_DIR/state.backup.json"
ANVIL_LOG="/tmp/anvil.log"
ANVIL_HOST="0.0.0.0"
ANVIL_PORT=8545
ANVIL_RPC="http://localhost:$ANVIL_PORT"

# Max memory in KB before forcing a rotation (12GB)
MAX_RSS_KB=12582912

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# Skip in Reth-only runtime (launch baseline).
if pgrep -x reth >/dev/null 2>&1 || docker ps --filter "label=com.docker.compose.service=reth" --format '{{.Names}}' | grep -q .; then
    log "INFO: Reth runtime detected. Skipping legacy Anvil rotation."
    exit 0
fi

# If Anvil is not installed, do not fail cron loops.
if ! command -v anvil >/dev/null 2>&1; then
    log "INFO: Anvil binary not found. Skipping legacy rotation."
    exit 0
fi

# ── Load env ──────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

# ── Check if Anvil is running ────────────────────────────────
ANVIL_PID=$(pgrep -f "anvil.*--host" || true)
if [[ -z "$ANVIL_PID" ]]; then
    log "WARN: Anvil not running. Starting fresh..."
    
    # Try to load from previous state dump if available
    if [[ -f "$STATE_FILE" ]]; then
        log "Found state dump at $STATE_FILE, loading..."
        nohup anvil \
            --load-state "$STATE_FILE" \
            --chain-id 31337 \
            --block-time 12 \
            --host "$ANVIL_HOST" \
            > "$ANVIL_LOG" 2>&1 &
    elif [[ -n "${MAINNET_RPC_URL:-}" ]] && [[ -n "${FORK_BLOCK:-}" ]]; then
        log "No state dump. Starting fresh fork from block $FORK_BLOCK..."
        nohup anvil \
            --fork-url "$MAINNET_RPC_URL" \
            --fork-block-number "$FORK_BLOCK" \
            --chain-id 31337 \
            --block-time 12 \
            --host "$ANVIL_HOST" \
            > "$ANVIL_LOG" 2>&1 &
    else
        log "ERROR: No state dump and no MAINNET_RPC_URL/FORK_BLOCK in env"
        exit 1
    fi
    
    sleep 5
    NEW_PID=$(pgrep -f "anvil.*--host" || true)
    log "Anvil started (PID: $NEW_PID)"
    exit 0
fi

# ── Check memory usage ──────────────────────────────────────
RSS_KB=$(ps -o rss= -p "$ANVIL_PID" 2>/dev/null | tr -d ' ')
RSS_MB=$((RSS_KB / 1024))
log "Anvil PID=$ANVIL_PID RSS=${RSS_MB}MB (threshold: $((MAX_RSS_KB / 1024))MB)"

# Only rotate if above threshold (unless --force flag)
if [[ "${1:-}" != "--force" ]] && [[ "$RSS_KB" -lt "$MAX_RSS_KB" ]]; then
    log "Memory OK (${RSS_MB}MB < $((MAX_RSS_KB / 1024))MB). Skipping rotation."
    exit 0
fi

log "Memory threshold exceeded or --force. Rotating..."

# ── Step 1: Dump state ────────────────────────────────────────
mkdir -p "$STATE_DIR"

# Backup previous state
if [[ -f "$STATE_FILE" ]]; then
    cp "$STATE_FILE" "$STATE_BACKUP"
    log "Backed up previous state to $STATE_BACKUP"
fi

log "Dumping state via anvil_dumpState RPC..."
DUMP_RESULT=$(curl -s -X POST "$ANVIL_RPC" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"anvil_dumpState","params":[],"id":1}' \
    --max-time 120)

if echo "$DUMP_RESULT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if 'result' in data and data['result']:
    # Result is hex-encoded state
    state_hex = data['result']
    if state_hex.startswith('0x'):
        state_hex = state_hex[2:]
    state_bytes = bytes.fromhex(state_hex)
    with open('$STATE_FILE', 'wb') as f:
        f.write(state_bytes)
    print(f'State dumped: {len(state_bytes)} bytes')
    sys.exit(0)
else:
    print(f'No result in response: {json.dumps(data)[:200]}')
    sys.exit(1)
" 2>&1; then
    STATE_SIZE=$(du -sh "$STATE_FILE" 2>/dev/null | cut -f1)
    log "State dump successful ($STATE_SIZE)"
else
    log "ERROR: State dump failed. Aborting rotation to preserve running instance."
    exit 1
fi

# ── Step 2: Record current block number ───────────────────────
BLOCK_NUM=$(curl -s -X POST "$ANVIL_RPC" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
    | python3 -c "import sys,json; print(int(json.load(sys.stdin)['result'], 16))" 2>/dev/null || echo "unknown")
log "Current block: $BLOCK_NUM"

# ── Step 3: Kill old Anvil ────────────────────────────────────
log "Killing Anvil (PID: $ANVIL_PID, RSS: ${RSS_MB}MB)..."
kill "$ANVIL_PID"
sleep 3

# Verify it's dead
if kill -0 "$ANVIL_PID" 2>/dev/null; then
    log "WARN: Anvil didn't exit gracefully, sending SIGKILL..."
    kill -9 "$ANVIL_PID" 2>/dev/null || true
    sleep 2
fi

# ── Step 4: Restart from state dump ───────────────────────────
log "Restarting Anvil from state dump..."
nohup anvil \
    --load-state "$STATE_FILE" \
    --chain-id 31337 \
    --block-time 12 \
    --host "$ANVIL_HOST" \
    > "$ANVIL_LOG" 2>&1 &

# Wait for it to come up
for i in $(seq 1 30); do
    if curl -s -X POST "$ANVIL_RPC" \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
        --max-time 2 | grep -q result; then
        break
    fi
    sleep 1
done

NEW_PID=$(pgrep -f "anvil.*--host" || echo "FAILED")
NEW_RSS=$(ps -o rss= -p "$NEW_PID" 2>/dev/null | tr -d ' ' || echo "0")
NEW_RSS_MB=$((NEW_RSS / 1024))
NEW_BLOCK=$(curl -s -X POST "$ANVIL_RPC" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
    | python3 -c "import sys,json; print(int(json.load(sys.stdin)['result'], 16))" 2>/dev/null || echo "unknown")

log "✓ Rotation complete"
log "  Old: PID=$ANVIL_PID RSS=${RSS_MB}MB Block=$BLOCK_NUM"
log "  New: PID=$NEW_PID RSS=${NEW_RSS_MB}MB Block=$NEW_BLOCK"
log "  Memory freed: $((RSS_MB - NEW_RSS_MB))MB"
