#!/bin/bash
# Setup Long User (User B pattern)
# Usage: long_user.sh <USER_KEY> <AMOUNT_DOLLARS>
#
# Example: long_user.sh $USER_B_KEY 100000

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
AMOUNT=${2:-100000}

if [ -z "$USER_KEY" ]; then
    echo "Usage: long_user.sh <USER_KEY> [AMOUNT_DOLLARS]"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)
log_header "Long User Setup: $USER_ADDR"

# 1. Fund user
$SCRIPT_DIR/../actions/fund.sh "$USER_ADDR" "$USER_KEY" "$AMOUNT"

# 2. Get waUSDC balance and swap to wRLP
WAUSDC_BAL=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')

$SCRIPT_DIR/../actions/swap.sh "$USER_KEY" "$WAUSDC_BAL" "$ZERO_FOR_ONE_LONG"

log_success "Long User ready: $USER_ADDR"
