#!/bin/bash
# RLD System Orchestrator with New Indexer (SQLite mode)
# For systems without Docker/PostgreSQL - uses SQLite database
# Usage: ./scripts/orchestrator_new_indexer_lite.sh

set -e
RLD_ROOT="/home/ubuntu/RLD"
INDEXER_ROOT="/home/ubuntu/RLD-indexing/indexer"
cd "$RLD_ROOT"

source scripts/utils/colors.sh

log_header "RLD SYSTEM ORCHESTRATOR (Lite Mode - SQLite)"

# ═══════════════════════════════════════════════════════════════
# PHASE 1: CLEANUP & CHAIN
# ═══════════════════════════════════════════════════════════════
log_phase "1" "INFRASTRUCTURE"
scripts/infra/kill_all.sh

# Also stop any running indexer
pkill -f "python -m src.main" 2>/dev/null || true
pkill -f "run_comprehensive_indexer" 2>/dev/null || true

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
# PHASE 3: START INDEXER (Original SQLite-based)
# ═══════════════════════════════════════════════════════════════
log_phase "3" "START INDEXER (SQLite Mode)"

# Use the existing comprehensive indexer which uses SQLite
scripts/infra/start_indexer.sh &
sleep 2

log_success "Indexer started (SQLite mode)"
echo "   Log: /tmp/indexer.log"
echo "   DB:  backend/comprehensive_state.db"

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
log_header "SYSTEM READY (Lite Mode)"
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
echo "  Database: backend/comprehensive_state.db"
echo "  Config:   /home/ubuntu/RLD/.env"
echo ""
echo "  ⚠️  Note: Running in lite mode (SQLite). For PostgreSQL mode:"
echo "      Install Docker and run: ./scripts/orchestrator_new_indexer.sh"
echo ""

