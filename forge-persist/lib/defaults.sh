#!/bin/bash
# defaults.sh — Shared config, colors, helpers for forge-persist

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
DIM='\033[2m'
NC='\033[0m'

header()   { echo -e "\n${BLUE}═══ $1 ═══${NC}\n"; }
step()     { echo -e "${YELLOW}[$1] $2${NC}"; }
ok()       { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()     { echo -e "${RED}  ✗ $1${NC}"; }
warn()     { echo -e "${YELLOW}  ⚠ $1${NC}"; }
info()     { echo -e "${CYAN}  ℹ $1${NC}"; }

check_tool() {
    local name=$1
    local hint=${2:-""}
    if ! command -v "$name" &>/dev/null; then
        fail "$name not found"
        [ -n "$hint" ] && echo -e "     ${DIM}$hint${NC}"
        exit 1
    fi
}

show_status() {
    local DATA_DIR="${1:-.forge-persist}"
    local PIDFILE="$DATA_DIR/reth.pid"
    if [ -f "$PIDFILE" ]; then
        local PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            local RSS=$(ps -o rss= -p "$PID" 2>/dev/null | awk '{printf "%.0f", $1/1024}')
            echo -e "${GREEN}● forge-persist running${NC} (PID: $PID, RSS: ${RSS}MB)"
            local PORT=$(ss -tlnp 2>/dev/null | grep "$PID" | awk '{print $4}' | grep -oE '[0-9]+$' | head -1)
            [ -n "$PORT" ] && echo "  RPC: http://localhost:$PORT"
            local BLOCK=$(cast block-number --rpc-url "http://localhost:${PORT:-8545}" 2>/dev/null || echo "?")
            echo "  Block: $BLOCK"
        else
            echo -e "${RED}● forge-persist not running${NC} (stale PID file)"
            rm -f "$PIDFILE"
        fi
    else
        echo -e "${DIM}● forge-persist not running${NC}"
    fi
}

stop_node() {
    local DATA_DIR="${1:-.forge-persist}"
    local PIDFILE="$DATA_DIR/reth.pid"
    if [ -f "$PIDFILE" ]; then
        local PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null
            wait "$PID" 2>/dev/null || true
            echo -e "${GREEN}✓ Stopped (PID: $PID)${NC}"
        else
            echo "Not running (stale PID)"
        fi
        rm -f "$PIDFILE"
    else
        echo "Not running"
    fi
}
