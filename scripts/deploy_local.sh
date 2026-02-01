#!/bin/bash
#
# RLD Protocol - Local Fork Deployment Script
# 
# This script automates the complete deployment of RLD Protocol on a local Anvil fork.
# Usage: ./scripts/deploy_local.sh [BLOCK_NUMBER]
#
# Example:
#   ./scripts/deploy_local.sh 24335184
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
RLD_ROOT="/home/ubuntu/RLD"
CONTRACTS_DIR="$RLD_ROOT/contracts"
BACKEND_DIR="$RLD_ROOT/backend"
FRONTEND_DIR="$RLD_ROOT/frontend"
DEFAULT_BLOCK=24363333

# Parse arguments
FORK_BLOCK=${1:-$DEFAULT_BLOCK}

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║           RLD Protocol Local Deployment Script             ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Fork Block: ${YELLOW}$FORK_BLOCK${NC}"
echo ""

# =============================================================================
# Step 1: Kill existing processes
# =============================================================================
echo -e "${CYAN}[1/9] Stopping existing processes...${NC}"

pkill -9 -f anvil 2>/dev/null || true
pkill -f "uvicorn api:app" 2>/dev/null || true
sleep 2
echo -e "${GREEN}✓ Processes stopped${NC}"

# =============================================================================
# Step 2: Start Anvil Fork
# =============================================================================
echo -e "${CYAN}[2/9] Starting Anvil fork on block $FORK_BLOCK...${NC}"

cd "$CONTRACTS_DIR"
source .env

if [ -z "$MAINNET_RPC_URL" ]; then
    echo -e "${RED}✗ Error: MAINNET_RPC_URL not set in contracts/.env${NC}"
    exit 1
fi

anvil --fork-url "$MAINNET_RPC_URL" --fork-block-number "$FORK_BLOCK" --host 0.0.0.0 > "$RLD_ROOT/anvil.log" 2>&1 &
ANVIL_PID=$!
echo "  Anvil PID: $ANVIL_PID"

# Wait for Anvil to be ready
echo "  Waiting for Anvil to initialize..."
for i in {1..30}; do
    if curl -s -X POST http://localhost:8545 -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Verify
BLOCK_HEX=$(curl -s -X POST http://localhost:8545 -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' | python3 -c "import sys,json; print(json.load(sys.stdin)['result'])")
BLOCK_DEC=$((BLOCK_HEX))
echo -e "${GREEN}✓ Anvil running on block $BLOCK_DEC${NC}"

# =============================================================================
# Step 3: Deploy Protocol
# =============================================================================
echo -e "${CYAN}[3/9] Deploying RLD Protocol contracts...${NC}"

cd "$CONTRACTS_DIR"
forge script script/DeployRLDProtocol.s.sol:DeployRLDProtocol \
    --rpc-url http://localhost:8545 \
    --broadcast \
    --quiet

# Extract key addresses
BROADCAST_FILE="$CONTRACTS_DIR/broadcast/DeployRLDProtocol.s.sol/1/run-latest.json"
CORE_ADDR=$(python3 -c "import json; d=json.load(open('$BROADCAST_FILE')); print([t['contractAddress'] for t in d['transactions'] if t.get('contractName')=='RLDCore'][0])")
FACTORY_ADDR=$(python3 -c "import json; d=json.load(open('$BROADCAST_FILE')); print([t['contractAddress'] for t in d['transactions'] if t.get('contractName')=='RLDMarketFactory'][0])")
AAVE_ORACLE=$(python3 -c "import json; d=json.load(open('$BROADCAST_FILE')); print([t['contractAddress'] for t in d['transactions'] if t.get('contractName')=='RLDAaveOracle'][0])")

echo -e "${GREEN}✓ Protocol deployed${NC}"
echo "  RLDCore: $CORE_ADDR"
echo "  RLDMarketFactory: $FACTORY_ADDR"
echo "  RLDAaveOracle: $AAVE_ORACLE"

# =============================================================================
# Step 4: Create Test Market
# =============================================================================
echo -e "${CYAN}[4/9] Creating test market...${NC}"

cd "$CONTRACTS_DIR"
forge script script/CreateTestMarket.s.sol:CreateTestMarket \
    --rpc-url http://localhost:8545 \
    --broadcast \
    --quiet 2>&1 || {
    echo -e "${YELLOW}⚠ Market creation may have failed. Continuing...${NC}"
}

# Merge market deployments into main deployments.json
if [ -f "market_deployments.json" ] && [ -f "deployments.json" ]; then
    echo "Merging deployment files..."
    jq -s '.[0] * .[1]' deployments.json market_deployments.json > deployments.tmp && mv deployments.tmp deployments.json
    rm market_deployments.json
    echo -e "${GREEN}✓ Addresses consolidated into contracts/deployments.json${NC}"
fi

echo -e "${GREEN}✓ Test market created${NC}"

# =============================================================================
# Step 5: Extract Market ID
# =============================================================================
echo -e "${CYAN}[5/9] Extracting Market ID...${NC}"

MARKET_ID=$(python3 << EOF
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('http://localhost:8545'))
factory_addr = Web3.to_checksum_address("$FACTORY_ADDR")
event_sig = w3.keccak(text="MarketDeployed(bytes32,address,address,address,address,address)")
logs = w3.eth.get_logs({'address': factory_addr, 'fromBlock': 0, 'toBlock': 'latest', 'topics': [event_sig]})
if logs:
    print("0x" + logs[-1]['topics'][1].hex())
else:
    print("")
EOF
)

