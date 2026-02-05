#!/bin/bash
# Mint wRLP debt from broker
# Usage: mint.sh <BROKER_ADDRESS> <USER_KEY> <AMOUNT_DOLLARS>
#
# Example: mint.sh 0xBroker... 0xabc... 1000000  (mint $1M worth of wRLP)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

BROKER=$1
USER_KEY=$2
AMOUNT_USD=$3

if [ -z "$BROKER" ] || [ -z "$USER_KEY" ] || [ -z "$AMOUNT_USD" ]; then
    echo "Usage: mint.sh <BROKER_ADDRESS> <USER_KEY> <AMOUNT_DOLLARS>"
    exit 1
fi

AMOUNT_WEI=$((AMOUNT_USD * 1000000))

log_step "1" "Minting $AMOUNT_USD wRLP via broker"

# modifyPosition(marketId, deltaCollateral=0, deltaDebt=amount)
cast send "$BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$AMOUNT_WEI" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null

BROKER_WRLP=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL" | awk '{print $1}')
log_success "Broker wRLP: $((BROKER_WRLP / 1000000))"
