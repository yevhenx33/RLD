#!/bin/bash
# Phase 2 (Reth): Deploy Market — identical to Anvil version except oracle priming
# Variables set: WAUSDC, POSITION_TOKEN, BROKER_FACTORY_ADDR, MARKET_ID,
#                TOKEN0, TOKEN1, ZERO_FOR_ONE_LONG, POOL_ID

log_phase "2" "DEPLOY WRAPPED MARKET"

cd /workspace/contracts

log_step "2.1" "Deploying wrapped market..."
MARKET_OUTPUT=$(USE_MOCK_ORACLE=true MOCK_ORACLE="$MOCK_ORACLE" \
    forge script script/DeployWrappedMarket.s.sol --tc DeployWrappedMarket \
    --rpc-url "$RPC_URL" --broadcast --code-size-limit 99999 -v 2>&1) || true

if ! echo "$MARKET_OUTPUT" | grep -q "WRAPPED MARKET CREATED"; then
    echo "$MARKET_OUTPUT"
    log_err "Market deployment failed"
fi

# Extract addresses
WAUSDC=$(echo "$MARKET_OUTPUT" | grep -i "waUSDC deployed:" | awk '{print $NF}')
[ -z "$WAUSDC" ] && WAUSDC=$(echo "$MARKET_OUTPUT" | grep "collateralToken (waUSDC):" | awk '{print $NF}')
MARKET_ID=$(echo "$MARKET_OUTPUT" | grep "MarketId:" | awk '{print $NF}')
POSITION_TOKEN=$(echo "$MARKET_OUTPUT" | grep "positionToken (wRLP):" | awk '{print $NF}')
BROKER_FACTORY_ADDR=$(echo "$MARKET_OUTPUT" | grep "BrokerFactory:" | awk '{print $NF}')

[ -z "$WAUSDC" ] || [ -z "$POSITION_TOKEN" ] || [ -z "$BROKER_FACTORY_ADDR" ] && log_err "Failed to extract market addresses"

log_ok "Wrapped market deployed"
echo "  waUSDC:         $WAUSDC"
echo "  wRLP:           $POSITION_TOKEN"
echo "  BrokerFactory:  $BROKER_FACTORY_ADDR"
echo "  MarketId:       $MARKET_ID"

# Token order
WAUSDC_LOWER=$(echo "$WAUSDC" | tr '[:upper:]' '[:lower:]')
POSITION_TOKEN_LOWER=$(echo "$POSITION_TOKEN" | tr '[:upper:]' '[:lower:]')
if [[ "$WAUSDC_LOWER" < "$POSITION_TOKEN_LOWER" ]]; then
    TOKEN0="$WAUSDC"; TOKEN1="$POSITION_TOKEN"; ZERO_FOR_ONE_LONG=true
else
    TOKEN0="$POSITION_TOKEN"; TOKEN1="$WAUSDC"; ZERO_FOR_ONE_LONG=false
fi
log_ok "Token order: TOKEN0=$TOKEN0"

# Compute PoolId
POOL_ID=$(cast keccak "$(cast abi-encode "x(address,address,uint24,int24,address)" "$TOKEN0" "$TOKEN1" 500 5 "$TWAMM_HOOK")")
log_ok "PoolId: $POOL_ID"

# ─── Prime TWAMM oracle ──────────────────────────────────────
log_step "2.2" "Priming TWAMM oracle & growing cardinality..."
cast send "$TWAMM_HOOK" "increaseCardinality(bytes32,uint16)" "$POOL_ID" 65535 \
    --private-key $DEPLOYER_KEY --rpc-url $RPC_URL > /dev/null 2>&1
log_ok "Oracle cardinality grown to 65535"

# Reth: No evm_increaseTime. Send no-op txs to advance blocks + produce observations.
WARMUP_OK=0
DEPLOYER_ADDR=$(cast wallet address --private-key "$DEPLOYER_KEY" 2>/dev/null)
for i in $(seq 1 10); do
    # Advance blocks with self-transfer
    cast send "$DEPLOYER_ADDR" --value 0 \
        --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
    # Wait for block time to produce separation
    sleep 2
    if cast send "$TWAMM_HOOK" \
        "executeJTMOrders((address,address,uint24,int24,address))" \
        "($TOKEN0,$TOKEN1,500,5,$TWAMM_HOOK)" \
        --private-key $DEPLOYER_KEY --rpc-url $RPC_URL > /dev/null 2>&1; then
        WARMUP_OK=$((WARMUP_OK + 1))
    fi
done
log_ok "Oracle warmed up ($WARMUP_OK/10 observations written)"

# ─── Configure BrokerRouter deposit route ──────────────────────
log_step "2.3" "Configuring BrokerRouter deposit route (USDC → aUSDC → waUSDC)..."
cast send "$BROKER_ROUTER" \
    "setDepositRoute(address,(address,address,address,address))" \
    "$WAUSDC" \
    "($USDC,$AUSDC,$WAUSDC,$AAVE_POOL)" \
    --private-key $DEPLOYER_KEY --rpc-url $RPC_URL > /dev/null 2>&1
log_ok "Deposit route configured: USDC → aUSDC → waUSDC"

# ─── Helper: sync EVM timestamp (no-op on Reth) ──────────────
sync_timestamp() {
    # Reth dev mode doesn't have Anvil's timestamp desync bug.
    # This is a no-op kept for interface compatibility.
    log_step "" "Timestamp sync: skipped (Reth mode)"
}
