#!/bin/bash
# Withdraw wRLP from broker to user wallet
# Usage: withdraw_position.sh <BROKER_ADDRESS> <USER_KEY> <AMOUNT_DOLLARS>

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

BROKER=$1
USER_KEY=$2
AMOUNT_USD=$3

if [ -z "$BROKER" ] || [ -z "$USER_KEY" ] || [ -z "$AMOUNT_USD" ]; then
    echo "Usage: withdraw_position.sh <BROKER_ADDRESS> <USER_KEY> <AMOUNT_DOLLARS>"
    exit 1
fi

AMOUNT_WEI=$((AMOUNT_USD * 1000000))
USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)

log_step "1" "Withdrawing $AMOUNT_USD wRLP from broker"

cast send "$BROKER" "withdrawPositionToken(address,uint256)" "$USER_ADDR" "$AMOUNT_WEI" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null

log_success "Withdrawn wRLP to $USER_ADDR"
