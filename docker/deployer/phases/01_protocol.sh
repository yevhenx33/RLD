#!/bin/bash
# Phase 1: Deploy Protocol (RLDCore, TWAMM, Oracle, BrokerRouter)
# Variables set: TWAMM_HOOK, FACTORY, RLD_CORE, BROKER_ROUTER, MOCK_ORACLE, BROKER_FACTORY_ADDR

log_phase "1" "DEPLOY PROTOCOL"

cd /workspace/contracts

log_step "1.1" "Deploying RLD Protocol..."
DEPLOY_OUTPUT=$(forge script script/DeployRLDProtocol.s.sol --tc DeployRLDProtocol \
    --rpc-url "$RPC_URL" --broadcast --code-size-limit 99999 -v 2>&1) || true

if ! echo "$DEPLOY_OUTPUT" | grep -q "DEPLOYMENT COMPLETE"; then
    echo "$DEPLOY_OUTPUT"
    log_err "Protocol deployment failed"
fi

TWAMM_HOOK=$(jq -r '.TWAMM' deployments.json)
FACTORY=$(jq -r '.RLDMarketFactory' deployments.json)
RLD_CORE=$(jq -r '.RLDCore' deployments.json)
BROKER_ROUTER=$(jq -r '.BrokerRouter' deployments.json)
BROKER_FACTORY_ADDR=""  # Comes from market deploy

log_ok "Protocol deployed"
echo "  RLDCore:       $RLD_CORE"
echo "  TWAMM Hook:    $TWAMM_HOOK"
echo "  Factory:       $FACTORY"
echo "  BrokerRouter:  $BROKER_ROUTER"

# ─── MockRLDAaveOracle (for simulation) ───────────────────────
log_step "1.2" "Deploying MockRLDAaveOracle..."
MOCK_ORACLE_OUTPUT=$(forge create test/mocks/MockRLDAaveOracle.sol:MockRLDAaveOracle \
    --rpc-url "$RPC_URL" --private-key "$DEPLOYER_KEY" --broadcast 2>&1) || true
MOCK_ORACLE=$(echo "$MOCK_ORACLE_OUTPUT" | grep "Deployed to:" | awk '{print $3}')
if [ -z "$MOCK_ORACLE" ]; then
    echo "$MOCK_ORACLE_OUTPUT"
    log_err "MockRLDAaveOracle deployment failed"
fi
log_ok "MockRLDAaveOracle: $MOCK_ORACLE (admin-settable rate for simulation)"

# --- Sync Initial Rate ---
log_step "1.3" "Syncing initial oracle rate from Envio GraphQL..."
RATE_APY=$(curl -sf "http://host.docker.internal:5000/graphql" \
  -H "Content-Type: application/json" \
  --data '{"query":"{ historicalRates(symbols:[\"USDC\"], resolution:\"1H\", limit:1){ apy } }"}' \
  | jq -r '.data.historicalRates[0].apy' || echo "")

if [ -n "$RATE_APY" ] && [ "$RATE_APY" != "null" ]; then
    # Normalize APY to fraction, then convert to RAY.
    RATE_RAY=$(python3 -c "r=float('${RATE_APY}'); r=r/100.0 if r>1 else r; print(int(r*1e27))")
    
    log_ok "Fetched live rate fraction: $RATE_APY (RAY: $RATE_RAY)"
    cast send "$MOCK_ORACLE" "setRate(uint256)" "$RATE_RAY" \
        --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null 2>&1
    log_ok "MockRLDAaveOracle initialized with live rate"
else
    log_info "Envio GraphQL unreachable or returned null. MockRLDAaveOracle will use default 5%."
fi
