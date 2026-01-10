#!/bin/bash

# Colors for pretty printing
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' 

echo -e "${BLUE}🚀 Starting RLD Protocol Development Environment...${NC}"

# Variables to track status
ANVIL_READY=false
DEPLOY_SUCCESS=false

# 0. Cleanup previous runs to prevent port conflicts
echo -e "${BLUE}>> Cleaning up old processes...${NC}"
pkill anvil
pkill -f "operator_bot.py"
pkill -f "indexer.py"
pkill -f "uvicorn" 
# Wait a moment for ports to clear
sleep 1

# 1. Source Environment Variables
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo -e "${YELLOW}⚠️  Warning: .env not found! Some features may trigger errors.${NC}"
fi

if [ -z "$MAINNET_RPC_URL" ]; then
    echo -e "${YELLOW}⚠️  Warning: MAINNET_RPC_URL not set in .env${NC}"
    echo -e "${YELLOW}   Skipping Anvil, Contracts, and Indexer.${NC}"
else
    # 2. Start Anvil (Local Blockchain)
    echo -e "${BLUE}>> Starting Anvil Node (Port 8545)...${NC}"
    anvil --fork-url $MAINNET_RPC_URL --port 8545 > /dev/null 2>&1 &
    ANVIL_PID=$!

    # Wait for Anvil to be ready (Max 10 seconds)
    echo -n "Waiting for chain..."
    MAX_RETRIES=10
    COUNT=0
    while [ $COUNT -lt $MAX_RETRIES ]; do
        if curl -s -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' http://127.0.0.1:8545 > /dev/null; then
            echo -e "${GREEN} Done!${NC}"
            ANVIL_READY=true
            break
        fi
        sleep 1
        echo -n "."
        COUNT=$((COUNT+1))
    done

    if [ "$ANVIL_READY" = false ]; then
        echo -e "${RED} Timeout waiting for Anvil. Proceeding without chain.${NC}"
    fi
fi

# 3. Deploy Contracts (Only if Anvil is Ready)
if [ "$ANVIL_READY" = true ]; then
    echo -e "${BLUE}>> Deploying Contracts...${NC}"
    cd contracts || exit
    if forge script script/DeployRLD.s.sol --fork-url http://127.0.0.1:8545 --broadcast --legacy; then
        cd ..
        if [ -f "shared/addresses.json" ]; then
            echo -e "${GREEN}✅ Contracts Deployed & Addresses Exported${NC}"
            DEPLOY_SUCCESS=true
        else
             echo -e "${RED}❌ Deployment reported success but addresses.json missing.${NC}"
        fi
    else
        echo -e "${RED}❌ Contract Deployment Failed.${NC}"
        cd ..
    fi


else
    echo -e "${YELLOW}⚠️  Skipping Contract Deployment (Chain not ready).${NC}"
fi

# 3b. Run Data Continuity Check (Fill Gaps) - RUN ALWAYS
echo -e "${BLUE}>> Checking Data Continuity...${NC}"
if [ -f "venv/bin/activate" ]; then
    # We must activate venv to have python packages
    source venv/bin/activate
    # Run in foreground to ensure DB is patched before API starts
    # But for speed, maybe background? No, safer foreground if gaps are huge.
    # Actually, user wants "deployment script to check", implying it's a step.
    # Let's run it.
    python3 backend/fill_gaps_startup.py || echo -e "${YELLOW}⚠️ Gap Fill failed (non-fatal)${NC}"
    deactivate 
else
    echo -e "${YELLOW}⚠️  venv not found, skipping gap fill.${NC}"
fi

# 4. Start REAL Backend (Uvicorn) - ALWAYS START
echo -e "${BLUE}>> Starting FastAPI Backend (Port 8000)...${NC}"
cd backend || exit

# Run Uvicorn in background
source ../venv/bin/activate
uvicorn api:app --reload --port 8000 > backend_logs.txt 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}✅ Backend running (logs in backend/backend_logs.txt)${NC}"

# 4b. Start Indexer (Only if Deploy Success)
if [ "$DEPLOY_SUCCESS" = true ]; then
    echo -e "${BLUE}>> Starting Indexer...${NC}"
    python3 indexer.py > indexer_logs.txt 2>&1 &
    INDEXER_PID=$!
    echo -e "${GREEN}✅ Indexer running (logs in backend/indexer_logs.txt)${NC}"
else
    echo -e "${YELLOW}⚠️  Skipping Indexer (Dependencies missing).${NC}"
fi

# 5. Start Operator Bot (Only if Deploy Success)
if [ "$DEPLOY_SUCCESS" = true ]; then
    echo -e "${BLUE}>> Starting Operator Bot...${NC}"
    export MAINNET_RPC_URL="http://127.0.0.1:8545"
    python3 operator_bot.py > operator_logs.txt 2>&1 &
    OPERATOR_PID=$!
    echo -e "${GREEN}✅ Operator running (logs in backend/operator_logs.txt)${NC}"
else
    echo -e "${YELLOW}⚠️  Skipping Operator Bot (Dependencies missing).${NC}"
fi

# 6. Start Frontend - ALWAYS START
echo -e "${BLUE}>> Starting Frontend...${NC}"
cd ../frontend || exit
npm run dev

# Exit Trap
# Only kill PIDs that were actually set
cleanup() {
    echo -e "${BLUE}Shutting down...${NC}"
    [ ! -z "$ANVIL_PID" ] && kill $ANVIL_PID 2>/dev/null
    [ ! -z "$BACKEND_PID" ] && kill $BACKEND_PID 2>/dev/null
    [ ! -z "$INDEXER_PID" ] && kill $INDEXER_PID 2>/dev/null
    [ ! -z "$OPERATOR_PID" ] && kill $OPERATOR_PID 2>/dev/null
}
trap cleanup EXIT