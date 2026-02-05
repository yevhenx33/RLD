#!/bin/bash
# Submit TWAMM order
# Usage: twamm_submit.sh <USER_KEY> <AMOUNT_WEI> <DURATION_SECONDS> <ZERO_FOR_ONE>
#
# Example: twamm_submit.sh 0xabc... 100000000000 3600 true  (sell $100k over 1 hour)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
AMOUNT=$2
DURATION=${3:-3600}
ZERO_FOR_ONE=${4:-true}

if [ -z "$USER_KEY" ] || [ -z "$AMOUNT" ]; then
    echo "Usage: twamm_submit.sh <USER_KEY> <AMOUNT_WEI> <DURATION_SECONDS> <ZERO_FOR_ONE>"
    exit 1
fi

log_step "1" "Submitting TWAMM order: $((AMOUNT / 1000000)) tokens over $((DURATION / 60)) minutes"

cd /home/ubuntu/RLD/contracts

TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    ORDER_AMOUNT="$AMOUNT" \
    DURATION_SECONDS="$DURATION" \
    ZERO_FOR_ONE="$ZERO_FOR_ONE" \
    TWAMM_USER_KEY="$USER_KEY" \
    forge script script/LifecycleTWAMM.s.sol --tc LifecycleTWAMM \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/twamm_output.log 2>&1

if grep -q "TWAMM Order" /tmp/twamm_output.log || grep -q "SUCCESS" /tmp/twamm_output.log; then
    log_success "TWAMM order submitted"
else
    log_error "TWAMM order failed - check /tmp/twamm_output.log"
fi
