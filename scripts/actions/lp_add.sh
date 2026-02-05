#!/bin/bash
# Add V4 liquidity
# Usage: lp_add.sh <USER_KEY> <WAUSDC_AMOUNT> <WRLP_AMOUNT>
#
# Example: lp_add.sh 0xabc... 5000000 5000000  (LP $5M each)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
WAUSDC_AMT=${2:-5000000}
WRLP_AMT=${3:-5000000}

if [ -z "$USER_KEY" ]; then
    echo "Usage: lp_add.sh <USER_KEY> [WAUSDC_DOLLARS] [WRLP_DOLLARS]"
    exit 1
fi

WAUSDC_WEI=$((WAUSDC_AMT * 1000000))
WRLP_WEI=$((WRLP_AMT * 1000000))

# V4 Contract addresses
V4_POSITION_MANAGER="0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"

log_step "1" "Approving V4 contracts..."
cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$WAUSDC_WEI" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$WRLP_WEI" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null

cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WAUSDC" "$V4_POSITION_MANAGER" "$(python3 -c 'print(2**160-1)')" "$(python3 -c 'print(2**48-1)')" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$POSITION_TOKEN" "$V4_POSITION_MANAGER" "$(python3 -c 'print(2**160-1)')" "$(python3 -c 'print(2**48-1)')" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null

log_step "2" "Adding V4 liquidity..."
cd /home/ubuntu/RLD/contracts

AUSDC_AMOUNT=$WAUSDC_WEI WRLP_AMOUNT=$WRLP_WEI \
    WAUSDC=$WAUSDC POSITION_TOKEN=$POSITION_TOKEN TWAMM_HOOK=$TWAMM_HOOK \
    forge script script/AddLiquidityWrapped.s.sol --tc AddLiquidityWrappedScript \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/lp_output.log 2>&1

if grep -q "LP Position Created" /tmp/lp_output.log; then
    TOKEN_ID=$(grep "Token ID:" /tmp/lp_output.log | awk '{print $NF}')
    log_success "LP Position created (Token ID: $TOKEN_ID)"
else
    log_error "LP creation failed - check /tmp/lp_output.log"
fi
