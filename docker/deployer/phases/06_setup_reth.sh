#!/bin/bash
# Phase 5: Setup Users, Brokers, LP (runs AFTER indexer is watching)
# All BrokerCreated events are captured live by the indexer.

if [ "${SKIP_USER_SETUP:-true}" != "true" ]; then
log_phase "5" "SETUP USERS (indexer capturing events)"

# Switch to auto-mine for fast tx confirmations (Phase 4 set interval mining)
cast rpc evm_setAutomine true --rpc-url "$RPC_URL" > /dev/null
log_ok "Auto-mine enabled for Phase 5 (fast tx confirmations)"

# Validate user keys
for VAR in USER_A_KEY USER_B_KEY USER_C_KEY MM_KEY CHAOS_KEY; do
    if [ -z "${!VAR}" ]; then
        log_err "$VAR not set (required for user setup)"
    fi
done

# ─── Deploy SimFunder (atomic USDC→Aave→waUSDC) ───────────────
log_step "5.0" "Deploying SimFunder (atomic user funder)..."
cd /workspace/contracts
SIM_FUNDER=$(forge create src/periphery/SimFunder.sol:SimFunder \
    --private-key $DEPLOYER_KEY \
    --rpc-url $RPC_URL \
    --broadcast \
    --constructor-args \
        $USDC \
        $AUSDC \
        $WAUSDC \
        $AAVE_POOL \
    2>&1 | grep "Deployed to:" | awk '{print $3}')

if [ -z "$SIM_FUNDER" ]; then
    log_err "SimFunder deployment failed"
fi
log_ok "SimFunder: $SIM_FUNDER"

# ─── Helper: atomic fund via SimFunder ─────────────────────────
fund_user() {
    local ADDR=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))

    # Set ETH balance for user
    cast rpc anvil_setBalance "$ADDR" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null

    # Impersonate whale → send USDC to SimFunder → call fund()
    cast rpc anvil_setBalance "$USDC_WHALE" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
    cast send "$USDC" "transfer(address,uint256)" "$SIM_FUNDER" "$AMOUNT_WEI" \
        --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

    # Atomic: USDC → Aave supply → wrap → transfer waUSDC to user
    cast send "$SIM_FUNDER" "fund(address,uint256)" "$ADDR" "$AMOUNT_WEI" \
        --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null

    log_ok "Funded $ADDR with \$$AMOUNT_USD (atomic)"
}

# ─── safe_send: fail-loud cast send wrapper ────────────────────
safe_send() {
    local LABEL=$1; shift
    local OUTPUT
    OUTPUT=$(timeout 30s cast send --json "$@" 2>&1)
    local EXIT_CODE=$?
    if [ $EXIT_CODE -ne 0 ]; then
        echo ""
        echo "  ╔══════════════════════════════════════════════════╗"
        echo "  ║  ✗✗✗ TX SEND FAILED: $LABEL"
        echo "  ╚══════════════════════════════════════════════════╝"
        echo "$OUTPUT" | tail -5
        log_err "FATAL: $LABEL SEND FAILED (exit=$EXIT_CODE)"
    fi
    # Check on-chain receipt status (0x0 = revert)
    local STATUS=$(echo "$OUTPUT" | jq -r '.status // "0x1"' 2>/dev/null)
    if [ "$STATUS" = "0x0" ] || [ "$STATUS" = "0" ]; then
        echo ""
        echo "  ╔══════════════════════════════════════════════════╗"
        echo "  ║  ✗✗✗ TX REVERTED ON-CHAIN: $LABEL"
        echo "  ╚══════════════════════════════════════════════════╝"
        echo "$OUTPUT" | jq '{status, transactionHash, gasUsed}' 2>/dev/null || echo "$OUTPUT" | tail -5
        log_err "FATAL: $LABEL REVERTED (status=$STATUS)"
    fi
    sleep 1
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
    [ -z "$BROKER" ] && log_err "Failed to create broker"
    sleep 1
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

    safe_send "deposit_to_broker($AMOUNT)" \
        "$WAUSDC" "transfer(address,uint256)" "$BROKER" "$AMOUNT" \
        --private-key "$KEY" --rpc-url "$RPC_URL"
}

