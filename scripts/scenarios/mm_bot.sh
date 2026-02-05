#!/bin/bash
# Setup Market Maker Bot
# Usage: mm_bot.sh <USER_KEY> <CAPITAL_DOLLARS>
#
# Example: mm_bot.sh $MM_KEY 10000000

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
CAPITAL=${2:-10000000}
WRLP_MINT=${3:-1000000}  # 1M wRLP (constrained by collateral ratio)

if [ -z "$USER_KEY" ]; then
    echo "Usage: mm_bot.sh <USER_KEY> [CAPITAL_DOLLARS] [WRLP_MINT]"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)
log_header "MM Bot Setup: $USER_ADDR"

# 1. Fund user with waUSDC
$SCRIPT_DIR/../actions/fund.sh "$USER_ADDR" "$USER_KEY" "$CAPITAL"

# 2. Create broker
BROKER=$($SCRIPT_DIR/../actions/broker_create.sh "$USER_KEY" "MM_BROKER")

# 3. Calculate split: keep waUSDC in wallet, deposit only enough for wRLP minting
# At $4.2/wRLP and 1.5x collateral ratio, 1M wRLP = $4.2M debt, needs $6.3M collateral
# So deposit $6.5M for safety, keep $3.5M in wallet for trading
DEPOSIT_AMOUNT=6500000
WALLET_AMOUNT=$((CAPITAL - DEPOSIT_AMOUNT))
log_info "Splitting: $DEPOSIT_AMOUNT to broker (for wRLP), $WALLET_AMOUNT in wallet (for trading)"

$SCRIPT_DIR/../actions/deposit.sh "$BROKER" "$USER_KEY" "$DEPOSIT_AMOUNT"

# 4. Prime TWAP oracle (advance time for broker)
log_step "4" "Priming TWAP oracle for MM broker..."
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null

# 5. Mint wRLP
log_step "5" "Minting $WRLP_MINT wRLP for MM trading..."
$SCRIPT_DIR/../actions/mint.sh "$BROKER" "$USER_KEY" "$WRLP_MINT"

# 6. Withdraw wRLP to wallet for daemon to use
log_step "6" "Withdrawing wRLP to MM wallet for trading..."
$SCRIPT_DIR/../actions/withdraw_position.sh "$BROKER" "$USER_KEY" "$WRLP_MINT"

log_success "MM Bot ready: $USER_ADDR (wRLP: $WRLP_MINT, waUSDC: $WALLET_AMOUNT)"
