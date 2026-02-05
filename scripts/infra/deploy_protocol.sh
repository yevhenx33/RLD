#!/bin/bash
# Deploy RLD Protocol and update .env
# Usage: deploy_protocol.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

# Load ETH_RPC_URL from contracts/.env
source /home/ubuntu/RLD/contracts/.env 2>/dev/null || true

RPC_URL=${RPC_URL:-"http://localhost:8545"}

log_step "1" "Deploying RLD Protocol..."
cd /home/ubuntu/RLD/contracts

DEPLOY_OUTPUT=$(forge script script/DeployRLDProtocol.s.sol --tc DeployRLDProtocol \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

if ! echo "$DEPLOY_OUTPUT" | grep -q "DEPLOYMENT COMPLETE"; then
    echo "$DEPLOY_OUTPUT"
    log_error "Protocol deployment failed"
fi

# Extract from deployments.json
TWAMM_HOOK=$(jq -r '.TWAMM' deployments.json)
FACTORY=$(jq -r '.RLDMarketFactory' deployments.json)
RLD_CORE=$(jq -r '.RLDCore' deployments.json)

log_success "Protocol deployed"
echo "  TWAMM Hook: $TWAMM_HOOK"
echo "  Factory:    $FACTORY"
echo "  RLDCore:    $RLD_CORE"

# Update .env
"$SCRIPT_DIR/../utils/update_env.sh" "RLD_CORE" "$RLD_CORE"
"$SCRIPT_DIR/../utils/update_env.sh" "TWAMM_HOOK" "$TWAMM_HOOK"
# Note: BROKER_FACTORY comes from deploy_market.sh, not here

# Deploy MockRLDAaveOracle
log_step "2" "Deploying MockRLDAaveOracle..."
MOCK_ORACLE=$(forge create src/rld/modules/oracles/MockRLDAaveOracle.sol:MockRLDAaveOracle \
    --private-key $PRIVATE_KEY \
    --rpc-url $RPC_URL \
    --broadcast 2>&1 | grep "Deployed to:" | awk '{print $3}')

if [ -z "$MOCK_ORACLE" ]; then
    log_error "Failed to deploy MockRLDAaveOracle"
fi

log_success "MockOracle: $MOCK_ORACLE"
"$SCRIPT_DIR/../utils/update_env.sh" "MOCK_ORACLE" "$MOCK_ORACLE"

# Set initial rate from API
log_step "3" "Setting initial rate..."
API_URL="${API_URL:-https://rate-dashboard.onrender.com}"
API_KEY="${API_KEY:-***REDACTED_API_KEY***}"

RATE_JSON=$(curl -s "$API_URL/rates?limit=1&symbol=USDC" -H "X-API-Key: $API_KEY")
APY=$(echo $RATE_JSON | jq -r '.[0].apy')
RATE_RAY=$(python3 -c "print(int($APY / 100 * 1e27))")

cast send $MOCK_ORACLE "setRate(uint256)" $RATE_RAY \
    --private-key $PRIVATE_KEY --rpc-url $RPC_URL > /dev/null 2>&1

log_success "Rate set to ${APY}%"
