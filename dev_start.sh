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
pkill -f "symbiotic_operator.py"
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
DEPLOY_OUTPUT=$(forge script script/DeployRLD.s.sol --fork-url http://127.0.0.1:8545 --broadcast --legacy)
ORACLE_ADDR=$(echo "$DEPLOY_OUTPUT" | grep "SymbioticRateOracle deployed at:" | awk '{print $4}')

if [ -z "$ORACLE_ADDR" ]; then
    echo -e "${RED}❌ Deployment Failed. Output:${NC}"
    echo "$DEPLOY_OUTPUT"
    kill $ANVIL_PID
    exit 1
fi
echo -e "${GREEN}✅ Symbiotic Oracle Deployed: $ORACLE_ADDR${NC}"

# 4. Start REAL Backend (Uvicorn) - UPDATED STEP
echo -e "${BLUE}>> Starting FastAPI Backend (Port 8000)...${NC}"
cd .. # Go back to root (assuming api.py is in root)

# Check if api.py exists, or adjust folder if it's in /backend
if [ ! -f "api.py" ]; then
    echo -e "${RED}⚠️  Warning: api.py not found in root. Checking /backend...${NC}"
    if [ -d "backend" ]; then
        cd backend
    fi
fi

# Run Uvicorn in background
uvicorn api:app --reload --port 8000 > backend_logs.txt 2>&1 &
BACKEND_PID=$!
echo -e "${GREEN}✅ Backend running (logs in backend_logs.txt)${NC}"

cd contracts || exit # Return to contracts folder for next steps

# 5. Config & Start Operator Bot
echo -e "${BLUE}>> Configuring & Starting Operator Bot...${NC}"
sed -i '' "s/ORACLE_ADDRESS = \".*\"/ORACLE_ADDRESS = \"$ORACLE_ADDR\"/" script/symbiotic_operator.py

export MAINNET_RPC_URL="http://127.0.0.1:8545"
python3 script/symbiotic_operator.py > ../operator_logs.txt 2>&1 &
OPERATOR_PID=$!

# 6. Config & Start Frontend
echo -e "${BLUE}>> Configuring & Starting Frontend...${NC}"
cd ../frontend/src/hooks || exit
sed -i '' "s/const CONTRACT_ADDRESS = \".*\";/const CONTRACT_ADDRESS = \"$ORACLE_ADDR\";/" useSymbioticOracle.js

cd ../.. || exit
npm run dev

# Exit Trap - Kills Anvil, Uvicorn, and Operator when you Ctrl+C
trap "kill $ANVIL_PID $BACKEND_PID $OPERATOR_PID" EXIT