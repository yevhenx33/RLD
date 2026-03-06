#!/bin/bash
#
# RLD Protocol - Mock Testnet Lifecycle Test
#
# Same as lifecycle_test.sh but uses MockRLDAaveOracle with live rate sync.
# This creates a complete, production-like market state with live mainnet rates.
#
# Phases:
# 0. Restart Anvil fork
# 1. Deploy protocol + MockRLDAaveOracle
# 2. Deploy waUSDC wrapped market (using mock oracle)
# 3. User A: $100M collateral → $5M wRLP → V4 LP
# 4. User B: Go long $100k
# 5. User C: TWAMM order $100k
# 6. Verification & Start Rate Sync Daemon
#
# Usage: ./scripts/mock_testnet_lifecycle.sh
#

set -e

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
NC='\033[0m'

# Paths
RLD_ROOT="/home/ubuntu/RLD"
CONTRACTS_DIR="$RLD_ROOT/contracts"
RPC_URL="http://localhost:8545"
FORK_BLOCK=21698573

# API Configuration
API_URL="https://rate-dashboard.onrender.com"
API_KEY="${API_KEY:-}"

# Amounts (6 decimals for USDC)
COLLATERAL_AMOUNT=100000000       # $100M
MINT_AMOUNT=5000000               # $5M wRLP
LP_AMOUNT=5000000                 # $5M each for LP
LONG_AMOUNT=100000                # $100k for go long
TWAMM_AMOUNT=100000               # $100k for TWAMM order
TWAMM_DURATION_HOURS=1
MM_CAPITAL=10000000               # $10M for market maker

# Convert to wei
COLLATERAL_WEI=$((COLLATERAL_AMOUNT * 1000000))
MINT_WEI=$((MINT_AMOUNT * 1000000))
LP_WEI=$((LP_AMOUNT * 1000000))
LONG_WEI=$((LONG_AMOUNT * 1000000))
TWAMM_WEI=$((TWAMM_AMOUNT * 1000000))
MM_WEI=$((MM_CAPITAL * 1000000))

# Mainnet addresses
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"
V4_POSITION_MANAGER="0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"

# Anvil accounts
USER_A_KEY=""  # Will be loaded from .env (PRIVATE_KEY)
USER_A_ADDRESS=""
USER_B_KEY="0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
USER_B_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
USER_C_KEY="0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a"
USER_C_ADDRESS="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
# MM User (User D) - Anvil account #3
MM_USER_KEY="0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a"
MM_USER_ADDRESS="0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65"
# Chaotic Trader (User E) - Anvil account #4
CHAOS_USER_KEY="0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba"
CHAOS_USER_ADDRESS="0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc"
CHAOS_CAPITAL=10000000  # $10M for chaotic trading
CHAOS_WEI=$((CHAOS_CAPITAL * 1000000))

# State variables (populated during execution)
MOCK_ORACLE=""
TWAMM_HOOK=""
FACTORY=""
WAUSDC=""
POSITION_TOKEN=""
BROKER_FACTORY=""
MARKET_ID=""
USER_A_BROKER=""
MM_BROKER=""

# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

log_phase() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  PHASE $1: $2${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════════${NC}"
    echo ""
}

log_step() {
    echo -e "${YELLOW}[$1] $2${NC}"
}

log_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

log_error() {
    echo -e "${RED}✗ $1${NC}"
    exit 1
}

parse_output() {
    echo "$1" | awk '{print $1}'
}

