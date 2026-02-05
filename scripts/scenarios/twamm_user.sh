#!/bin/bash
# Setup TWAMM User (User C pattern)
# Usage: twamm_user.sh <USER_KEY> <AMOUNT_DOLLARS> <DURATION_HOURS>
#
# Example: twamm_user.sh $USER_C_KEY 100000 1

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
AMOUNT=${2:-100000}
DURATION_HOURS=${3:-1}

if [ -z "$USER_KEY" ]; then
    echo "Usage: twamm_user.sh <USER_KEY> [AMOUNT_DOLLARS] [DURATION_HOURS]"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)
log_header "TWAMM User Setup: $USER_ADDR"

# 1. Fund user
$SCRIPT_DIR/../actions/fund.sh "$USER_ADDR" "$USER_KEY" "$AMOUNT"

# 2. Get waUSDC balance and submit TWAMM order
WAUSDC_BAL=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')

DURATION_SECONDS=$((DURATION_HOURS * 3600))
$SCRIPT_DIR/../actions/twamm_submit.sh "$USER_KEY" "$WAUSDC_BAL" "$DURATION_SECONDS" "$ZERO_FOR_ONE_LONG"

log_success "TWAMM User ready: $USER_ADDR"
