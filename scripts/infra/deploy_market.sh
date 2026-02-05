#!/bin/bash
# Deploy wrapped market (waUSDC/wRLP) and update .env
# Usage: deploy_market.sh

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

# Load keys from contracts/.env
source /home/ubuntu/RLD/contracts/.env 2>/dev/null || true

RPC_URL=${RPC_URL:-"http://localhost:8545"}

log_step "1" "Deploying Wrapped Market..."
cd /home/ubuntu/RLD/contracts

MARKET_OUTPUT=$(USE_MOCK_ORACLE=true MOCK_ORACLE=$MOCK_ORACLE \
    forge script script/DeployWrappedMarket.s.sol --tc DeployWrappedMarket \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

if ! echo "$MARKET_OUTPUT" | grep -q "WRAPPED MARKET CREATED"; then
    echo "$MARKET_OUTPUT"
    log_error "Market deployment failed"
fi

# Extract addresses from output
WAUSDC=$(echo "$MARKET_OUTPUT" | grep -i "waUSDC deployed:" | awk '{print $NF}')
if [ -z "$WAUSDC" ]; then
    WAUSDC=$(echo "$MARKET_OUTPUT" | grep "collateralToken (waUSDC):" | awk '{print $NF}')
fi
MARKET_ID=$(echo "$MARKET_OUTPUT" | grep "MarketId:" | awk '{print $NF}')
POSITION_TOKEN=$(echo "$MARKET_OUTPUT" | grep "positionToken (wRLP):" | awk '{print $NF}')
BROKER_FACTORY=$(echo "$MARKET_OUTPUT" | grep "BrokerFactory:" | awk '{print $NF}')

if [ -z "$WAUSDC" ] || [ -z "$POSITION_TOKEN" ] || [ -z "$BROKER_FACTORY" ]; then
    log_error "Failed to extract market addresses"
fi

log_success "Wrapped market deployed"
echo "  waUSDC:         $WAUSDC"
echo "  wRLP:           $POSITION_TOKEN"
echo "  BrokerFactory:  $BROKER_FACTORY"
echo "  MarketId:       $MARKET_ID"

# Determine token order
log_step "2" "Determining currency order..."
WAUSDC_LOWER=$(echo "$WAUSDC" | tr '[:upper:]' '[:lower:]')
POSITION_TOKEN_LOWER=$(echo "$POSITION_TOKEN" | tr '[:upper:]' '[:lower:]')

if [[ "$WAUSDC_LOWER" < "$POSITION_TOKEN_LOWER" ]]; then
    TOKEN0="$WAUSDC"
    TOKEN1="$POSITION_TOKEN"
    ZERO_FOR_ONE_LONG=true
else
    TOKEN0="$POSITION_TOKEN"
    TOKEN1="$WAUSDC"
    ZERO_FOR_ONE_LONG=false
fi

log_success "Token order: TOKEN0=$TOKEN0"

# Prime TWAMM oracle
log_step "3" "Priming TWAMM oracle..."
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null
log_success "Oracle primed (advanced 2 hours)"

# Update .env
"$SCRIPT_DIR/../utils/update_env.sh" "WAUSDC" "$WAUSDC"
"$SCRIPT_DIR/../utils/update_env.sh" "POSITION_TOKEN" "$POSITION_TOKEN"
"$SCRIPT_DIR/../utils/update_env.sh" "MARKET_ID" "$MARKET_ID"
"$SCRIPT_DIR/../utils/update_env.sh" "TOKEN0" "$TOKEN0"
"$SCRIPT_DIR/../utils/update_env.sh" "TOKEN1" "$TOKEN1"
"$SCRIPT_DIR/../utils/update_env.sh" "ZERO_FOR_ONE_LONG" "$ZERO_FOR_ONE_LONG"
"$SCRIPT_DIR/../utils/update_env.sh" "BROKER_FACTORY" "$BROKER_FACTORY"

log_success "Updated .env with market addresses"