mint_wrlp() {
    local BROKER=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))
    safe_send "mint_wrlp($AMOUNT_USD)" \
        "$BROKER" "modifyPosition(bytes32,int256,int256)" \
        "$MARKET_ID" 0 "$AMOUNT_WEI" \
        --private-key "$KEY" --rpc-url "$RPC_URL"
}

withdraw_position() {
    local BROKER=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))
    local USER_ADDR=$(cast wallet address --private-key "$KEY" 2>/dev/null)
    safe_send "withdraw_position($AMOUNT_USD)" \
        "$BROKER" "withdrawPositionToken(address,uint256)" "$USER_ADDR" "$AMOUNT_WEI" \
        --private-key "$KEY" --rpc-url "$RPC_URL"
}

withdraw_collateral() {
    local BROKER=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))
    local USER_ADDR=$(cast wallet address --private-key "$KEY" 2>/dev/null)
    safe_send "withdraw_collateral($AMOUNT_USD)" \
        "$BROKER" "withdrawCollateral(address,uint256)" "$USER_ADDR" "$AMOUNT_WEI" \
        --private-key "$KEY" --rpc-url "$RPC_URL"
}

prime_oracle() {
    cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null
}

# ─── User A: LP Provider ($100M collateral, $5M LP) ───────────
log_step "5.1" "Setting up LP Provider (User A)..."
USER_A_ADDR=$(cast wallet address --private-key "$USER_A_KEY" 2>/dev/null)
fund_user "$USER_A_ADDR" "$USER_A_KEY" 100000000

