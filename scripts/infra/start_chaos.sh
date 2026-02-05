#!/bin/bash
# Start chaos trader daemon
# Usage: start_chaos.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

log_step "1" "Starting chaos trader daemon..."

# Kill existing chaos daemon
pkill -f "chaos_daemon.py" 2>/dev/null || true
sleep 1

# Start chaos daemon
cd /home/ubuntu/RLD/backend/scripts
nohup python3 chaos_daemon.py > /tmp/chaos_trader.log 2>&1 &
CHAOS_PID=$!
sleep 2

if ps -p $CHAOS_PID > /dev/null 2>&1; then
    log_success "Chaos trader started (PID: $CHAOS_PID)"
    log_info "Log: /tmp/chaos_trader.log"
else
    log_error "Chaos trader failed to start"
    exit 1
fi
