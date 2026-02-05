#!/bin/bash
# Claim TWAMM order proceeds
# Usage: twamm_claim.sh <USER_KEY>

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1

if [ -z "$USER_KEY" ]; then
    echo "Usage: twamm_claim.sh <USER_KEY>"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)

log_step "1" "Claiming TWAMM proceeds for $USER_ADDR"

# Get balances before
WAUSDC_BEFORE=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
WRLP_BEFORE=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')

# Build PoolKey tuple for claim
# struct PoolKey { Currency currency0; Currency currency1; uint24 fee; int24 tickSpacing; IHooks hooks; }
# Call claimTokensByPoolKey(PoolKey calldata key) on TWAMM hook

cd /home/ubuntu/RLD/contracts

# Use forge to call claim (easier for struct encoding)
CLAIM_USER_KEY="$USER_KEY" TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    forge script script/ClaimTWAMM.s.sol --tc ClaimTWAMM --rpc-url "$RPC_URL" --broadcast -v > /tmp/twamm_claim_output.log 2>&1 || true

cd /home/ubuntu/RLD

# Get balances after
WAUSDC_AFTER=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
WRLP_AFTER=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')

WAUSDC_DIFF=$((WAUSDC_AFTER - WAUSDC_BEFORE))
WRLP_DIFF=$((WRLP_AFTER - WRLP_BEFORE))

log_success "Claimed: +$((WAUSDC_DIFF / 1000000)) waUSDC, +$((WRLP_DIFF / 1000000)) wRLP"
