#!/bin/bash
# Phase 4: Finalize — Write deployment.json, reset indexer, verify immutables
# This MUST run before Phase 5 (users/brokers) so the indexer is watching.

log_phase "4" "WRITE DEPLOYMENT CONFIG & RESET INDEXER"

# ─── Capture deploy block + timestamp before switching to interval mining ──
FORK_BLOCK="${FORK_BLOCK:-24660000}"
DEPLOY_BLOCK=$(cast block-number --rpc-url "$RPC_URL" 2>/dev/null || echo "0")
DEPLOY_TIMESTAMP=$(cast block latest --rpc-url "$RPC_URL" --field timestamp 2>/dev/null || echo "0")

mkdir -p /config

cat > /config/deployment.json << EOF
{
    "fork_block": $FORK_BLOCK,
    "deploy_block": $DEPLOY_BLOCK,
    "deploy_timestamp": $DEPLOY_TIMESTAMP,
    "rpc_url": "$RPC_URL",
    "rld_core": "$RLD_CORE",
    "twamm_hook": "$TWAMM_HOOK",
    "market_id": "$MARKET_ID",
    "mock_oracle": "$MOCK_ORACLE",
    "broker_router": "$BROKER_ROUTER",
    "wausdc": "$WAUSDC",
    "position_token": "$POSITION_TOKEN",
    "broker_factory": "$BROKER_FACTORY_ADDR",
    "swap_router": "$SWAP_ROUTER",
    "bond_factory": "$BOND_FACTORY",
    "basis_trade_factory": "$BASIS_TRADE_FACTORY",
    "broker_executor": "$BROKER_EXECUTOR",
    "pool_manager": "$POOL_MANAGER",
    "pool_id": "$POOL_ID",
    "v4_quoter": "$V4_QUOTER",
    "v4_position_manager": "$V4_POSITION_MANAGER",
    "v4_position_descriptor": "$V4_POSITION_DESCRIPTOR",
    "v4_state_view": "$V4_STATE_VIEW",
    "universal_router": "$UNIVERSAL_ROUTER",
    "permit2": "$PERMIT2",
    "token0": "$TOKEN0",
    "token1": "$TOKEN1",
    "zero_for_one_long": $ZERO_FOR_ONE_LONG,
    "external_contracts": {
        "usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "ausdc": "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
        "aave_pool": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
        "susde": "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497",
        "usdc_whale": "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"
    }
}
EOF

log_ok "Written /config/deployment.json"
cat /config/deployment.json | python3 -m json.tool

log_ok "Written /config/deployment.json (fork_block=${FORK_BLOCK}, deploy_block=${DEPLOY_BLOCK})"

# ─── Signal indexer to reset ──────────────────────────────────
log_step "4.1" "Resetting indexer with new deployment config..."
for i in $(seq 1 10); do
    RESET_RESULT=$(curl -s -X POST http://indexer:8080/admin/reset \
        -H "Content-Type: application/json" \
        -d @/config/deployment.json 2>/dev/null) && break
    echo "  Waiting for indexer API ($i/10)..."
    sleep 3
done
if echo "$RESET_RESULT" | grep -q '"ok"'; then
    log_ok "Indexer reset: $RESET_RESULT"
else
    log_info "Indexer reset skipped (non-critical): ${RESET_RESULT:-timeout}"
fi

sync_timestamp
log_ok "Timestamp synchronized after deployment"

# ─── Verify factory immutables (Poka-Yoke) ────────────────────
log_step "4.2" "Verifying factory immutables..."
VERIFY_FAILED=false

if [ -n "$BOND_FACTORY" ]; then
    BF_COLLATERAL=$(cast call "$BOND_FACTORY" "COLLATERAL()(address)" --rpc-url "$RPC_URL" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    BF_TWAMM=$(cast call "$BOND_FACTORY" "TWAMM_HOOK()(address)" --rpc-url "$RPC_URL" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    BF_ROUTER=$(cast call "$BOND_FACTORY" "ROUTER()(address)" --rpc-url "$RPC_URL" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    EXPECTED_WAUSDC=$(echo "$WAUSDC" | tr '[:upper:]' '[:lower:]')
    EXPECTED_TWAMM=$(echo "$TWAMM_HOOK" | tr '[:upper:]' '[:lower:]')
    EXPECTED_ROUTER=$(echo "$BROKER_ROUTER" | tr '[:upper:]' '[:lower:]')

    [ "$BF_COLLATERAL" != "$EXPECTED_WAUSDC" ] && echo -e "${RED}  ✗ BondFactory.COLLATERAL mismatch${NC}" && VERIFY_FAILED=true
    [ "$BF_TWAMM" != "$EXPECTED_TWAMM" ] && echo -e "${RED}  ✗ BondFactory.TWAMM_HOOK mismatch${NC}" && VERIFY_FAILED=true
    [ "$BF_ROUTER" != "$EXPECTED_ROUTER" ] && echo -e "${RED}  ✗ BondFactory.ROUTER mismatch${NC}" && VERIFY_FAILED=true
    [ "$VERIFY_FAILED" = false ] && log_ok "BondFactory immutables verified ✓"
else
    log_info "BondFactory not deployed — skipping verification"
fi

if [ -n "$BASIS_TRADE_FACTORY" ]; then
    BTF_COLLATERAL=$(cast call "$BASIS_TRADE_FACTORY" "COLLATERAL()(address)" --rpc-url "$RPC_URL" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    BTF_TWAMM=$(cast call "$BASIS_TRADE_FACTORY" "TWAMM_HOOK()(address)" --rpc-url "$RPC_URL" 2>/dev/null | tr '[:upper:]' '[:lower:]')
    [ "$BTF_COLLATERAL" != "$EXPECTED_WAUSDC" ] && echo -e "${RED}  ✗ BasisTradeFactory.COLLATERAL mismatch${NC}" && VERIFY_FAILED=true
    [ "$BTF_TWAMM" != "$EXPECTED_TWAMM" ] && echo -e "${RED}  ✗ BasisTradeFactory.TWAMM_HOOK mismatch${NC}" && VERIFY_FAILED=true
    [ "$VERIFY_FAILED" = false ] && log_ok "BasisTradeFactory immutables verified ✓"
else
    log_info "BasisTradeFactory not deployed — skipping verification"
fi

[ "$VERIFY_FAILED" = true ] && log_err "FATAL: Factory immutables mismatch — deployment inconsistent!"

# ─── Switch to interval mining ─────────────────────────────────
cast rpc evm_setAutomine false --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
cast rpc evm_setIntervalMining 1 --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
log_ok "Interval mining restored (1s blocks)"

log_ok "✅ Indexer is now watching — Phase 5 (users/brokers) will be captured live"