# Balance trace helper
trace_bal() {
    local LABEL=$1 TOKEN=$2 ADDR=$3
    local BAL
    BAL=$(cast call "$TOKEN" "balanceOf(address)(uint256)" "$ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
    echo "    $LABEL=$BAL"
}

echo "  [TRACE] After fund_user:"
trace_bal "waUSDC" "$WAUSDC" "$USER_A_ADDR"

USER_A_BROKER=$(create_broker "$USER_A_KEY")
log_ok "User A broker: $USER_A_BROKER"

deposit_to_broker "$USER_A_BROKER" "$USER_A_KEY" "all"
echo "  [TRACE] After deposit_to_broker:"
trace_bal "User_waUSDC" "$WAUSDC" "$USER_A_ADDR"
trace_bal "Broker_waUSDC" "$WAUSDC" "$USER_A_BROKER"

# Oracle needs time-weighted data before minting
prime_oracle

mint_wrlp "$USER_A_BROKER" "$USER_A_KEY" 5500000
echo "  [TRACE] After mint_wrlp:"
trace_bal "Broker_waUSDC" "$WAUSDC" "$USER_A_BROKER"

withdraw_position "$USER_A_BROKER" "$USER_A_KEY" 5000000
echo "  [TRACE] After withdraw_position:"
trace_bal "User_wRLP" "$POSITION_TOKEN" "$USER_A_ADDR"

withdraw_collateral "$USER_A_BROKER" "$USER_A_KEY" 5000000
echo "  [TRACE] After withdraw_collateral:"
trace_bal "User_waUSDC" "$WAUSDC" "$USER_A_ADDR"
trace_bal "User_wRLP" "$POSITION_TOKEN" "$USER_A_ADDR"

# ─── V4 LP ─────────────────────────────────────────────────────
log_step "5.1b" "Adding V4 LP..."
LP_WEI=$((5000000 * 1000000))
MAX_UINT=$(python3 -c 'print(2**160-1)')
MAX_UINT48=$(python3 -c 'print(2**48-1)')

cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$(python3 -c 'print(2**256-1)')" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$(python3 -c 'print(2**256-1)')" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WAUSDC" "$V4_POSITION_MANAGER" "$MAX_UINT" "$MAX_UINT48" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$POSITION_TOKEN" "$V4_POSITION_MANAGER" "$MAX_UINT" "$MAX_UINT48" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null

cd /workspace/contracts
AUSDC_AMOUNT=$LP_WEI WRLP_AMOUNT=$LP_WEI PRIVATE_KEY=$USER_A_KEY \
    WAUSDC=$WAUSDC POSITION_TOKEN=$POSITION_TOKEN TWAMM_HOOK=$TWAMM_HOOK \
    TICK_SPACING=5 POOL_FEE=500 \
    forge script script/AddLiquidityWrapped.s.sol --tc AddLiquidityWrappedScript \
    --rpc-url "$RPC_URL" --broadcast --code-size-limit 99999 -v > /tmp/lp_output.log 2>&1 || true

USER_A_WAUSDC=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_A_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
USER_A_WRLP=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_A_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
log_step "5.1b" "User A: waUSDC=${USER_A_WAUSDC} wRLP=${USER_A_WRLP}"
grep -E "sqrtPrice|Amount:|liquidity|Tick|range|waUSDC|wRLP|currency|Error|revert|fail" /tmp/lp_output.log || true
log_ok "LP setup complete"

# ─── User B: Long User ($100k) ────────────────────────────────
log_step "5.2" "Setting up Long User (User B)..."
USER_B_ADDR=$(cast wallet address --private-key "$USER_B_KEY" 2>/dev/null)
fund_user "$USER_B_ADDR" "$USER_B_KEY" 100000

WAUSDC_BAL_B=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_B_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
cd /workspace/contracts
TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    SWAP_AMOUNT="$WAUSDC_BAL_B" ZERO_FOR_ONE="$ZERO_FOR_ONE_LONG" \
    SWAP_USER_KEY="$USER_B_KEY" \
    forge script script/LifecycleSwap.s.sol --tc LifecycleSwap \
    --rpc-url "$RPC_URL" --broadcast -v > /dev/null 2>&1 || true
log_ok "Long user ready"

# ─── User C: TWAMM User ($100k, funded) ───────────────────────
log_step "5.3" "Setting up TWAMM User (User C)..."
USER_C_ADDR=$(cast wallet address --private-key "$USER_C_KEY" 2>/dev/null)
fund_user "$USER_C_ADDR" "$USER_C_KEY" 100000
log_ok "TWAMM user funded (no order placed)"

# ─── MM Bot ($10M) ─────────────────────────────────────────────
log_step "5.4" "Setting up Market Maker..."
MM_ADDR=$(cast wallet address --private-key "$MM_KEY" 2>/dev/null)
fund_user "$MM_ADDR" "$MM_KEY" 10000000

MM_BROKER=$(create_broker "$MM_KEY")
log_ok "MM broker: $MM_BROKER"
deposit_to_broker "$MM_BROKER" "$MM_KEY" 6500000

prime_oracle
mint_wrlp "$MM_BROKER" "$MM_KEY" 1000000
withdraw_position "$MM_BROKER" "$MM_KEY" 1000000
log_ok "MM bot ready"

# ─── Chaos Trader ($10M) ──────────────────────────────────────
log_step "5.5" "Setting up Chaos Trader..."
CHAOS_ADDR=$(cast wallet address --private-key "$CHAOS_KEY" 2>/dev/null)
fund_user "$CHAOS_ADDR" "$CHAOS_KEY" 10000000

CHAOS_BROKER=$(create_broker "$CHAOS_KEY")
log_ok "Chaos broker: $CHAOS_BROKER"
deposit_to_broker "$CHAOS_BROKER" "$CHAOS_KEY" 5000000

prime_oracle

WRLP_PRICE_WAD=$(cast call "$MOCK_ORACLE" "getIndexPrice(address,address)(uint256)" \
    "$AAVE_POOL" "$USDC" \
    --rpc-url "$RPC_URL" | awk '{print $1}')
WRLP_MINT=$(python3 -c "
price_wad = $WRLP_PRICE_WAD
target_usd = 1000000
tokens = int(target_usd * 1e18 / price_wad)
print(tokens)
")
mint_wrlp "$CHAOS_BROKER" "$CHAOS_KEY" "$WRLP_MINT"
withdraw_position "$CHAOS_BROKER" "$CHAOS_KEY" "$WRLP_MINT"
log_ok "Chaos trader ready"

# Restore interval mining after all txns are done
cast rpc evm_setAutomine false --rpc-url "$RPC_URL" > /dev/null
cast rpc evm_setIntervalMining 1 --rpc-url "$RPC_URL" > /dev/null
log_ok "Interval mining restored (1s blocks)"

else
    log_ok "Phase 5 skipped (SKIP_USER_SETUP=true — contracts-only mode)"
fi
