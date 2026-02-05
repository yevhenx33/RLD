#!/bin/bash
# Fund user with USDC, convert to aUSDC, then wrap to waUSDC
# Usage: fund.sh <ADDRESS> <USER_KEY> <USDC_AMOUNT_DOLLARS>
#
# Example: fund.sh 0x123... 0xabc... 1000000  (funds $1M)

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

ADDRESS=$1
USER_KEY=$2
AMOUNT_USD=${3:-1000000}

if [ -z "$ADDRESS" ] || [ -z "$USER_KEY" ]; then
    echo "Usage: fund.sh <ADDRESS> <USER_KEY> <USDC_AMOUNT_DOLLARS>"
    exit 1
fi

AMOUNT_WEI=$((AMOUNT_USD * 1000000))

# Mainnet addresses
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"

log_step "1" "Funding $ADDRESS with \$$AMOUNT_USD USDC"

# Set ETH balance for whale and user
cast rpc anvil_setBalance "$USDC_WHALE" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_setBalance "$ADDRESS" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null

# Impersonate whale and transfer USDC
cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
cast send "$USDC" "transfer(address,uint256)" "$ADDRESS" "$AMOUNT_WEI" \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

log_step "2" "Supplying to Aave"
cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$AMOUNT_WEI" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
    "$USDC" "$AMOUNT_WEI" "$ADDRESS" 0 \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null

log_step "3" "Wrapping aUSDC → waUSDC"
AUSDC_BAL=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$ADDRESS" --rpc-url "$RPC_URL" | awk '{print $1}')

cast send "$AUSDC" "approve(address,uint256)" "$WAUSDC" "$AUSDC_BAL" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" --gas-limit 150000 > /dev/null
cast send "$WAUSDC" "wrap(uint256)" "$AUSDC_BAL" \
    --private-key "$USER_KEY" --rpc-url "$RPC_URL" --gas-limit 500000 > /dev/null

WAUSDC_BAL=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$ADDRESS" --rpc-url "$RPC_URL" | awk '{print $1}')
log_success "Funded: $((WAUSDC_BAL / 1000000)) waUSDC"
