#!/bin/bash

# Colors for pretty printing
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' 

echo -e "${BLUE}🚀 Starting RLD Protocol Development Environment...${NC}"

# 0. Cleanup previous runs to prevent port conflicts
echo -e "${BLUE}>> Cleaning up old processes...${NC}"
pkill anvil
pkill -f "operator_bot.py"
pkill -f "uvicorn" 

# 1. Source Environment Variables
if [ -f contracts/.env ]; then
    set -a
    source contracts/.env
    set +a
else
    echo -e "${RED}❌ Error: contracts/.env not found!${NC}"
    exit 1
fi

if [ -z "$MAINNET_RPC_URL" ]; then
    echo -e "${RED}❌ Error: MAINNET_RPC_URL not set in .env${NC}"
    exit 1
fi

# 2. Start Anvil (Local Blockchain)
echo -e "${BLUE}>> Starting Anvil Node (Port 8545)...${NC}"
anvil --fork-url $MAINNET_RPC_URL --port 8545 > /dev/null 2>&1 &
ANVIL_PID=$!

# Wait for Anvil to be ready
echo -n "Waiting for chain..."
while ! curl -s -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' http://127.0.0.1:8545 > /dev/null; do
    sleep 1
    echo -n "."
done
echo -e "${GREEN} Done!${NC}"

# 3. Deploy Contracts
echo -e "${BLUE}>> Deploying Contracts...${NC}"
cd contracts || exit
forge script script/DeployRLD.s.sol --fork-url http://127.0.0.1:8545 --broadcast --legacy
cd ..

# Check if addresses.json exists
if [ ! -f "shared/addresses.json" ]; then
    echo -e "${RED}❌ Deployment Failed. shared/addresses.json not found.${NC}"
    kill $ANVIL_PID
    exit 1
fi
echo -e "${GREEN}✅ Contracts Deployed & Addresses Exported${NC}"

# 4. Start REAL Backend (Uvicorn)
echo -e "${BLUE}>> Starting FastAPI Backend (Port 8000)...${NC}"
cd backend || exit

# Run Uvicorn in background
source ../venv/bin/activate
uvicorn api:app --reload --port 8000 > backend_logs.txt 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}✅ Backend running (logs in backend/backend_logs.txt)${NC}"

# 5. Start Operator Bot
echo -e "${BLUE}>> Starting Operator Bot...${NC}"
export MAINNET_RPC_URL="http://127.0.0.1:8545"
# Operator now reads from shared/addresses.json directly
python3 operator_bot.py > operator_logs.txt 2>&1 &
OPERATOR_PID=$!
echo -e "${GREEN}✅ Operator running (logs in backend/operator_logs.txt)${NC}"

# 6. Start Frontend
echo -e "${BLUE}>> Starting Frontend...${NC}"
cd ../frontend || exit
# Frontend now reads from shared/addresses.json directly
npm run dev

# Exit Trap
trap "kill $ANVIL_PID $BACKEND_PID $OPERATOR_PID" EXIT