#!/bin/bash
# Setup Chaotic Trader
# Usage: chaos_trader.sh <USER_KEY> <CAPITAL_DOLLARS>
#
# Example: chaos_trader.sh $CHAOS_KEY 10000000

set -e
SCRIPT_DIR="$(dirname "$(realpath "$0")")"
source "$SCRIPT_DIR/../utils/load_env.sh"
source "$SCRIPT_DIR/../utils/colors.sh"

USER_KEY=$1
CAPITAL=${2:-10000000}

if [ -z "$USER_KEY" ]; then
    echo "Usage: chaos_trader.sh <USER_KEY> [CAPITAL_DOLLARS]"
    exit 1
fi

USER_ADDR=$(cast wallet address --private-key "$USER_KEY" 2>/dev/null)
log_header "Chaos Trader Setup: $USER_ADDR"

# 1. Fund user
$SCRIPT_DIR/../actions/fund.sh "$USER_ADDR" "$USER_KEY" "$CAPITAL"

# 2. Create broker
BROKER=$($SCRIPT_DIR/../actions/broker_create.sh "$USER_KEY" "CHAOS_BROKER")

# 3. Split capital: keep half in wallet (for waUSDC trades), deposit half to broker (for wRLP minting)
DEPOSIT_AMOUNT=$((CAPITAL / 2))
WALLET_AMOUNT=$((CAPITAL - DEPOSIT_AMOUNT))
log_info "Splitting: $DEPOSIT_AMOUNT to broker, $WALLET_AMOUNT in wallet"

$SCRIPT_DIR/../actions/deposit.sh "$BROKER" "$USER_KEY" "$DEPOSIT_AMOUNT"

# 4. Prime TWAP oracle (advance time for broker)
log_step "4" "Priming TWAP oracle for Chaos broker..."
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null

# 5. Mint wRLP ($1M worth, constrained by collateral)
WRLP_PRICE_WAD=$(cast call "$MOCK_ORACLE" "getIndexPrice(address,address)(uint256)" \
    "0x0000000000000000000000000000000000000000" "0x0000000000000000000000000000000000000000" \
    --rpc-url "$RPC_URL" | awk '{print $1}')

WRLP_MINT=$(python3 -c "
price_wad = $WRLP_PRICE_WAD
target_usd = 1000000  # $1M worth of wRLP
tokens = int(target_usd * 1e18 / price_wad)
print(tokens)
")

log_step "5" "Minting $WRLP_MINT wRLP for trading..."
$SCRIPT_DIR/../actions/mint.sh "$BROKER" "$USER_KEY" "$WRLP_MINT"

# 6. Withdraw wRLP to wallet for daemon to use
log_step "6" "Withdrawing wRLP to wallet for trading..."
$SCRIPT_DIR/../actions/withdraw_position.sh "$BROKER" "$USER_KEY" "$WRLP_MINT"

log_success "Chaos Trader ready: $USER_ADDR (wRLP: $WRLP_MINT, waUSDC: $WALLET_AMOUNT)"
