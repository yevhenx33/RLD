#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Anvil Hourly Restart — Memory Management (Safe 2-File Rotation)
# ═══════════════════════════════════════════════════════════════
# Anvil accumulates block history in RAM (~1-2GB/hour with block-time 1).
# This script safely rotates the state dump then restarts anvil.
#
# Strategy: 2-file rotation to prevent corruption
#   1. Copy current state.json → state-backup.json (atomic)
#   2. Kill anvil (state.json may be corrupted mid-write)
#   3. If state.json is valid → use it; else fallback to state-backup.json
#   4. Restart anvil from the valid state file
#
# Install: crontab -e → 0 * * * * /home/ubuntu/RLD/docker/anvil-restart.sh
# ═══════════════════════════════════════════════════════════════

set -uo pipefail

LOG="/tmp/anvil-restart.log"
STATE_DIR="/tmp/anvil-state"
STATE_FILE="$STATE_DIR/state.json"
BACKUP_FILE="$STATE_DIR/state-backup.json"
ANVIL_BIN="/home/ubuntu/.foundry/bin/anvil"
FORK_URL="https://eth-mainnet.g.alchemy.com/v2/***REDACTED_ALCHEMY***"
FORK_BLOCK=21698573

log() { echo "[$(date)] $1" >> "$LOG"; }

log "── Anvil restart triggered ──"

# 1. Validate anvil binary exists
if [ ! -x "$ANVIL_BIN" ]; then
    log "ERROR: Anvil binary not found at $ANVIL_BIN"
    exit 1
fi

mkdir -p "$STATE_DIR"

# 2. Take a safe backup BEFORE killing anvil
#    cp creates an atomic snapshot of the current (valid) state
if [ -f "$STATE_FILE" ]; then
    cp "$STATE_FILE" "$BACKUP_FILE"
    log "Backed up state.json → state-backup.json ($(stat -c%s "$BACKUP_FILE" 2>/dev/null || echo '?') bytes)"
fi

# 3. Log memory before
ANVIL_PID=$(pgrep -f 'anvil --fork' || true)
if [ -n "$ANVIL_PID" ]; then
    RSS_BEFORE=$(ps -o rss= -p "$ANVIL_PID" 2>/dev/null || echo 0)
    log "Memory before: $((RSS_BEFORE / 1024))MB (PID $ANVIL_PID)"
else
    RSS_BEFORE=0
    log "No running anvil found"
fi

# 4. Kill anvil (state.json may be corrupted by this)
if [ -n "$ANVIL_PID" ]; then
    kill "$ANVIL_PID" 2>/dev/null || true
    sleep 2
    kill -9 "$ANVIL_PID" 2>/dev/null || true
    sleep 1
fi

# 5. Drop filesystem caches
sync
echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true

# 6. Determine which state file to load
#    Try state.json first; if it's corrupt, use the backup
LOAD_STATE=""
if [ -f "$STATE_FILE" ] && python3 -c "import json; json.load(open('$STATE_FILE'))" 2>/dev/null; then
    LOAD_STATE="$STATE_FILE"
    log "Using state.json (valid)"
elif [ -f "$BACKUP_FILE" ] && python3 -c "import json; json.load(open('$BACKUP_FILE'))" 2>/dev/null; then
    LOAD_STATE="$BACKUP_FILE"
    log "WARNING: state.json corrupted, falling back to state-backup.json"
else
    log "WARNING: No valid state file found — starting fresh fork"
fi

# 7. Restart anvil
rm -f /tmp/anvil.log 2>/dev/null || true
touch /tmp/anvil.log
chmod 666 /tmp/anvil.log 2>/dev/null || true

LOAD_FLAG=""
if [ -n "$LOAD_STATE" ]; then
    LOAD_FLAG="--load-state $LOAD_STATE"
fi

nohup "$ANVIL_BIN" \
    --fork-url "$FORK_URL" \
    --fork-block-number "$FORK_BLOCK" \
    --chain-id 31337 \
    --block-time 12 \
    --host 0.0.0.0 \
    $LOAD_FLAG \
    --dump-state "$STATE_FILE" \
    >> /tmp/anvil.log 2>&1 &

NEW_PID=$!
log "Anvil restarted (PID=$NEW_PID, block-time=1, load=${LOAD_STATE:-none})"

# 8. Wait for anvil to be ready
READY=false
for i in $(seq 1 20); do
    if curl -s -X POST http://127.0.0.1:8545 \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","id":1}' \
        | grep -q result; then
        log "Anvil ready after ${i}s"
        READY=true
        break
    fi
    sleep 1
done

if [ "$READY" = false ]; then
    log "ERROR: Anvil failed to start — check /tmp/anvil.log"
    tail -5 /tmp/anvil.log >> "$LOG" 2>/dev/null
    exit 1
fi

# 9. Re-enforce chain ID (fork inherits mainnet chain ID 1)
curl -s -X POST http://127.0.0.1:8545 \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"anvil_setChainId","params":[31337],"id":1}' \
    > /dev/null 2>&1

# 10. Log memory after
RSS_AFTER=$(ps -o rss= -p "$NEW_PID" 2>/dev/null || echo 0)
SAVED=$(( (RSS_BEFORE - RSS_AFTER) / 1024 ))
log "Memory after: $((RSS_AFTER / 1024))MB (saved ${SAVED}MB)"
log "── Done ──"
