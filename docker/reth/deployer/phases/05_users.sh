#!/bin/bash
# Phase 5 (Reth): Setup Users, Brokers, LP
# Identical to Anvil version except:
#   - No anvil_setBalance / anvil_impersonateAccount
#   - No evm_setAutomine / evm_setIntervalMining
#   - Uses Reth-compatible fund_user() from lib_setup.sh

if [ "${SKIP_USER_SETUP:-true}" != "true" ]; then
log_phase "5" "SETUP USERS (Reth mode)"

# Reth --dev mines blocks automatically. No auto-mine toggle needed.
log_ok "Reth dev mode: blocks mine automatically"

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
export SIM_FUNDER

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

# Redefine deposit/mint/withdraw to use safe_send (from Reth lib_setup.sh)
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

# Reth dev mode handles block production automatically — no mining mode changes needed.
log_ok "Phase 5 complete (Reth mode)"

else
    log_ok "Phase 5 skipped (SKIP_USER_SETUP=true — contracts-only mode)"
fi
