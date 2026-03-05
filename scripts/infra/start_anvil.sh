#!/bin/bash
# Start Anvil fork
# Usage: start_anvil.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

# Load ETH_RPC_URL from contracts/.env (has Alchemy key)
source /home/ubuntu/RLD/contracts/.env 2>/dev/null || true

FORK_BLOCK=${FORK_BLOCK:-21698573}

if [ -z "$ETH_RPC_URL" ]; then
    log_error "ETH_RPC_URL not set - check contracts/.env"
fi

log_step "1" "Starting Anvil fork at block $FORK_BLOCK..."
log_info "RPC: ${ETH_RPC_URL:0:50}..."

anvil --fork-url "$ETH_RPC_URL" --fork-block-number $FORK_BLOCK \
    --no-rate-limit --block-time 12 \
    > /tmp/anvil.log 2>&1 &

echo $! > /tmp/anvil.pid
sleep 5

# Wait for RPC (increased timeout to 60s for slow forks)
log_step "2" "Waiting for RPC..."
for i in {1..60}; do
    if cast block-number --rpc-url "http://localhost:8545" > /dev/null 2>&1; then
        log_success "Anvil ready (PID: $(cat /tmp/anvil.pid))"
        exit 0
    fi
    sleep 1
done

log_error "Anvil failed to start - check /tmp/anvil.log"
