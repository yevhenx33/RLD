#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# RLD Master Deploy Script
# ═══════════════════════════════════════════════════════════════
# Consolidates all deployment phases into one script.
# Writes /config/deployment.json with all addresses when done.
#
# Required env vars:
#   RPC_URL, DEPLOYER_KEY, USER_A_KEY, USER_B_KEY, USER_C_KEY,
#   MM_KEY, CHAOS_KEY
#
# Optional env vars:
#   ETH_RPC_URL, API_URL, API_KEY
# ═══════════════════════════════════════════════════════════════

set -e
export FOUNDRY_DISABLE_NIGHTLY_WARNING=1

# ─── Colors ────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

log_phase() { echo -e "\n${BLUE}═══ PHASE $1: $2 ═══${NC}\n"; }
log_step()  { echo -e "${YELLOW}[$1] $2${NC}"; }
log_ok()    { echo -e "${GREEN}✓ $1${NC}"; }
log_err()   { echo -e "${RED}✗ $1${NC}"; exit 1; }
log_info()  { echo -e "${CYAN}ℹ $1${NC}"; }

# ─── Validate env ─────────────────────────────────────────────
RPC_URL=${RPC_URL:-"http://host.docker.internal:8545"}
PRIVATE_KEY=${DEPLOYER_KEY}

for VAR in DEPLOYER_KEY USER_A_KEY USER_B_KEY USER_C_KEY MM_KEY CHAOS_KEY; do
    if [ -z "${!VAR}" ]; then
        log_err "$VAR not set"
    fi
done

# ─── Mainnet constants ────────────────────────────────────────
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"

# ─── Official Uniswap V4 mainnet addresses (always available on mainnet fork) ──
POOL_MANAGER="0x000000000004444c5dc75cB358380D2e3dE08A90"
V4_POSITION_MANAGER="0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
V4_QUOTER="0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203"
V4_POSITION_DESCRIPTOR="0xd1428ba554f4c8450b763a0b2040a4935c63f06c"
V4_STATE_VIEW="0x7ffe42c4a5deea5b0fec41c94c136cf115597227"
UNIVERSAL_ROUTER="0x66a9893cc07d91d95644aedd05d03f95e1dba8af"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"

# ─── Wait for Anvil ───────────────────────────────────────────
log_phase "0" "WAITING FOR ANVIL"
for i in $(seq 1 60); do
    if cast block-number --rpc-url "$RPC_URL" > /dev/null 2>&1; then
        BLOCK=$(cast block-number --rpc-url "$RPC_URL")
        log_ok "Anvil reachable at block $BLOCK"
        break
    fi
    echo "  Waiting for $RPC_URL... ($i/60)"
    sleep 2
done
cast block-number --rpc-url "$RPC_URL" > /dev/null 2>&1 || log_err "Anvil not reachable at $RPC_URL"

# Switch Anvil to auto-mine mode for deployment (prevents nonce collisions)
cast rpc evm_setAutomine true --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
log_ok "Auto-mine enabled for deployment"

# ═══════════════════════════════════════════════════════════════
# PHASE 1: DEPLOY PROTOCOL
# ═══════════════════════════════════════════════════════════════
log_phase "1" "DEPLOY PROTOCOL"

cd /workspace/contracts

