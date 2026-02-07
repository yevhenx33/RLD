#!/bin/bash
# Start comprehensive indexer
# Usage: start_indexer.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

log_step "1" "Starting comprehensive indexer..."

cd /home/ubuntu/RLD/backend

# Export required vars
export RPC_URL MARKET_ID MOCK_ORACLE
export USER_A_BROKER MM_BROKER CHAOS_BROKER

# Run indexer in background with --run for continuous mode
python3 tools/run_comprehensive_indexer.py --run > /tmp/indexer.log 2>&1 &
echo $! > /tmp/indexer.pid

log_success "Indexer started (PID: $(cat /tmp/indexer.pid))"
echo "   Log: /tmp/indexer.log"
echo "   DB:  backend/data/comprehensive_state.db"
