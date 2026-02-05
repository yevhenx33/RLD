#!/bin/bash
# Execute V4 swap
# Usage: swap.sh <USER_KEY> <AMOUNT_WEI> <ZERO_FOR_ONE>
#
# Example: swap.sh 0xabc... 100000000000 true  (swap $100k, zeroForOne=true)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
AMOUNT=$2
ZERO_FOR_ONE=${3:-true}

if [ -z "$USER_KEY" ] || [ -z "$AMOUNT" ]; then
    echo "Usage: swap.sh <USER_KEY> <AMOUNT_WEI> <ZERO_FOR_ONE>"
    exit 1
fi

log_step "1" "Executing swap: $((AMOUNT / 1000000)) tokens, zeroForOne=$ZERO_FOR_ONE"

cd /home/ubuntu/RLD/contracts

TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    SWAP_AMOUNT="$AMOUNT" ZERO_FOR_ONE="$ZERO_FOR_ONE" \
    SWAP_USER_KEY="$USER_KEY" \
    forge script script/LifecycleSwap.s.sol --tc LifecycleSwap \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/swap_output.log 2>&1

if grep -q "SUCCESS" /tmp/swap_output.log; then
    log_success "Swap complete"
else
    log_error "Swap failed - check /tmp/swap_output.log"
fi