fund_user() {
    local USER_ADDR=$1
    local AMOUNT=$2
    local USER_KEY=$3
    local USER_NAME=$4
    
    log_step "1" "Funding $USER_NAME with $((AMOUNT / 1000000)) USDC"
    
    # Fund whale with ETH
    cast rpc anvil_setBalance "$USDC_WHALE" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_setBalance "$USER_ADDR" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
    
    cast send "$USDC" "transfer(address,uint256)" "$USER_ADDR" "$AMOUNT" \
        --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" > /dev/null
    
    cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
    
    log_step "2" "Supplying to Aave"
    cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$AMOUNT" \
        --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null
    
    cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
        "$USDC" "$AMOUNT" "$USER_ADDR" 0 \
        --private-key "$USER_KEY" --rpc-url "$RPC_URL" > /dev/null
    
    log_step "3" "Wrapping aUSDC → waUSDC"
    local AUSDC_BAL=$(parse_output "$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL")")
    
    cast send "$AUSDC" "approve(address,uint256)" "$WAUSDC" "$AUSDC_BAL" \
        --private-key "$USER_KEY" --rpc-url "$RPC_URL" --gas-limit 150000 > /dev/null
    
    cast send "$WAUSDC" "wrap(uint256)" "$AUSDC_BAL" \
        --private-key "$USER_KEY" --rpc-url "$RPC_URL" --gas-limit 500000 > /dev/null
    
    local WAUSDC_BAL=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_ADDR" --rpc-url "$RPC_URL")")
    log_success "$USER_NAME waUSDC: $((WAUSDC_BAL / 1000000))"
}

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

echo -e "${MAGENTA}╔═══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║     RLD PROTOCOL - MOCK TESTNET LIFECYCLE                         ║${NC}"
echo -e "${MAGENTA}║     Live Rate Sync with Mainnet Aave V3                           ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Collateral:   \$${COLLATERAL_AMOUNT}"
echo "  wRLP Mint:    \$${MINT_AMOUNT}"
echo "  LP Amount:    \$${LP_AMOUNT} each"
echo "  Go Long:      \$${LONG_AMOUNT}"
echo "  TWAMM Order:  \$${TWAMM_AMOUNT}"
echo ""

# Load credentials
cd "$CONTRACTS_DIR"
if [ -f .env ]; then
    source .env
    USER_A_KEY="$PRIVATE_KEY"
    USER_A_ADDRESS=$(cast wallet address --private-key "$USER_A_KEY" 2>/dev/null)
else
    log_error ".env file not found"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 0: RESTART ANVIL
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 0 "RESTART ANVIL FORK"

log_step "1" "Killing existing anvil process..."
pkill -f "anvil" 2>/dev/null || true
sleep 2

log_step "2" "Starting fresh anvil fork at block $FORK_BLOCK..."
MAINNET_RPC=$(grep "^MAINNET_RPC_URL=" .env | cut -d'=' -f2)
anvil --fork-url "$MAINNET_RPC" --fork-block-number $FORK_BLOCK --host 0.0.0.0 --port 8545 > /tmp/anvil_lifecycle.log 2>&1 &
ANVIL_PID=$!

log_step "3" "Waiting for RPC to be ready..."
for i in {1..30}; do
    if curl -s -X POST "$RPC_URL" -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' 2>/dev/null | grep -q "result"; then
        break
    fi
    sleep 1
done

