#!/bin/bash
# ============================================================================
# deploy_mock_testnet.sh - Deploy RLD market with MockRLDAaveOracle
# ============================================================================
#
# Deploys a complete RLD market using the mock oracle for testnet.
# The mock oracle can be updated via the rate_sync_daemon.py.
#
# Usage:
#   ./scripts/deploy_mock_testnet.sh
#
# After deployment:
#   1. Note the MOCK_ORACLE_ADDR from output
#   2. Run: MOCK_ORACLE_ADDR=0x... python3 backend/scripts/rate_sync_daemon.py
#
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}   RLD MOCK TESTNET DEPLOYMENT${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# Load environment
cd /home/ubuntu/RLD/contracts
source .env 2>/dev/null || true
source ../.env 2>/dev/null || true

# Configuration
RPC_URL=${RPC_URL:-http://localhost:8545}
PRIVATE_KEY=${PRIVATE_KEY:-0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80}
DEPLOYER=$(cast wallet address --private-key "$PRIVATE_KEY" 2>/dev/null)

echo "RPC:      $RPC_URL"
echo "Deployer: $DEPLOYER"
echo ""

# Check Anvil is running
if ! curl -s $RPC_URL > /dev/null 2>&1; then
    echo -e "${RED}Error: Anvil not running at $RPC_URL${NC}"
    exit 1
fi

# ============================================================================
# Step 1: Deploy MockRLDAaveOracle
# ============================================================================
echo -e "${YELLOW}[1/3] Deploying MockRLDAaveOracle...${NC}"

MOCK_ORACLE=$(forge create src/rld/modules/oracles/MockRLDAaveOracle.sol:MockRLDAaveOracle \
    --private-key $PRIVATE_KEY \
    --rpc-url $RPC_URL \
    --broadcast \
    --json 2>/dev/null | jq -r '.deployedTo')

echo -e "${GREEN}✓ MockRLDAaveOracle: $MOCK_ORACLE${NC}"

# Set initial rate (fetch from API)
echo -e "${YELLOW}[2/3] Setting initial rate from API...${NC}"

# Fetch current rate
API_KEY=${API_KEY:-***REDACTED_API_KEY***}
RATE_JSON=$(curl -s "https://rate-dashboard.onrender.com/rates?limit=1&symbol=USDC" \
    -H "X-API-Key: $API_KEY")
APY=$(echo $RATE_JSON | jq -r '.[0].apy')

# Convert APY to RAY: apy / 100 * 1e27
# Using bc for precision: 4.64 / 100 * 10^27 = 4.64e25
RATE_RAY=$(echo "scale=0; $APY / 100 * 10^27" | bc)

echo "  Current APY: ${APY}%"
echo "  Rate in RAY: $RATE_RAY"

# Set the rate
cast send $MOCK_ORACLE "setRate(uint256)" $RATE_RAY \
    --private-key $PRIVATE_KEY \
    --rpc-url $RPC_URL > /dev/null

echo -e "${GREEN}✓ Initial rate set to ${APY}%${NC}"

# ============================================================================
# Step 3: Save to JSON
# ============================================================================
echo -e "${YELLOW}[3/3] Saving deployment info...${NC}"

cat > mock_testnet.json <<EOF
{
  "mockOracle": "$MOCK_ORACLE",
  "initialRatePercent": $APY,
  "initialRateRay": "$RATE_RAY",
  "deployer": "$DEPLOYER",
  "timestamp": $(date +%s)
}
EOF

echo -e "${GREEN}✓ Saved to mock_testnet.json${NC}"
echo ""

# ============================================================================
# Output
# ============================================================================
echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}   DEPLOYMENT COMPLETE${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""
echo "MockRLDAaveOracle: $MOCK_ORACLE"
echo "Current Rate: ${APY}%"
echo ""
echo "To start the rate sync daemon:"
echo ""
echo -e "${GREEN}  export MOCK_ORACLE_ADDR=$MOCK_ORACLE${NC}"
echo -e "${GREEN}  cd backend && python3 scripts/rate_sync_daemon.py${NC}"
echo ""
echo "Or run manually:"
echo ""
echo "  cast call $MOCK_ORACLE 'getRatePercent()' --rpc-url $RPC_URL"
echo "  cast send $MOCK_ORACLE 'setRate(uint256)' <NEW_RATE_RAY> --private-key \$PRIVATE_KEY --rpc-url $RPC_URL"
echo ""
