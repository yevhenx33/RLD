#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# RLD Simulation — Clean Restart
# ═══════════════════════════════════════════════════════════════
# Tears down everything, restarts Anvil from a clean fork,
# and relaunches the full Docker Compose stack.
#
# Usage:  ./docker/restart.sh
# ═══════════════════════════════════════════════════════════════

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RLD_ROOT="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.yml"

# Load ETH_RPC_URL from contracts/.env (needed for Anvil fork)
if [ -f "$RLD_ROOT/contracts/.env" ]; then
    export $(grep -E '^ETH_RPC_URL=' "$RLD_ROOT/contracts/.env" | xargs)
fi
: "${ETH_RPC_URL:?ETH_RPC_URL not set — add it to contracts/.env}"

FORK_BLOCK="${FORK_BLOCK:-21698573}"
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}═══ RLD Simulation — Clean Restart ═══${NC}\n"

# ─── Step 1: Tear down containers + volumes ───────────────────
echo -e "${YELLOW}[1/3] Tearing down Docker Compose stack...${NC}"
sudo docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
echo -e "${GREEN}  ✓ Containers and volumes removed${NC}"

# ─── Step 2: Restart Anvil from clean fork ────────────────────
echo -e "${YELLOW}[2/3] Restarting Anvil (fork block $FORK_BLOCK)...${NC}"
pkill -f "anvil" 2>/dev/null || true
sleep 2

nohup anvil \
    --fork-url "$ETH_RPC_URL" \
    --fork-block-number "$FORK_BLOCK" \
    --block-time 1 \
    --host 0.0.0.0 \
    > /tmp/anvil.log 2>&1 &

# Wait for Anvil to be ready
for i in $(seq 1 30); do
    if cast block-number --rpc-url http://localhost:8545 > /dev/null 2>&1; then
        BLOCK=$(cast block-number --rpc-url http://localhost:8545)
        echo -e "${GREEN}  ✓ Anvil ready at block $BLOCK${NC}"
        break
    fi
    sleep 1
done
cast block-number --rpc-url http://localhost:8545 > /dev/null 2>&1 || {
    echo "  ✗ Anvil failed to start — check /tmp/anvil.log"
    exit 1
}

# ─── Step 3: Launch Docker Compose stack ──────────────────────
echo -e "${YELLOW}[3/3] Launching Docker Compose stack...${NC}"
echo "  Deployer will run (~3 min), then indexer + daemons start."
echo ""
sudo docker compose -f "$COMPOSE_FILE" up -d

echo ""
echo -e "${GREEN}═══ Stack is up! ═══${NC}"
echo ""
echo "  Monitor:  sudo docker compose -f docker/docker-compose.yml logs -f"
echo "  Status:   sudo docker compose -f docker/docker-compose.yml ps -a"
echo "  Stop:     sudo docker compose -f docker/docker-compose.yml down -v"
echo ""