BLOCK=$(curl -s -X POST "$RPC_URL" -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' | jq -r '.result' | xargs printf "%d")

if [ "$BLOCK" -eq "$FORK_BLOCK" ]; then
    log_success "Anvil running at block $BLOCK (PID: $ANVIL_PID)"
else
    log_error "Failed to start anvil"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DEPLOY PROTOCOL + MOCK ORACLE
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 1 "DEPLOY PROTOCOL + MOCK ORACLE"

log_step "1" "Running DeployRLDProtocol.s.sol..."
DEPLOY_OUTPUT=$(forge script script/DeployRLDProtocol.s.sol --tc DeployRLDProtocol \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

if ! echo "$DEPLOY_OUTPUT" | grep -q "DEPLOYMENT COMPLETE"; then
    log_error "Protocol deployment failed"
fi

TWAMM_HOOK=$(jq -r '.TWAMM' deployments.json)
FACTORY=$(jq -r '.RLDMarketFactory' deployments.json)
RLD_CORE=$(jq -r '.RLDCore' deployments.json)

log_success "Protocol deployed"
echo "  TWAMM Hook: $TWAMM_HOOK"
echo "  Factory:    $FACTORY"

log_step "2" "Deploying MockRLDAaveOracle..."
MOCK_ORACLE=$(forge create src/rld/modules/oracles/MockRLDAaveOracle.sol:MockRLDAaveOracle \
    --private-key $PRIVATE_KEY \
    --rpc-url $RPC_URL \
    --broadcast 2>&1 | grep "Deployed to:" | awk '{print $3}')

if [ -z "$MOCK_ORACLE" ]; then
    log_error "Failed to deploy MockRLDAaveOracle"
fi

log_success "MockRLDAaveOracle: $MOCK_ORACLE"

log_step "3" "Fetching current mainnet rate from API..."
RATE_JSON=$(curl -s "$API_URL/rates?limit=1&symbol=USDC" -H "X-API-Key: $API_KEY")
APY=$(echo $RATE_JSON | jq -r '.[0].apy')
RATE_RAY=$(python3 -c "print(int($APY / 100 * 1e27))")

echo "  Current Mainnet Rate: ${APY}%"

cast send $MOCK_ORACLE "setRate(uint256)" $RATE_RAY \
    --private-key $PRIVATE_KEY \
    --rpc-url $RPC_URL > /dev/null 2>&1

log_success "Mock oracle set to ${APY}%"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: DEPLOY WRAPPED MARKET
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 2 "DEPLOY WRAPPED MARKET (with Mock Oracle)"

log_step "1" "Running DeployWrappedMarket.s.sol with USE_MOCK_ORACLE=true..."
MARKET_OUTPUT=$(USE_MOCK_ORACLE=true MOCK_ORACLE=$MOCK_ORACLE \
    forge script script/DeployWrappedMarket.s.sol --tc DeployWrappedMarket \
    --rpc-url "$RPC_URL" --broadcast -v 2>&1)

if ! echo "$MARKET_OUTPUT" | grep -q "WRAPPED MARKET CREATED"; then
    echo "$MARKET_OUTPUT"
    log_error "Market deployment failed"
fi

# Extract addresses
WAUSDC=$(echo "$MARKET_OUTPUT" | grep -i "waUSDC deployed:" | awk '{print $NF}')
if [ -z "$WAUSDC" ]; then
    WAUSDC=$(echo "$MARKET_OUTPUT" | grep "collateralToken (waUSDC):" | awk '{print $NF}')
fi
MARKET_ID=$(echo "$MARKET_OUTPUT" | grep "MarketId:" | awk '{print $NF}')
BROKER_FACTORY=$(echo "$MARKET_OUTPUT" | grep "BrokerFactory:" | awk '{print $NF}')
POSITION_TOKEN=$(echo "$MARKET_OUTPUT" | grep "positionToken (wRLP):" | awk '{print $NF}')

if [ -z "$WAUSDC" ] || [ -z "$POSITION_TOKEN" ]; then
    log_error "Failed to extract market addresses"
fi

log_success "Wrapped market deployed (using MockRLDAaveOracle)"
echo "  waUSDC:         $WAUSDC"
echo "  wRLP:           $POSITION_TOKEN"
echo "  BrokerFactory:  $BROKER_FACTORY"
echo "  MarketId:       $MARKET_ID"
echo "  RateOracle:     $MOCK_ORACLE (mock)"

log_step "2" "Priming TWAMM oracle..."
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null
log_success "Oracle primed (advanced 2 hours)"

# Currency sorting
log_step "3" "Determining currency order..."
WAUSDC_LOWER=$(echo "$WAUSDC" | tr '[:upper:]' '[:lower:]')
POSITION_TOKEN_LOWER=$(echo "$POSITION_TOKEN" | tr '[:upper:]' '[:lower:]')

if [[ "$WAUSDC_LOWER" < "$POSITION_TOKEN_LOWER" ]]; then
    TOKEN0="$WAUSDC"
    TOKEN1="$POSITION_TOKEN"
    ZERO_FOR_ONE_LONG=true
else
    TOKEN0="$POSITION_TOKEN"
    TOKEN1="$WAUSDC"
    ZERO_FOR_ONE_LONG=false
fi

log_success "Currency order determined"
echo "  TOKEN0: $TOKEN0"
echo "  TOKEN1: $TOKEN1"

# Save to JSON
cat > wrapped_market.json << EOF
{
  "waUSDC": "$WAUSDC",
  "positionToken": "$POSITION_TOKEN",
  "brokerFactory": "$BROKER_FACTORY",
  "marketId": "$MARKET_ID",
  "mockOracle": "$MOCK_ORACLE",
  "token0": "$TOKEN0",
  "token1": "$TOKEN1",
  "zeroForOneLong": $ZERO_FOR_ONE_LONG,
  "deployedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "network": "mainnet-fork-mock"
}
EOF

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: USER A - PROVIDE LP
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 3 "USER A - PROVIDE \$${COLLATERAL_AMOUNT} COLLATERAL & LP"

fund_user "$USER_A_ADDRESS" "$COLLATERAL_WEI" "$USER_A_KEY" "User A"

log_step "4" "Creating PrimeBroker..."
SALT=$(cast keccak "lifecycle-$(date +%s)")
BROKER_TX=$(cast send "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" --json)

USER_A_BROKER=$(echo "$BROKER_TX" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for log in data.get('logs', []):
    topics = log.get('topics', [])
    if topics and topics[0].lower() == '0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71':
        data_field = log.get('data', '')
        if data_field.startswith('0x') and len(data_field) >= 66:
            print('0x' + data_field[26:66])
            break
")
log_success "Broker: $USER_A_BROKER"

log_step "5" "Transferring waUSDC to broker..."
WAUSDC_BAL=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_A_ADDRESS" --rpc-url "$RPC_URL")")
cast send "$WAUSDC" "transfer(address,uint256)" "$USER_A_BROKER" "$WAUSDC_BAL" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
log_success "Broker waUSDC: $((WAUSDC_BAL / 1000000))"

log_step "6" "Minting $MINT_AMOUNT wRLP..."
cast send "$USER_A_BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$MINT_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null

BROKER_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_A_BROKER" --rpc-url "$RPC_URL")")
log_success "Broker wRLP: $((BROKER_WRLP / 1000000))"

log_step "7" "Withdrawing $LP_AMOUNT each for LP..."
cast send "$USER_A_BROKER" "withdrawPositionToken(address,uint256)" "$USER_A_ADDRESS" "$LP_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null

cast send "$USER_A_BROKER" "withdrawCollateral(address,uint256)" "$USER_A_ADDRESS" "$LP_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
log_success "Withdrawn for LP"

log_step "8" "Approving V4 contracts..."
cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$LP_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$LP_WEI" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null

cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WAUSDC" "$V4_POSITION_MANAGER" "$(python3 -c 'print(2**160-1)')" "$(python3 -c 'print(2**48-1)')" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$POSITION_TOKEN" "$V4_POSITION_MANAGER" "$(python3 -c 'print(2**160-1)')" "$(python3 -c 'print(2**48-1)')" \
    --private-key "$USER_A_KEY" --rpc-url "$RPC_URL" > /dev/null
log_success "V4 approvals complete"

log_step "9" "Adding V4 liquidity..."
AUSDC_AMOUNT=$LP_WEI WRLP_AMOUNT=$LP_WEI WAUSDC=$WAUSDC POSITION_TOKEN=$POSITION_TOKEN TWAMM_HOOK=$TWAMM_HOOK \
    forge script script/AddLiquidityWrapped.s.sol --tc AddLiquidityWrappedScript \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/lp_output.log 2>&1

if grep -q "LP Position Created" /tmp/lp_output.log; then
    TOKEN_ID=$(grep "Token ID:" /tmp/lp_output.log | awk '{print $NF}')
    log_success "V4 LP Position created (Token ID: $TOKEN_ID)"
else
    log_error "LP creation failed - check /tmp/lp_output.log"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: USER B - GO LONG
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 4 "USER B - GO LONG \$${LONG_AMOUNT}"

fund_user "$USER_B_ADDRESS" "$LONG_WEI" "$USER_B_KEY" "User B"

log_step "4" "Swapping waUSDC → wRLP (using LifecycleSwap)..."

WRLP_BEFORE=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_B_ADDRESS" --rpc-url "$RPC_URL")")
USER_B_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_B_ADDRESS" --rpc-url "$RPC_URL")")

TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    SWAP_AMOUNT="$USER_B_WAUSDC" ZERO_FOR_ONE="$ZERO_FOR_ONE_LONG" \
    SWAP_USER_KEY="$USER_B_KEY" \
    forge script script/LifecycleSwap.s.sol --tc LifecycleSwap \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/swap_output.log 2>&1

WRLP_AFTER=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_B_ADDRESS" --rpc-url "$RPC_URL")")
WRLP_RECEIVED=$((WRLP_AFTER - WRLP_BEFORE))

if [ "$WRLP_RECEIVED" -gt 0 ]; then
    log_success "Swap complete: received $((WRLP_RECEIVED / 1000000)) wRLP"
else
    log_error "Swap failed - check /tmp/swap_output.log"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: USER C - TWAMM ORDER
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 5 "USER C - TWAMM ORDER \$${TWAMM_AMOUNT}"

fund_user "$USER_C_ADDRESS" "$TWAMM_WEI" "$USER_C_KEY" "User C"

log_step "4" "Submitting TWAMM order (using LifecycleTWAMM)..."

WAUSDC_BEFORE=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_C_ADDRESS" --rpc-url "$RPC_URL")")
USER_C_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_C_ADDRESS" --rpc-url "$RPC_URL")")

TOKEN0="$TOKEN0" TOKEN1="$TOKEN1" TWAMM_HOOK="$TWAMM_HOOK" \
    ORDER_AMOUNT="$USER_C_WAUSDC" \
    DURATION_SECONDS="$((TWAMM_DURATION_HOURS * 3600))" \
    ZERO_FOR_ONE="$ZERO_FOR_ONE_LONG" \
    TWAMM_USER_KEY="$USER_C_KEY" \
    forge script script/LifecycleTWAMM.s.sol --tc LifecycleTWAMM \
    --rpc-url "$RPC_URL" --broadcast -v > /tmp/twamm_output.log 2>&1

