#!/bin/bash
# RLD System Orchestrator
# One command to launch the entire simulation environment
# Usage: ./scripts/orchestrator.sh

set -e
RLD_ROOT="/home/ubuntu/RLD"
cd "$RLD_ROOT"

source scripts/utils/colors.sh

log_header "RLD SYSTEM ORCHESTRATOR"

# ═══════════════════════════════════════════════════════════════
# PHASE 1: CLEANUP & CHAIN
# ═══════════════════════════════════════════════════════════════
log_phase "1" "INFRASTRUCTURE"
scripts/infra/kill_all.sh
scripts/infra/start_anvil.sh

# ═══════════════════════════════════════════════════════════════
# PHASE 2: DEPLOY (Updates .env automatically)
# ═══════════════════════════════════════════════════════════════
log_phase "2" "DEPLOY PROTOCOL & MARKET"
scripts/infra/deploy_protocol.sh
scripts/infra/deploy_market.sh

# Reload .env with new addresses
source /home/ubuntu/RLD/.env

# ═══════════════════════════════════════════════════════════════
# PHASE 3: START INDEXER
# ═══════════════════════════════════════════════════════════════
log_phase "3" "START INDEXER"
scripts/infra/start_indexer.sh &
sleep 2

# ═══════════════════════════════════════════════════════════════
# PHASE 4: SETUP USERS
# ═══════════════════════════════════════════════════════════════
log_phase "4" "SETUP USERS"

# Reload .env after deploy
source /home/ubuntu/RLD/.env

# User A: LP Provider ($100M collateral, $5M LP)
scripts/scenarios/lp_provider.sh "$USER_A_KEY" 100000000 5000000

# Reload to get USER_A_BROKER
source /home/ubuntu/RLD/.env

# User B: Go Long ($100k)
scripts/scenarios/long_user.sh "$USER_B_KEY" 100000

# User C: TWAMM Order ($100k, 1 hour)
scripts/scenarios/twamm_user.sh "$USER_C_KEY" 100000 1

# MM Bot ($10M)
scripts/scenarios/mm_bot.sh "$MM_KEY" 10000000

# Chaos Trader ($10M)
scripts/scenarios/chaos_trader.sh "$CHAOS_KEY" 10000000

# ═══════════════════════════════════════════════════════════════
# PHASE 5: START DAEMONS
# ═══════════════════════════════════════════════════════════════
log_phase "5" "START DAEMONS"

# Reload to get all broker addresses
source /home/ubuntu/RLD/.env

scripts/infra/start_daemons.sh
scripts/infra/start_chaos.sh

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
echo ""
log_header "SYSTEM READY"
echo ""
echo "  📊 Indexer: Running (PID: $(cat /tmp/indexer.pid 2>/dev/null || echo 'N/A'))"
echo "  🤖 Daemon:  Running (PID: $(cat /tmp/daemon.pid 2>/dev/null || echo 'N/A'))"
echo "  🌀 Chaos:   Running (PID: $(pgrep -f chaos_daemon.py 2>/dev/null || echo 'N/A'))"
echo "  ⛓️  Anvil:   Running (PID: $(cat /tmp/anvil.pid 2>/dev/null || echo 'N/A'))"
echo ""
echo "  Logs:"
echo "    /tmp/anvil.log"
echo "    /tmp/indexer.log"
echo "    /tmp/daemon.log"
echo "    /tmp/chaos_trader.log"
echo ""
echo "  Config: /home/ubuntu/RLD/.env"
echo ""