log_step "1.1" "Deploying RLD Protocol..."
DEPLOY_OUTPUT=$(forge script script/DeployRLDProtocol.s.sol --tc DeployRLDProtocol \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

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

# ─── Deploy MockOracle ─────────────────────────────────────────
log_step "1.2" "Deploying MockRLDAaveOracle..."
MOCK_ORACLE=$(forge create src/rld/modules/oracles/MockRLDAaveOracle.sol:MockRLDAaveOracle \
    --private-key $DEPLOYER_KEY \
    --rpc-url $RPC_URL \
    --broadcast 2>&1 | grep "Deployed to:" | awk '{print $3}')

[ -z "$MOCK_ORACLE" ] && log_err "Failed to deploy MockOracle"
log_ok "MockOracle: $MOCK_ORACLE"

# ─── Set initial rate ──────────────────────────────────────────
log_step "1.3" "Setting initial rate..."
APY="5.0"
API_URL_RATE="${API_URL:-http://host.docker.internal:8080}"
API_KEY_RATE="${API_KEY:-***REDACTED_API_KEY***}"
RATE_JSON=$(curl -s --max-time 5 "$API_URL_RATE/rates?limit=1&symbol=USDC" -H "X-API-Key: $API_KEY_RATE" 2>/dev/null) || true
API_APY=$(echo "$RATE_JSON" | jq -r '.[0].apy' 2>/dev/null)
if [ -n "$API_APY" ] && [ "$API_APY" != "null" ]; then
    APY="$API_APY"
else
    log_info "Rate API unavailable, using default 5%"
fi

RATE_RAY=$(python3 -c "print(int($APY / 100 * 1e27))")
cast send $MOCK_ORACLE "setRate(uint256)" $RATE_RAY \
    --private-key $DEPLOYER_KEY --rpc-url $RPC_URL > /dev/null 2>&1
log_ok "Rate set to ${APY}%"

# ═══════════════════════════════════════════════════════════════
# PHASE 2: DEPLOY MARKET
# ═══════════════════════════════════════════════════════════════
log_phase "2" "DEPLOY WRAPPED MARKET"

cd /workspace/contracts

log_step "2.1" "Deploying wrapped market..."
MARKET_OUTPUT=$(USE_MOCK_ORACLE=true MOCK_ORACLE=$MOCK_ORACLE \
    forge script script/DeployWrappedMarket.s.sol --tc DeployWrappedMarket \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

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

# Prime TWAMM oracle
log_step "2.2" "Priming TWAMM oracle..."
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null
log_ok "Oracle primed (2h advance)"

# ─── Configure BrokerRouter deposit route ──────────────────────
log_step "2.3" "Configuring BrokerRouter deposit route (USDC → aUSDC → waUSDC)..."

# BrokerRouter.setDepositRoute(collateralToken, (underlying, aToken, wrapped, aavePool))
cast send "$BROKER_ROUTER" \
    "setDepositRoute(address,(address,address,address,address))" \
    "$WAUSDC" \
    "($USDC,$AUSDC,$WAUSDC,$AAVE_POOL)" \
    --private-key $DEPLOYER_KEY --rpc-url $RPC_URL > /dev/null 2>&1

log_ok "Deposit route configured: USDC → aUSDC → waUSDC"

# ═══════════════════════════════════════════════════════════════
# PHASE 3: SETUP USERS
# ═══════════════════════════════════════════════════════════════
log_phase "3" "SETUP USERS"

# ─── Helper functions ──────────────────────────────────────────
fund_user() {
    local ADDR=$1 KEY=$2 AMOUNT_USD=$3
    local AMOUNT_WEI=$((AMOUNT_USD * 1000000))

    # Set ETH balance
    cast rpc anvil_setBalance "$USDC_WHALE" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_setBalance "$ADDR" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null

    # Transfer USDC from whale
    cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
    cast send "$USDC" "transfer(address,uint256)" "$ADDR" "$AMOUNT_WEI" \
        --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" > /dev/null
    sleep 1
    cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

    # Supply to Aave
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

    log_ok "Funded $ADDR with \$$AMOUNT_USD"
}

create_broker() {
    local KEY=$1
    local SALT=$(cast keccak "broker-$(date +%s)-$RANDOM")

    # cast send --json can produce multiple JSON documents; jq -s handles them
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
    cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null
}

# ─── User A: LP Provider ($100M collateral, $5M LP) ───────────
log_step "3.1" "Setting up LP Provider (User A)..."
USER_A_ADDR=$(cast wallet address --private-key "$USER_A_KEY" 2>/dev/null)
fund_user "$USER_A_ADDR" "$USER_A_KEY" 100000000

USER_A_BROKER=$(create_broker "$USER_A_KEY")
log_ok "User A broker: $USER_A_BROKER"

deposit_to_broker "$USER_A_BROKER" "$USER_A_KEY" "all"

# Mint wRLP for LP (5M + 10% buffer)
mint_wrlp "$USER_A_BROKER" "$USER_A_KEY" 5500000
withdraw_position "$USER_A_BROKER" "$USER_A_KEY" 5000000
withdraw_collateral "$USER_A_BROKER" "$USER_A_KEY" 5000000

# Add V4 LP
log_step "3.1b" "Adding V4 LP..."
LP_WEI=$((5000000 * 1000000))
MAX_UINT=$(python3 -c 'print(2**160-1)')
MAX_UINT48=$(python3 -c 'print(2**48-1)')

cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$LP_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$LP_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WAUSDC" "$V4_POSITION_MANAGER" "$MAX_UINT" "$MAX_UINT48" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$POSITION_TOKEN" "$V4_POSITION_MANAGER" "$MAX_UINT" "$MAX_UINT48" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null

cd /workspace/contracts
AUSDC_AMOUNT=$LP_WEI WRLP_AMOUNT=$LP_WEI \
    WAUSDC=$WAUSDC POSITION_TOKEN=$POSITION_TOKEN TWAMM_HOOK=$TWAMM_HOOK \
    forge script script/AddLiquidityWrapped.s.sol --tc AddLiquidityWrappedScript \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/lp_output.log 2>&1 || true

if grep -q "LP Position Created" /tmp/lp_output.log; then
    log_ok "LP position created"
else
    echo "  ⚠️  LP creation may have failed"
    tail -5 /tmp/lp_output.log
fi

# ─── User B: Long User ($100k) ────────────────────────────────
log_step "3.2" "Setting up Long User (User B)..."
USER_B_ADDR=$(cast wallet address --private-key "$USER_B_KEY" 2>/dev/null)
fund_user "$USER_B_ADDR" "$USER_B_KEY" 100000

# Swap waUSDC → wRLP (go long)
WAUSDC_BAL_B=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_B_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
cd /workspace/contracts
TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    SWAP_AMOUNT="$WAUSDC_BAL_B" ZERO_FOR_ONE="$ZERO_FOR_ONE_LONG" \
    SWAP_USER_KEY="$USER_B_KEY" \
    forge script script/LifecycleSwap.s.sol --tc LifecycleSwap \
    --rpc-url "$RPC_URL" --broadcast -v > /dev/null 2>&1 || true
log_ok "Long user ready"

# ─── User C: TWAMM Order ($100k, 1 hour) ──────────────────────
log_step "3.3" "Setting up TWAMM User (User C)..."
USER_C_ADDR=$(cast wallet address --private-key "$USER_C_KEY" 2>/dev/null)
fund_user "$USER_C_ADDR" "$USER_C_KEY" 100000

WAUSDC_BAL_C=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_C_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
cd /workspace/contracts
TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    ORDER_AMOUNT="$WAUSDC_BAL_C" DURATION_SECONDS=3600 \
    ZERO_FOR_ONE="$ZERO_FOR_ONE_LONG" TWAMM_USER_KEY="$USER_C_KEY" \
    forge script script/LifecycleTWAMM.s.sol --tc LifecycleTWAMM \
    --rpc-url "$RPC_URL" --broadcast -v > /dev/null 2>&1 || true
log_ok "TWAMM user ready"

# ─── MM Bot ($10M) ─────────────────────────────────────────────
log_step "3.4" "Setting up Market Maker..."
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
log_step "3.5" "Setting up Chaos Trader..."
CHAOS_ADDR=$(cast wallet address --private-key "$CHAOS_KEY" 2>/dev/null)
fund_user "$CHAOS_ADDR" "$CHAOS_KEY" 10000000

CHAOS_BROKER=$(create_broker "$CHAOS_KEY")
log_ok "Chaos broker: $CHAOS_BROKER"
deposit_to_broker "$CHAOS_BROKER" "$CHAOS_KEY" 5000000

prime_oracle

# Compute wRLP mint amount from price
WRLP_PRICE_WAD=$(cast call "$MOCK_ORACLE" "getIndexPrice(address,address)(uint256)" \
    "0x0000000000000000000000000000000000000000" "0x0000000000000000000000000000000000000000" \
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

# ═══════════════════════════════════════════════════════════════
# PHASE 4: DEPLOY SWAP ROUTER
# ═══════════════════════════════════════════════════════════════
log_phase "4" "DEPLOY SWAP ROUTER"

# Export vars for deploy_swap_router.py
export RPC_URL DEPLOYER_KEY MM_KEY CHAOS_KEY WAUSDC POSITION_TOKEN

cd /workspace/contracts
ROUTER_OUTPUT=$(forge script script/DeploySwapRouter.s.sol --tc DeploySwapRouter \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

SWAP_ROUTER=$(echo "$ROUTER_OUTPUT" | grep "SWAP_ROUTER:" | awk -F: '{print $NF}' | tr -d ' ')
if [ -z "$SWAP_ROUTER" ]; then
    echo "  ⚠️  Could not parse SWAP_ROUTER, trying python deploy..."
    cd /workspace
    SWAP_ROUTER=$(python3 -c "
import subprocess, os
result = subprocess.run(
    ['forge', 'script', 'script/DeploySwapRouter.s.sol', '--tc', 'DeploySwapRouter',
     '--rpc-url', os.environ['RPC_URL'], '--broadcast', '-v'],
    cwd='/workspace/contracts', capture_output=True, text=True,
    env={**os.environ}
)
for line in result.stdout.split('\n'):
    if 'SWAP_ROUTER:' in line:
        print(line.split(':')[-1].strip())
        break
")
fi

if [ -n "$SWAP_ROUTER" ]; then
    log_ok "SwapRouter: $SWAP_ROUTER"
else
    log_info "SwapRouter deploy skipped (non-critical)"
    SWAP_ROUTER=""
fi

# Approve tokens for MM and Chaos (via python for web3 calls)
if [ -n "$SWAP_ROUTER" ]; then
    python3 -c "
from web3 import Web3
from eth_account import Account
import os

w3 = Web3(Web3.HTTPProvider(os.environ['RPC_URL']))
ERC20_ABI = [
    {'inputs': [{'name': 'spender', 'type': 'address'}, {'name': 'amount', 'type': 'uint256'}],
     'name': 'approve', 'outputs': [{'name': '', 'type': 'bool'}],
     'stateMutability': 'nonpayable', 'type': 'function'},
]
MAX_UINT = 2**256 - 1
pm = '$POOL_MANAGER'
router = '$SWAP_ROUTER'
wausdc = '$WAUSDC'
pos_token = '$POSITION_TOKEN'

for name, key_env in [('MM', 'MM_KEY'), ('Chaos', 'CHAOS_KEY')]:
    key = os.environ.get(key_env)
    if not key: continue
    acct = Account.from_key(key)
    for token_addr, tname in [(wausdc, 'waUSDC'), (pos_token, 'wRLP')]:
        token = w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
        for spender, sname in [(router, 'Router'), (pm, 'PoolManager')]:
            nonce = w3.eth.get_transaction_count(acct.address)
            tx = token.functions.approve(Web3.to_checksum_address(spender), MAX_UINT).build_transaction({
                'from': acct.address, 'nonce': nonce, 'gas': 60000,
                'maxFeePerGas': w3.to_wei('2', 'gwei'), 'maxPriorityFeePerGas': w3.to_wei('1', 'gwei'),
            })
            signed = w3.eth.account.sign_transaction(tx, key)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            print(f'  ✅ {name} approved {tname} for {sname}')
"
fi

# ═══════════════════════════════════════════════════════════════
# PHASE 5: WRITE DEPLOYMENT CONFIG
# ═══════════════════════════════════════════════════════════════
log_phase "5" "WRITE DEPLOYMENT CONFIG"

mkdir -p /config

cat > /config/deployment.json << EOF
{
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
    "pool_manager": "$POOL_MANAGER",
    "v4_quoter": "$V4_QUOTER",
    "v4_position_manager": "$V4_POSITION_MANAGER",
    "v4_position_descriptor": "$V4_POSITION_DESCRIPTOR",
    "v4_state_view": "$V4_STATE_VIEW",
    "universal_router": "$UNIVERSAL_ROUTER",
    "permit2": "$PERMIT2",
    "token0": "$TOKEN0",
    "token1": "$TOKEN1",
    "zero_for_one_long": $ZERO_FOR_ONE_LONG,
    "user_a_broker": "$USER_A_BROKER",
    "mm_broker": "$MM_BROKER",
    "chaos_broker": "$CHAOS_BROKER",
    "deployer_key": "$DEPLOYER_KEY",
    "mm_key": "$MM_KEY",
    "chaos_key": "$CHAOS_KEY"
}
EOF

log_ok "Written /config/deployment.json"
cat /config/deployment.json | python3 -m json.tool

# Restore interval mining for daemon operation
cast rpc evm_setAutomine false --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
cast rpc evm_setIntervalMining 1 --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
log_ok "Interval mining restored (1s blocks)"

echo ""
echo -e "${MAGENTA}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║     DEPLOYMENT COMPLETE                           ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Indexer, MM daemon, and Chaos trader will start automatically."
echo ""