WAUSDC_AFTER=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_C_ADDRESS" --rpc-url "$RPC_URL")")

if [ "$WAUSDC_AFTER" -lt "$WAUSDC_BEFORE" ]; then
    LOCKED=$((WAUSDC_BEFORE - WAUSDC_AFTER))
    log_success "TWAMM order created: locked $((LOCKED / 1000000)) waUSDC over $TWAMM_DURATION_HOURS hour(s)"
else
    log_error "TWAMM order failed - check /tmp/twamm_output.log"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5.5: MARKET MAKER SETUP
# ═══════════════════════════════════════════════════════════════════════════════
log_phase "5.5" "MARKET MAKER SETUP - \$${MM_CAPITAL} CAPITAL"

log_step "1" "Funding MM User with $((MM_WEI / 1000000)) USDC"
cast rpc anvil_setBalance "$USDC_WHALE" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_setBalance "$MM_USER_ADDRESS" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
cast send "$USDC" "transfer(address,uint256)" "$MM_USER_ADDRESS" "$MM_WEI" \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

log_step "2" "Supplying to Aave"
cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$MM_WEI" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
    "$USDC" "$MM_WEI" "$MM_USER_ADDRESS" 0 \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null

log_step "3" "Wrapping aUSDC → waUSDC"
MM_AUSDC_BAL=$(parse_output "$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$MM_USER_ADDRESS" --rpc-url "$RPC_URL")")
cast send "$AUSDC" "approve(address,uint256)" "$WAUSDC" "$MM_AUSDC_BAL" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" --gas-limit 150000 > /dev/null
cast send "$WAUSDC" "wrap(uint256)" "$MM_AUSDC_BAL" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" --gas-limit 500000 > /dev/null

MM_WAUSDC_BAL=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$MM_USER_ADDRESS" --rpc-url "$RPC_URL")")
log_success "MM User waUSDC: $((MM_WAUSDC_BAL / 1000000))"

log_step "4" "Creating MM Broker..."
SALT=$(cast keccak "mm-broker-$(date +%s)")
MM_BROKER_TX=$(cast send "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" --json)

