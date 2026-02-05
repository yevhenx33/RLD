#!/bin/bash
# Setup LP Provider (User A pattern)
# Usage: lp_provider.sh <USER_KEY> <COLLATERAL_DOLLARS> <LP_DOLLARS>
#
# Example: lp_provider.sh $USER_A_KEY 100000000 5000000

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
COLLATERAL=${2:-100000000}
LP_AMOUNT=${3:-5000000}

if [ -z "$USER_KEY" ]; then
    echo "Usage: lp_provider.sh <USER_KEY> [COLLATERAL_DOLLARS] [LP_DOLLARS]"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)
log_header "LP Provider Setup: $USER_ADDR"

# 1. Fund user
$SCRIPT_DIR/../actions/fund.sh "$USER_ADDR" "$USER_KEY" "$COLLATERAL"

# 2. Create broker
BROKER=$($SCRIPT_DIR/../actions/broker_create.sh "$USER_KEY" "USER_A_BROKER")

# 3. Deposit all to broker
$SCRIPT_DIR/../actions/deposit.sh "$BROKER" "$USER_KEY" "all"

# 4. Use 1:1 LP ratio (simpler, matching original lifecycle)
# The daemon will arbitrage price to parity with index
LP_WAUSDC=$LP_AMOUNT
LP_WRLP=$LP_AMOUNT

log_info "LP ratio: $LP_WAUSDC waUSDC : $LP_WRLP wRLP (1:1)"

# 5. Mint wRLP for LP (enough for LP + buffer)
MINT_AMOUNT=$((LP_WRLP + LP_WRLP / 10))  # 10% buffer
$SCRIPT_DIR/../actions/mint.sh "$BROKER" "$USER_KEY" "$MINT_AMOUNT"

# 6. Withdraw tokens for LP
$SCRIPT_DIR/../actions/withdraw_position.sh "$BROKER" "$USER_KEY" "$LP_WRLP"
$SCRIPT_DIR/../actions/withdraw_collateral.sh "$BROKER" "$USER_KEY" "$LP_WAUSDC"

# 7. Add LP
$SCRIPT_DIR/../actions/lp_add.sh "$USER_KEY" "$LP_WAUSDC" "$LP_WRLP"

log_success "LP Provider ready: $USER_ADDR"
