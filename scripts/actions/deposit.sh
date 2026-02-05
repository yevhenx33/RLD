#!/bin/bash
# Deposit waUSDC to broker (simple transfer)
# Usage: deposit.sh <BROKER_ADDRESS> <USER_KEY> <AMOUNT_DOLLARS>
#
# Example: deposit.sh 0xBroker... 0xabc... 1000000  (deposit $1M)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

BROKER=$1
USER_KEY=$2
AMOUNT_USD=${3:-"all"}

if [ -z "$BROKER" ] || [ -z "$USER_KEY" ]; then
    echo "Usage: deposit.sh <BROKER_ADDRESS> <USER_KEY> [AMOUNT_DOLLARS|all]"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)

# Get current balance
CURRENT_BAL=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')

if [ "$AMOUNT_USD" = "all" ]; then
    AMOUNT_WEI=$CURRENT_BAL
else
    AMOUNT_WEI=$((AMOUNT_USD * 1000000))
fi

log_step "1" "Depositing $((AMOUNT_WEI / 1000000)) waUSDC to broker"

# Simple transfer to broker = deposit
cast send "$WAUSDC" "transfer(address,uint256)" "$BROKER" "$AMOUNT_WEI" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null

log_success "Deposited to $BROKER"
