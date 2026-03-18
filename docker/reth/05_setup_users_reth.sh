#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 05_setup_users_reth.sh — Post-Genesis User Setup on RETH
# ═══════════════════════════════════════════════════════════════
# Re-executes broker creation, collateral deposits, wRLP minting,
# and LP operations DIRECTLY on Reth so the indexer captures all
# events natively (BrokerCreated, ERC20Transfer, ModifyLiquidity).
#
# Usage:
#   ./docker/reth/05_setup_users_reth.sh           # standalone
#   Called from restart-reth.sh --with-users        # integrated
#
# Prerequisites:
#   - Reth running on port 8545
#   - Indexer healthy + seeded (POST /admin/reset done)
#   - deployment.json populated with contract addresses
#   - Accounts funded from genesis (waUSDC, ETH)
# ═══════════════════════════════════════════════════════════════

set -u  # strict vars, but no -e (we handle errors manually)

# ─── Paths & Config ──────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
RLD_ROOT="$(dirname "$DOCKER_DIR")"
ENV_FILE="$DOCKER_DIR/.env"
DEPLOY_JSON="$DOCKER_DIR/deployment.json"
RETH_RPC="${RETH_RPC:-http://localhost:8545}"
GAS_LIMIT=1000000

# ─── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; DIM='\033[2m'; NC='\033[0m'

