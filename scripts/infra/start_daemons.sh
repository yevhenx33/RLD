#!/bin/bash
# Start trading daemons
# Usage: start_daemons.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

log_step "1" "Starting combined daemon..."

cd /home/ubuntu/RLD/backend

# Export all required vars from .env
export RPC_URL MOCK_ORACLE_ADDR=$MOCK_ORACLE
export PRIVATE_KEY=$MM_KEY
export ORACLE_ADMIN_KEY=$DEPLOYER_KEY
export API_KEY API_URL
export WAUSDC POSITION_TOKEN TWAMM_HOOK MARKET_ID BROKER_FACTORY RLD_CORE

python3 services/combined_daemon.py > /tmp/daemon.log 2>&1 &
echo $! > /tmp/daemon.pid

log_success "Daemon started (PID: $(cat /tmp/daemon.pid))"
echo "   Log: /tmp/daemon.log"
