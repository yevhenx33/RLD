#!/usr/bin/env bash
# reset.sh — Full teardown and clean restart for single-market simulation.
#
# What this does:
#   1. Stops all containers and removes the postgres volume (wipes DB)
#   2. Kills Anvil and relaunches it from scratch (clean fork state)
#   3. docker compose up -d (fresh start: postgres → indexer → deployer → daemons)
#
# Usage:
#   cd /home/ubuntu/RLD/docker
#   ./reset.sh [--no-anvil]   (skip Anvil restart if you manage it externally)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANVIL_HOST_RPC="${ANVIL_HOST_RPC:-http://localhost:8545}"
NO_ANVIL="${1:-}"

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  RLD Single-Market Reset"
echo "═══════════════════════════════════════════════════════════════"

# ── 1. Tear down all containers + postgres volume ────────────────────────
echo ""
echo "▶ Stopping all containers and wiping postgres volume..."
cd "$SCRIPT_DIR"
docker compose down -v --remove-orphans 2>&1 | grep -v "^#" || true
echo "✓ All containers stopped, volume postgres-data removed"

# ── 2. Kill and relaunch Anvil with original args (guaranteed clean fork) ─
if [ "$NO_ANVIL" != "--no-anvil" ]; then
    echo ""
    echo "▶ Restarting Anvil (clean fork)..."

    # Grab the full command line of the running anvil process
    ANVIL_PID=$(pgrep -f "anvil --fork-url" || true)
    if [ -z "$ANVIL_PID" ]; then
        echo "⚠ No running Anvil process found. Start it manually:"
        echo "    anvil --fork-url <ETH_RPC_URL> --fork-block-number 24626989 \\"
        echo "          --chain-id 31337 --block-time 12 --host 0.0.0.0"
    else
        # Extract the original command args
        ANVIL_CMD=$(cat /proc/$ANVIL_PID/cmdline | tr '\0' ' ' | sed 's/ $//')
        echo "  Killing PID $ANVIL_PID..."
        kill "$ANVIL_PID"
        sleep 2

        # Remove stale state dump if present (so it starts clean)
        DUMP_PATH=$(echo "$ANVIL_CMD" | grep -oP '(?<=--dump-state )\S+' || true)
        if [ -n "$DUMP_PATH" ] && [ -f "$DUMP_PATH" ]; then
            rm -f "$DUMP_PATH"
            echo "  Removed stale dump: $DUMP_PATH"
        fi

        # Relaunch in background with same args
        echo "  Relaunching Anvil..."
        nohup $ANVIL_CMD > /tmp/anvil-reset.log 2>&1 &
        NEW_ANVIL_PID=$!
        echo "  Launched PID $NEW_ANVIL_PID"

        # Wait for Anvil to be ready
        for i in $(seq 1 20); do
            if curl -sf "$ANVIL_HOST_RPC" -X POST \
                -H "Content-Type: application/json" \
                -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
                > /dev/null 2>&1; then
                BLOCK=$(curl -s "$ANVIL_HOST_RPC" -X POST \
                    -H "Content-Type: application/json" \
                    -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' \
                    | python3 -c "import sys,json; print(int(json.load(sys.stdin).get('result','0x0'),16))" 2>/dev/null || echo "?")
                echo "✓ Anvil ready at block $BLOCK"
                break
            fi
            sleep 1
        done
    fi
else
    echo "▶ Skipping Anvil restart (--no-anvil flag)"
fi

# ── 3. Bring the stack back up ────────────────────────────────────────────
echo ""
echo "▶ Starting fresh stack (postgres → indexer → deployer → daemons)..."
docker compose up -d --scale mm-daemon=0 --scale chaos-trader=0

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  Stack is starting up."
echo ""
echo "  Monitor deployer:   docker compose logs -f deployer"
echo "  Monitor indexer:    docker compose logs -f indexer"
echo ""
echo "  GraphQL API:        http://localhost:8080/graphql"
echo "  Healthcheck:        curl http://localhost:8080/healthz"
echo ""
echo "  DB (single market): docker exec docker-postgres-1 psql -U rld \\"
echo "      -d rld_indexer -c 'SELECT market_id FROM markets;'"
echo "═══════════════════════════════════════════════════════════════"