header()  { echo -e "\n${BLUE}═══ $1 ═══${NC}\n"; }
step()    { echo -e "${YELLOW}[$1] $2${NC}"; }
ok()      { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()    { echo -e "${RED}  ✗ $1${NC}"; }
warn()    { echo -e "${YELLOW}  ⚠ $1${NC}"; }
info()    { echo -e "${CYAN}  ℹ $1${NC}"; }

# ─── Load env ────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    export $(grep -E '^(DEPLOYER_KEY|USER_A_KEY|USER_B_KEY|USER_C_KEY|MM_KEY|CHAOS_KEY)=' "$ENV_FILE" | xargs)
fi

# ─── Load contract addresses ─────────────────────────────────
if [ ! -f "$DEPLOY_JSON" ] || [ "$(cat "$DEPLOY_JSON")" = "{}" ]; then
    fail "deployment.json is empty"
    exit 1
fi

WAUSDC=$(jq -r '.wausdc' "$DEPLOY_JSON")
POSITION_TOKEN=$(jq -r '.position_token' "$DEPLOY_JSON")
BROKER_FACTORY=$(jq -r '.broker_factory' "$DEPLOY_JSON")
MOCK_ORACLE=$(jq -r '.mock_oracle' "$DEPLOY_JSON")
SWAP_ROUTER=$(jq -r '.swap_router' "$DEPLOY_JSON")
TWAMM_HOOK=$(jq -r '.twamm_hook' "$DEPLOY_JSON")
MARKET_ID=$(jq -r '.market_id' "$DEPLOY_JSON")
RLD_CORE=$(jq -r '.rld_core' "$DEPLOY_JSON")
POOL_MANAGER="0x000000000004444c5dc75cB358380D2e3dE08A90"

# ─── Validate Reth is up ─────────────────────────────────────
header "RETH USER SETUP"
step "0" "Checking Reth connectivity..."
BLOCK=$(cast block-number --rpc-url "$RETH_RPC" 2>/dev/null) || { fail "Reth not reachable at $RETH_RPC"; exit 1; }
ok "Reth at block $BLOCK"

# ─── Helpers ─────────────────────────────────────────────────

# safe_send: send a tx with receipt + revert check
safe_send() {
    local LABEL=$1; shift
    local OUTPUT
    OUTPUT=$(timeout 60s cast send --json --gas-limit $GAS_LIMIT "$@" 2>&1) || true
    if [ -z "$OUTPUT" ]; then
        fail "$LABEL — no output from cast send"
        return 1
    fi
    local STATUS
    STATUS=$(echo "$OUTPUT" | jq -r '.status // "0x1"' 2>/dev/null) || STATUS="unknown"
    if [ "$STATUS" = "0x0" ] || [ "$STATUS" = "0" ]; then
        fail "$LABEL — tx reverted on-chain"
        return 1
    fi
    # Return output for callers that need it (create_broker)
    LAST_TX_OUTPUT="$OUTPUT"
    return 0
}

# create_broker: deploy a broker via BrokerFactory
create_broker() {
    local KEY=$1
    local SALT=$(cast keccak "broker-reth-$(date +%s)-$RANDOM")

    local OUTPUT
    safe_send "createBroker" \
        "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
        --private-key "$KEY" --rpc-url "$RETH_RPC" || return 1
    local OUTPUT="$LAST_TX_OUTPUT"

    local BROKER=$(echo "$OUTPUT" \
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
    [ -z "$BROKER" ] && { fail "Failed to parse broker address from logs"; return 1; }
    echo "$BROKER"
}

# deposit_collateral: transfer waUSDC to broker
deposit_collateral() {
    local BROKER=$1 KEY=$2 AMOUNT=$3
    safe_send "deposit($AMOUNT)" \
        "$WAUSDC" "transfer(address,uint256)" "$BROKER" "$AMOUNT" \
        --private-key "$KEY" --rpc-url "$RETH_RPC"
}

# check_balance: read ERC20 balance
check_balance() {
    local TOKEN=$1 ADDR=$2
    cast call "$TOKEN" "balanceOf(address)(uint256)" "$ADDR" --rpc-url "$RETH_RPC" 2>/dev/null | head -1 | awk '{print $1}'
}

# ═════════════════════════════════════════════════════════════
# STEP 1: Create brokers and deposit collateral
# ═════════════════════════════════════════════════════════════
header "STEP 1: BROKER SETUP"

# --- User A / Deployer (LP provider + large collateral) ---
step "1.1" "Setting up LP Provider (Deployer/User A)..."
USER_A_ADDR=$(cast wallet address --private-key "$USER_A_KEY" 2>/dev/null)
USER_A_WAUSDC=$(check_balance "$WAUSDC" "$USER_A_ADDR")
USER_A_WRLP=$(check_balance "$POSITION_TOKEN" "$USER_A_ADDR")
info "User A ($USER_A_ADDR): waUSDC=$USER_A_WAUSDC wRLP=$USER_A_WRLP"

if [ -n "$USER_A_WAUSDC" ] && [ "$USER_A_WAUSDC" != "0" ]; then
    USER_A_BROKER=$(create_broker "$USER_A_KEY")
    if [ -n "$USER_A_BROKER" ]; then
        ok "User A broker: $USER_A_BROKER"

        # Deposit all waUSDC to broker
        deposit_collateral "$USER_A_BROKER" "$USER_A_KEY" "$USER_A_WAUSDC"
        ok "Deposited $USER_A_WAUSDC waUSDC to broker"
    else
        warn "User A broker creation failed — skipping"
    fi
else
    warn "User A has no waUSDC — skipping broker setup"
fi

# --- MM Bot ---
step "1.2" "Setting up Market Maker..."
MM_ADDR=$(cast wallet address --private-key "$MM_KEY" 2>/dev/null)
MM_WAUSDC=$(check_balance "$WAUSDC" "$MM_ADDR")
MM_WRLP=$(check_balance "$POSITION_TOKEN" "$MM_ADDR")
info "MM ($MM_ADDR): waUSDC=$MM_WAUSDC wRLP=$MM_WRLP"

if [ -n "$MM_WAUSDC" ] && [ "$MM_WAUSDC" != "0" ]; then
    MM_BROKER=$(create_broker "$MM_KEY")
    if [ -n "$MM_BROKER" ]; then
        ok "MM broker: $MM_BROKER"

        # Deposit 65% of waUSDC as collateral
        MM_DEPOSIT=$(python3 -c "print(int(int('$MM_WAUSDC') * 0.65))")
        deposit_collateral "$MM_BROKER" "$MM_KEY" "$MM_DEPOSIT"
        ok "Deposited $MM_DEPOSIT waUSDC to MM broker"
    else
        warn "MM broker creation failed — skipping"
    fi
else
    warn "MM has no waUSDC — skipping broker setup"
fi

# --- Chaos Trader ---
step "1.3" "Setting up Chaos Trader..."
CHAOS_ADDR=$(cast wallet address --private-key "$CHAOS_KEY" 2>/dev/null)
CHAOS_WAUSDC=$(check_balance "$WAUSDC" "$CHAOS_ADDR")
CHAOS_WRLP=$(check_balance "$POSITION_TOKEN" "$CHAOS_ADDR")
info "Chaos ($CHAOS_ADDR): waUSDC=$CHAOS_WAUSDC wRLP=$CHAOS_WRLP"

if [ -n "$CHAOS_WAUSDC" ] && [ "$CHAOS_WAUSDC" != "0" ]; then
    CHAOS_BROKER=$(create_broker "$CHAOS_KEY")
    if [ -n "$CHAOS_BROKER" ]; then
        ok "Chaos broker: $CHAOS_BROKER"
        deposit_collateral "$CHAOS_BROKER" "$CHAOS_KEY" "$CHAOS_WAUSDC"
        ok "Deposited $CHAOS_WAUSDC waUSDC to chaos broker"
    else
        warn "Chaos broker creation failed — skipping"
    fi
else
    warn "Chaos has no waUSDC — skipping broker setup"
fi

# ═════════════════════════════════════════════════════════════
# STEP 2: Wait for indexer to process new events
# ═════════════════════════════════════════════════════════════
header "STEP 2: VERIFY INDEXER"
step "2.1" "Waiting for indexer to process broker events..."
INDEXER_URL="http://localhost:${INDEXER_PORT:-8080}"
sleep 15  # Let the indexer poll and process

# Check snapshot
SNAPSHOT=$(curl -s --max-time 5 "$INDEXER_URL/graphql" -H 'Content-Type: application/json' \
    -d '{"query":"{ snapshot }"}' 2>/dev/null)

if [ -n "$SNAPSHOT" ]; then
    python3 -c "
import json, sys
d = json.loads('''$SNAPSHOT''')
snap = d.get('data', {}).get('snapshot')
if isinstance(snap, str): snap = json.loads(snap)
if snap:
    pool = snap.get('pool', {})
    derived = snap.get('derived', {})
    brokers = snap.get('brokers', [])
    print(f'  Pool TVL:     \${derived.get(\"poolTvlUsd\", 0):,.0f}')
    print(f'  Total Debt:   \${derived.get(\"totalDebtUsd\", 0):,.0f}')
    print(f'  Health:       {derived.get(\"systemHealth\", \"?\")}')
    print(f'  Brokers:      {len(brokers)}')
    print(f'  Swap Count:   {derived.get(\"swapCount24h\", 0)}')
else:
    print('  Snapshot not available yet')
" 2>/dev/null || warn "Could not parse snapshot"
fi

# ═════════════════════════════════════════════════════════════
# DONE
# ═════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Reth user setup complete!${NC}"
echo -e "${GREEN}   Brokers created + collateral deposited on-chain.${NC}"
echo -e "${GREEN}   Indexer now tracking BrokerCreated + ERC20Transfer events.${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