MM_BROKER=$(echo "$MM_BROKER_TX" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for log in data.get('logs', []):
    topics = log.get('topics', [])
    if topics and topics[0].lower() == '0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71':
        data_field = log.get('data', '')
        if data_field.startswith('0x') and len(data_field) >= 66:
            print('0x' + data_field[26:66])
            break
")
log_success "MM Broker: $MM_BROKER"

log_step "5" "Transferring waUSDC to MM broker..."
cast send "$WAUSDC" "transfer(address,uint256)" "$MM_BROKER" "$MM_WAUSDC_BAL" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
log_success "MM Broker waUSDC: $((MM_WAUSDC_BAL / 1000000))"

# Prime TWAP oracle for MM broker (advance time)
log_step "6" "Priming TWAP oracle for MM broker..."
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_mine 1 --rpc-url "$RPC_URL" > /dev/null

# Get wRLP price from oracle to calculate proper mint amount
# Price is in WAD (18 decimals): ~4.64e18 means 1 wRLP = 4.64 waUSDC
WRLP_PRICE_WAD=$(parse_output "$(cast call "$MOCK_ORACLE" "getIndexPrice(address,address)(uint256)" \
    "0x0000000000000000000000000000000000000000" "0x0000000000000000000000000000000000000000" \
    --rpc-url "$RPC_URL")")

# Calculate how many wRLP tokens to mint for $1M worth (safe amount)
# Formula: tokens = target_value_usd / price_per_token
# $1M = 1,000,000 * 1e6 (6 decimals)
# tokens = (1M * 1e6 * 1e18) / price_wad / 1e18 = 1M * 1e6 / (price_wad / 1e18)
MM_TARGET_VALUE=1000000  # $1M worth of wRLP
MM_MINT_TOKENS=$(python3 -c "
price_wad = $WRLP_PRICE_WAD
target_usd = $MM_TARGET_VALUE
# tokens = target_usd / (price_wad / 1e18)
tokens = int(target_usd * 1e18 / price_wad)  # In wRLP units (not wei)
tokens_wei = tokens * 1000000  # Convert to 6 decimals
print(tokens_wei)
")

MM_MINT_DISPLAY=$((MM_MINT_TOKENS / 1000000))
PRICE_DISPLAY=$(python3 -c "print(f'{$WRLP_PRICE_WAD / 1e18:.4f}')")

log_step "7" "Minting ~$MM_MINT_DISPLAY wRLP (\$$MM_TARGET_VALUE worth at \$$PRICE_DISPLAY/wRLP)..."
cast send "$MM_BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$MM_MINT_TOKENS" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null

MM_BROKER_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$MM_BROKER" --rpc-url "$RPC_URL")")
log_success "MM Broker wRLP: $((MM_BROKER_WRLP / 1000000))"

# Only withdraw wRLP to MM wallet (keep collateral in broker to stay solvent)
# MM will sell wRLP when mark > index, and buy back when mark < index
MM_WITHDRAW_WRLP=$MM_BROKER_WRLP  # Withdraw all wRLP

log_step "8" "Withdrawing wRLP to MM wallet for trading (keeping collateral in broker)..."
cast send "$MM_BROKER" "withdrawPositionToken(address,uint256)" "$MM_USER_ADDRESS" "$MM_WITHDRAW_WRLP" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null

# Also give MM some waUSDC from the whale for bidirectional trading
log_step "8b" "Funding MM with additional waUSDC for buying..."
MM_TRADE_USDC=$((1000000 * 1000000))  # $1M for trading
cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
cast send "$USDC" "transfer(address,uint256)" "$MM_USER_ADDRESS" "$MM_TRADE_USDC" \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" > /dev/null
cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$MM_TRADE_USDC" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
    "$USDC" "$MM_TRADE_USDC" "$MM_USER_ADDRESS" 0 \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
MM_EXTRA_AUSDC=$(parse_output "$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$MM_USER_ADDRESS" --rpc-url "$RPC_URL")")
cast send "$AUSDC" "approve(address,uint256)" "$WAUSDC" "$MM_EXTRA_AUSDC" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" --gas-limit 150000 > /dev/null
cast send "$WAUSDC" "wrap(uint256)" "$MM_EXTRA_AUSDC" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" --gas-limit 500000 > /dev/null

MM_FINAL_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$MM_USER_ADDRESS" --rpc-url "$RPC_URL")")
MM_FINAL_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$MM_USER_ADDRESS" --rpc-url "$RPC_URL")")
log_success "MM Wallet: $((MM_FINAL_WAUSDC / 1000000)) waUSDC + $((MM_FINAL_WRLP / 1000000)) wRLP"

