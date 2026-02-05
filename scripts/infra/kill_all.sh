#!/bin/bash
# Kill all RLD-related processes
# Usage: kill_all.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/colors.sh"

log_step "1" "Killing existing processes..."

pkill -f "anvil" 2>/dev/null || true
pkill -f "combined_daemon.py" 2>/dev/null || true
pkill -f "chaotic_trader.py" 2>/dev/null || true
pkill -f "comprehensive_indexer" 2>/dev/null || true
pkill -f "run_comprehensive_indexer" 2>/dev/null || true

sleep 2
log_success "Processes killed"
