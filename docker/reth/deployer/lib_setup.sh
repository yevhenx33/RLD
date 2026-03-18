#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# lib_setup.sh — Reth-compatible helper functions
# ═══════════════════════════════════════════════════════════════
# Drop-in replacement for docker/deployer/lib_setup.sh
# Key difference: NO Anvil-specific RPCs (no impersonation,
# no setBalance, no evm_increaseTime).
#
# Funding strategy: The deployer key owns USDC directly
# (pre-loaded in genesis from the Anvil dump where the deployer
# was funded via whale impersonation). On Reth, the deployer
# simply sends USDC directly — no whale needed.
# ═══════════════════════════════════════════════════════════════

fund_user() {
    local ADDR=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))

    # On Reth, all accounts are pre-funded with ETH in genesis.
    # Just need to transfer USDC from deployer → SimFunder → user.

    # If SimFunder is deployed, use atomic path
    if [ -n "${SIM_FUNDER:-}" ]; then
        # Transfer USDC from deployer to SimFunder
        cast send "$USDC" "transfer(address,uint256)" "$SIM_FUNDER" "$AMOUNT_WEI" \
            --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null
        sleep 1

        # Atomic: USDC → Aave supply → wrap → transfer waUSDC to user
        cast send "$SIM_FUNDER" "fund(address,uint256)" "$ADDR" "$AMOUNT_WEI" \
            --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null
    else
        # Fallback: direct transfer + manual Aave supply
        cast send "$USDC" "transfer(address,uint256)" "$ADDR" "$AMOUNT_WEI" \
            --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null
        sleep 1

        # User supplies to Aave themselves
        cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$AMOUNT_WEI" \
            --private-key "$KEY" --rpc-url "$RPC_URL" > /dev/null
        sleep 1
        cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
            "$USDC" "$AMOUNT_WEI" "$ADDR" 0 \
            --private-key "$KEY" --rpc-url "$RPC_URL" > /dev/null
        sleep 1

        # Wrap aUSDC → waUSDC
        local AUSDC_BAL=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
        cast send "$AUSDC" "approve(address,uint256)" "$WAUSDC" "$AUSDC_BAL" \
            --private-key "$KEY" --rpc-url "$RPC_URL" --gas-limit 150000 > /dev/null
        sleep 1
        cast send "$WAUSDC" "wrap(uint256)" "$AUSDC_BAL" \
            --private-key "$KEY" --rpc-url "$RPC_URL" --gas-limit 500000 > /dev/null
        sleep 1
    fi

    log_ok "Funded $ADDR with \$$AMOUNT_USD"
}

create_broker() {
    local KEY=$1
    local SALT=$(cast keccak "broker-$(date +%s)-$RANDOM")

    local BROKER=$(cast send "$BROKER_FACTORY_ADDR" "createBroker(bytes32)" "$SALT" \
        --private-key "$KEY" --rpc-url "$RPC_URL" --json 2>/dev/null \
        | jq -r '[.logs[]? | select(.topics[0] == "0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71")] | .[0].data // empty' 2>/dev/null \
        | head -1 \
        | python3 -c "
import sys
data = sys.stdin.readline().strip()
if data and data.startswith('0x') and len(data) >= 66:
    print('0x' + data[26:66])
else:
    print('')
")
    echo "$BROKER"
}

deposit_to_broker() {
    local BROKER=$1 KEY=$2 AMOUNT=$3
    local USER_ADDR=$(cast wallet address --private-key "$KEY" 2>/dev/null)

    if [ "$AMOUNT" = "all" ]; then
        AMOUNT=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
    else
        AMOUNT=$((AMOUNT * 1000000))
    fi

    cast send "$WAUSDC" "transfer(address,uint256)" "$BROKER" "$AMOUNT" \
        --private-key "$KEY" --rpc-url "$RPC_URL" > /dev/null
    sleep 1
}

mint_wrlp() {
    local BROKER=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))
    cast send "$BROKER" "modifyPosition(bytes32,int256,int256)" \
        "$MARKET_ID" 0 "$AMOUNT_WEI" \
        --private-key "$KEY" --rpc-url "$RPC_URL" > /dev/null
    sleep 1
}

withdraw_position() {
    local BROKER=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))
    local USER_ADDR=$(cast wallet address --private-key "$KEY" 2>/dev/null)
    cast send "$BROKER" "withdrawPositionToken(address,uint256)" "$USER_ADDR" "$AMOUNT_WEI" \
        --private-key "$KEY" --rpc-url "$RPC_URL" > /dev/null
    sleep 1
}

withdraw_collateral() {
    local BROKER=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))
    local USER_ADDR=$(cast wallet address --private-key "$KEY" 2>/dev/null)
    cast send "$BROKER" "withdrawCollateral(address,uint256)" "$USER_ADDR" "$AMOUNT_WEI" \
        --private-key "$KEY" --rpc-url "$RPC_URL" > /dev/null
    sleep 1
}

prime_oracle() {
    # On Reth: no evm_increaseTime. Instead, just wait for real blocks.
    # With --dev.block-time 12, we need ~600 blocks for 2 hours.
    # For deployment speed, we send 10 no-op transactions to advance blocks.
    log_step "" "Advancing blocks for oracle priming..."
    local DEPLOYER_ADDR=$(cast wallet address --private-key "$DEPLOYER_KEY" 2>/dev/null)
    for i in $(seq 1 10); do
        # Self-transfer of 0 ETH to produce a block
        cast send "$DEPLOYER_ADDR" --value 0 \
            --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
    done
    # Wait a few block times for timestamps to advance
    sleep 5
}