log_step "9" "Approving swap contracts for MM..."
cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$(python3 -c 'print(2**256-1)')" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$(python3 -c 'print(2**256-1)')" \
    --private-key "$MM_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
log_success "MM User swap approvals complete"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5.6: CHAOTIC TRADER SETUP (MISTER CHAOS)
# ═══════════════════════════════════════════════════════════════════════════════
log_phase "5.6" "CHAOTIC TRADER SETUP - \$${CHAOS_CAPITAL} CAPITAL"

# Use fund_user helper (includes whale balance reset)
fund_user "$CHAOS_USER_ADDRESS" "$CHAOS_WEI" "$CHAOS_USER_KEY" "Mister Chaos"

# Get Chaos waUSDC balance
CHAOS_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$CHAOS_USER_ADDRESS" --rpc-url "$RPC_URL")")
log_success "Chaos waUSDC: $((CHAOS_WAUSDC / 1000000))"

# For wRLP: withdraw some from User A's broker (which has plenty of collateral)
# User A broker has 95M+ waUSDC collateral and 0 wRLP (already withdrawn for LP)
# So we'll mint from Chaos's own broker

log_step "4" "Creating Chaos Broker for wRLP minting..."
SALT=$(cast keccak "chaos-broker-$(date +%s)")
CHAOS_BROKER_TX=$(cast send "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$CHAOS_USER_KEY" --rpc-url "$RPC_URL" --json)

CHAOS_BROKER=$(echo "$CHAOS_BROKER_TX" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for log in data.get('logs', []):
    topics = log.get('topics', [])
    if topics and topics[0].lower() == '0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71':
        data_field = log.get('data', '')
        if data_field.startswith('0x') and len(data_field) >= 66:
            print('0x' + data_field[26:66])
            break
")
log_success "Chaos Broker: $CHAOS_BROKER"

# Transfer 50% waUSDC to broker for collateral (need collateral to mint)
CHAOS_BROKER_DEPOSIT=$((CHAOS_WAUSDC / 2))
log_step "5" "Depositing $((CHAOS_BROKER_DEPOSIT / 1000000)) waUSDC to broker..."
cast send "$WAUSDC" "transfer(address,uint256)" "$CHAOS_BROKER" "$CHAOS_BROKER_DEPOSIT" \
    --private-key "$CHAOS_USER_KEY" --rpc-url "$RPC_URL" > /dev/null

# Calculate wRLP mint amount - mint ~$500k worth (conservative)
CHAOS_MINT_TARGET=500000  # $500k worth of wRLP
CHAOS_MINT_TOKENS=$(python3 -c "
price_wad = $WRLP_PRICE_WAD
target_usd = $CHAOS_MINT_TARGET
tokens = int(target_usd * 1e18 / price_wad)
tokens_wei = tokens * 1000000
print(tokens_wei)
")

log_step "6" "Minting ~\$$CHAOS_MINT_TARGET worth of wRLP..."
cast send "$CHAOS_BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$CHAOS_MINT_TOKENS" \
    --private-key "$CHAOS_USER_KEY" --rpc-url "$RPC_URL" > /dev/null

# Withdraw wRLP to wallet
CHAOS_BROKER_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$CHAOS_BROKER" --rpc-url "$RPC_URL")")
cast send "$CHAOS_BROKER" "withdrawPositionToken(address,uint256)" "$CHAOS_USER_ADDRESS" "$CHAOS_BROKER_WRLP" \
    --private-key "$CHAOS_USER_KEY" --rpc-url "$RPC_URL" > /dev/null

CHAOS_FINAL_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$CHAOS_USER_ADDRESS" --rpc-url "$RPC_URL")")
CHAOS_FINAL_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$CHAOS_USER_ADDRESS" --rpc-url "$RPC_URL")")
log_success "🎲 Mister Chaos: $((CHAOS_FINAL_WAUSDC / 1000000)) waUSDC + $((CHAOS_FINAL_WRLP / 1000000)) wRLP"