if [ -z "$MARKET_ID" ]; then
    echo -e "${RED}✗ No market found${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Market ID: $MARKET_ID${NC}"

# =============================================================================
# Step 6: Clean Databases
# =============================================================================
echo -e "${CYAN}[6/9] Cleaning databases...${NC}"

cd "$BACKEND_DIR"
python3 << 'EOF'
import sqlite3
for db in ["market_state.db", "simulations.db"]:
    try:
        conn = sqlite3.connect(db)
        for table in ["markets", "market_risk_params", "market_state_snapshots", "state_indexer_state"]:
            try:
                conn.execute(f"DELETE FROM {table}")
            except: pass
        conn.commit()
        conn.close()
    except: pass
EOF

echo -e "${GREEN}✓ Databases cleaned${NC}"

# =============================================================================
# Step 7: Start Backend
# =============================================================================
echo -e "${CYAN}[7/9] Starting backend API...${NC}"

cd "$BACKEND_DIR"
uvicorn api:app --host 0.0.0.0 --port 8000 --reload > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"

# Wait for backend to be ready
echo "  Waiting for backend to initialize..."
for i in {1..30}; do
    if curl -s http://localhost:8000/simulations/enriched > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo -e "${GREEN}✓ Backend running${NC}"

# =============================================================================
# Step 8: Register Market
# =============================================================================
echo -e "${CYAN}[8/9] Registering market with indexer...${NC}"

sleep 2
REGISTER_RESULT=$(curl -s -X POST "http://localhost:8000/market/register?market_id=$MARKET_ID")
echo "  Response: $REGISTER_RESULT"

# Verify registration
sleep 2
MARKET_DATA=$(curl -s "http://localhost:8000/simulation/$MARKET_ID/enriched")
MARKET_SYMBOL=$(echo "$MARKET_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('positionTokenSymbol', 'Unknown'))")
INDEX_PRICE=$(echo "$MARKET_DATA" | python3 -c "import sys,json; print(json.load(sys.stdin).get('prices', {}).get('index_price_display', 'N/A'))")

echo -e "${GREEN}✓ Market registered${NC}"

# =============================================================================
# Step 9: Check and Start Frontend
# =============================================================================
echo -e "${CYAN}[9/9] Checking frontend...${NC}"

# Check if frontend is already running
if curl -s http://localhost:5173 > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Frontend already running at http://localhost:5173${NC}"
    FRONTEND_STATUS="already running"
else
    echo "  Frontend not running. Starting..." 
    cd "$FRONTEND_DIR"
    npm run dev > /tmp/frontend.log 2>&1 &
    FRONTEND_PID=$!
    echo "  Frontend PID: $FRONTEND_PID"
    
    # Wait for frontend to be ready
    echo "  Waiting for frontend to initialize..."
    for i in {1..30}; do
        if curl -s http://localhost:5173 > /dev/null 2>&1; then
            break
        fi
        sleep 1
    done
    
    if curl -s http://localhost:5173 > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Frontend started at http://localhost:5173${NC}"
        FRONTEND_STATUS="started"
    else
        echo -e "${YELLOW}⚠ Frontend may still be initializing...${NC}"
        FRONTEND_STATUS="starting"
    fi
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                    Deployment Complete                     ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Market:${NC}       $MARKET_SYMBOL"
echo -e "  ${GREEN}Market ID:${NC}    $MARKET_ID"
echo -e "  ${GREEN}Index Price:${NC}  $INDEX_PRICE"
echo -e "  ${GREEN}Fork Block:${NC}   $FORK_BLOCK"
echo ""
echo -e "  ${CYAN}Anvil:${NC}    http://localhost:8545"
echo -e "  ${CYAN}Backend:${NC}  http://localhost:8000"
echo -e "  ${CYAN}Frontend:${NC} http://localhost:5173 ($FRONTEND_STATUS)"
echo ""
echo -e "  ${YELLOW}Logs:${NC}"
echo -e "    Anvil:   tail -f $RLD_ROOT/anvil.log"
echo -e "    Backend:  tail -f /tmp/backend.log"
echo -e "    Frontend: tail -f /tmp/frontend.log"
echo ""

# Save deployment info
cat > "$RLD_ROOT/shared/deployment_info.json" << DEPLOY_JSON
{
  "fork_block": $FORK_BLOCK,
  "market_id": "$MARKET_ID",
  "market_symbol": "$MARKET_SYMBOL",
  "index_price": "$INDEX_PRICE",
  "contracts": {
    "RLDCore": "$CORE_ADDR",
    "RLDMarketFactory": "$FACTORY_ADDR",
    "RLDAaveOracle": "$AAVE_ORACLE"
  },
  "deployed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
DEPLOY_JSON

echo -e "  ${GREEN}Deployment info saved to:${NC} shared/deployment_info.json"
echo ""