log_step "7" "Approving swap contracts for Chaos..."
cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$(python3 -c 'print(2**256-1)')" \
    --private-key "$CHAOS_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$(python3 -c 'print(2**256-1)')" \
    --private-key "$CHAOS_USER_KEY" --rpc-url "$RPC_URL" > /dev/null
log_success "Chaos swap approvals complete"

# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: VERIFICATION & START DAEMON
# ═══════════════════════════════════════════════════════════════════════════════
log_phase 6 "VERIFICATION & RATE SYNC DAEMON"

echo -e "${CYAN}Final Balances:${NC}"
echo ""

# User A Broker
BROKER_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_A_BROKER" --rpc-url "$RPC_URL")")
BROKER_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_A_BROKER" --rpc-url "$RPC_URL")")
echo "  User A Broker:"
echo "    waUSDC: $((BROKER_WAUSDC / 1000000))"
echo "    wRLP:   $((BROKER_WRLP / 1000000))"
echo ""

# User B
USER_B_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_B_ADDRESS" --rpc-url "$RPC_URL")")
USER_B_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$USER_B_ADDRESS" --rpc-url "$RPC_URL")")
echo "  User B (Long):"
echo "    waUSDC: $((USER_B_WAUSDC / 1000000))"
echo "    wRLP:   $((USER_B_WRLP / 1000000))"
echo ""

# User C
USER_C_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$USER_C_ADDRESS" --rpc-url "$RPC_URL")")
echo "  User C (TWAMM):"
echo "    waUSDC: $((USER_C_WAUSDC / 1000000)) (rest in TWAMM order)"
echo ""

# Pool Manager
PM="0x000000000004444c5dc75cB358380D2e3dE08A90"
PM_WAUSDC=$(parse_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$PM" --rpc-url "$RPC_URL")")
PM_WRLP=$(parse_output "$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$PM" --rpc-url "$RPC_URL")")
echo "  V4 Pool Manager:"
echo "    waUSDC: $((PM_WAUSDC / 1000000))"
echo "    wRLP:   $((PM_WRLP / 1000000))"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
# START COMBINED DAEMON (Rate Sync + Market Maker)
# ═══════════════════════════════════════════════════════════════════════════════
echo -e "${MAGENTA}╔═══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║          MOCK TESTNET LIFECYCLE COMPLETE!                         ║${NC}"
echo -e "${MAGENTA}║          Starting Combined Daemon (Rate Sync + MM)...             ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  MockRLDAaveOracle: $MOCK_ORACLE"
echo "  Current Rate:      ${APY}%"
echo "  Sync Interval:     12 seconds"
echo "  MM Threshold:      1% (100bps)"
echo ""
echo -e "${CYAN}Copy these exports:${NC}"
echo ""
echo "export WAUSDC=$WAUSDC"
echo "export POSITION_TOKEN=$POSITION_TOKEN"
echo "export TWAMM_HOOK=$TWAMM_HOOK"
echo "export MARKET_ID=$MARKET_ID"
echo "export BROKER_FACTORY=$BROKER_FACTORY"
echo "export USER_A_BROKER=$USER_A_BROKER"
echo "export MOCK_ORACLE=$MOCK_ORACLE"
echo "export CHAOS_USER_KEY=$CHAOS_USER_KEY"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the daemon${NC}"
echo ""

# Run the combined daemon
cd "$RLD_ROOT/backend"
export MOCK_ORACLE_ADDR=$MOCK_ORACLE
export RPC_URL=$RPC_URL
export PRIVATE_KEY=$MM_USER_KEY  # Use MM user's key for trading
export ORACLE_ADMIN_KEY=$USER_A_KEY  # Use deployer's key for oracle updates (has admin rights)
export API_KEY=$API_KEY
export API_URL=$API_URL
export WAUSDC=$WAUSDC
export POSITION_TOKEN=$POSITION_TOKEN
export TWAMM_HOOK=$TWAMM_HOOK
export MARKET_ID=$MARKET_ID
export BROKER_FACTORY=$BROKER_FACTORY
export RLD_CORE=$RLD_CORE

python3 scripts/combined_daemon.py
