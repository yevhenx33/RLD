#!/bin/bash
#
# RLD Protocol - Mint wRLP and Provide V4 Liquidity
#
# This script:
# 1. Opens a position (mint wRLP debt)
# 2. Withdraws wRLP and aUSDC from broker
# 3. Provides concentrated liquidity to the V4 pool
#
# Run AFTER deploy_local.sh completes.
#
# Usage: ./scripts/mint_and_lp.sh [COLLATERAL] [DEBT] [LP_AMOUNT]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
RLD_ROOT="/home/ubuntu/RLD"
CONTRACTS_DIR="$RLD_ROOT/contracts"
RPC_URL="http://localhost:8545"

# Default amounts (6 decimals for USDC-like tokens)
COLLATERAL_AMOUNT=${1:-10000000}   # 10M aUSDC
DEBT_AMOUNT=${2:-500000}           # 500k wRLP
LP_AMOUNT=${3:-100000}             # 100k of each token for LP

# Convert to wei (6 decimals)
COLLATERAL_WEI=$(echo "$COLLATERAL_AMOUNT * 1000000" | bc)
DEBT_WEI=$(echo "$DEBT_AMOUNT * 1000000" | bc)
LP_WEI=$(echo "$LP_AMOUNT * 1000000" | bc)

# Mainnet addresses
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0xCFFAd3200574698b78f32232aa9D63eABD290703"
V4_POOL_MANAGER="0x000000000004444c5dc75cB358380D2e3dE08A90"
V4_POSITION_MANAGER="0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"

# Helper function to parse cast output
parse_cast_output() {
    echo "$1" | awk '{print $1}'
}

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║         RLD Protocol - Mint wRLP & Provide V4 LP          ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Collateral:  ${YELLOW}$COLLATERAL_AMOUNT${NC} aUSDC"
echo -e "  Debt:        ${YELLOW}$DEBT_AMOUNT${NC} wRLP"
echo -e "  LP Amount:   ${YELLOW}$LP_AMOUNT${NC} each token"
echo ""

# =============================================================================
# Load environment
# =============================================================================
cd "$CONTRACTS_DIR"
source .env

if [ -z "$PRIVATE_KEY" ]; then
    echo -e "${RED}✗ Error: PRIVATE_KEY not set in contracts/.env${NC}"
    exit 1
fi

DEPLOYER=$(cast wallet address --private-key "$PRIVATE_KEY" 2>/dev/null)
echo -e "  Deployer: ${CYAN}$DEPLOYER${NC}"

# =============================================================================
# Load deployment addresses
# =============================================================================
echo -e "\n${CYAN}[1/9] Loading deployment addresses...${NC}"

if [ ! -f "deployments.json" ]; then
    echo -e "${RED}✗ Error: deployments.json not found. Run deploy_local.sh first.${NC}"
    exit 1
fi

BROKER_FACTORY=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('BrokerFactory', ''))")
MARKET_ID=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('MarketId', ''))")
RLD_CORE=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('RLDCore', ''))")
TWAMM=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('TWAMM', ''))")

if [ -z "$BROKER_FACTORY" ] || [ -z "$MARKET_ID" ] || [ -z "$RLD_CORE" ]; then
    echo -e "${RED}✗ Error: Missing addresses in deployments.json${NC}"
    exit 1
fi

echo -e "${GREEN}✓ RLDCore: $RLD_CORE${NC}"
echo -e "${GREEN}✓ MarketId: $MARKET_ID${NC}"

# Get PositionToken (wRLP) address from market addresses
# MarketAddresses struct has positionToken as the 10th field (index 9)
echo "  Querying PositionToken address..."
# Use a simpler approach - call getMarketAddresses and parse with Python
MARKET_ADDRS=$(cast call "$RLD_CORE" "getMarketAddresses(bytes32)" "$MARKET_ID" --rpc-url "$RPC_URL" 2>/dev/null || echo "")

if [ -z "$MARKET_ADDRS" ]; then
    echo -e "${YELLOW}⚠ Could not query market addresses, using fallback...${NC}"
    # Try to get the position token from the whale's perspective - it gets logged on createMarket
    # For now, we'll skip LP if we can't find it
    POSITION_TOKEN=""
else
    # The output is a tuple of addresses, positionToken is the 10th element
    POSITION_TOKEN=$(echo "$MARKET_ADDRS" | python3 -c "
import sys
data = sys.stdin.read().strip()
# Remove '0x' prefix and split into 32-byte chunks
if data.startswith('0x'):
    data = data[2:]
# Each address is padded to 32 bytes (64 hex chars)
chunks = [data[i:i+64] for i in range(0, len(data), 64)]
# positionToken is at index 9 (0-indexed)
if len(chunks) > 9:
    print('0x' + chunks[9][-40:])
else:
    print('')
")
fi

if [ -n "$POSITION_TOKEN" ] && [ "$POSITION_TOKEN" != "0x0000000000000000000000000000000000000000" ]; then
    echo -e "${GREEN}✓ PositionToken (wRLP): $POSITION_TOKEN${NC}"
else
    echo -e "${YELLOW}⚠ Could not find PositionToken, LP provision will be skipped${NC}"
    POSITION_TOKEN=""
fi

# =============================================================================
# Step 2: Acquire aUSDC via whale impersonation
# =============================================================================
echo -e "\n${CYAN}[2/9] Impersonating USDC whale and depositing to Aave...${NC}"

WHALE_BALANCE_RAW=$(cast call "$USDC" "balanceOf(address)(uint256)" "$USDC_WHALE" --rpc-url "$RPC_URL")
WHALE_BALANCE=$(parse_cast_output "$WHALE_BALANCE_RAW")
WHALE_BALANCE_NUM=$((WHALE_BALANCE / 1000000))
echo "  Whale USDC balance: $WHALE_BALANCE_NUM USDC"

if [ "$WHALE_BALANCE" -lt "$COLLATERAL_WEI" ]; then
    echo -e "${RED}✗ Error: Whale has insufficient balance${NC}"
    exit 1
fi

cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

echo "  Approving Aave Pool..."
cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$COLLATERAL_WEI" \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" --quiet > /dev/null

echo "  Supplying $COLLATERAL_AMOUNT USDC to Aave..."
cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
    "$USDC" "$COLLATERAL_WEI" "$DEPLOYER" 0 \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" --quiet > /dev/null

cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

AUSDC_BALANCE_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")
AUSDC_BALANCE=$(parse_cast_output "$AUSDC_BALANCE_RAW")
echo -e "${GREEN}✓ Deployer aUSDC balance: $((AUSDC_BALANCE / 1000000)) aUSDC${NC}"

# =============================================================================
# Step 3: Advance time for TWAMM oracle
# =============================================================================
echo -e "\n${CYAN}[3/9] Priming TWAMM oracle (advancing time)...${NC}"

cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc evm_mine --rpc-url "$RPC_URL" > /dev/null

echo -e "${GREEN}✓ Advanced time by 2 hours${NC}"

# =============================================================================
# Step 4: Create PrimeBroker
# =============================================================================
echo -e "\n${CYAN}[4/9] Creating PrimeBroker...${NC}"

SALT=$(cast keccak "lp-broker-$(date +%s)")
BROKER_TX=$(cast send "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --json)

# Extract broker address from BrokerCreated event
BROKER=$(echo "$BROKER_TX" | python3 -c "
import sys, json
data = json.load(sys.stdin)
broker_created_sig = '0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71'
for log in data.get('logs', []):
    topics = log.get('topics', [])
    if topics and topics[0].lower() == broker_created_sig:
        data_field = log.get('data', '')
        if data_field.startswith('0x') and len(data_field) >= 66:
            print('0x' + data_field[26:66])
            break
    elif len(topics) >= 4 and topics[1] == '0x' + '0'*64:
        addr = '0x' + topics[3][-40:]
        print(addr)
        break
")

if [ -z "$BROKER" ] || [ "$BROKER" = "0x0000000000000000000000000000000000000000" ]; then
    echo -e "${RED}✗ Error: Could not extract broker address${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Broker created: $BROKER${NC}"

# =============================================================================
# Step 5: Transfer collateral to broker
# =============================================================================
echo -e "\n${CYAN}[5/9] Transferring collateral to broker...${NC}"

cast send "$AUSDC" "transfer(address,uint256)" "$BROKER" "$AUSDC_BALANCE" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

BROKER_AUSDC_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
BROKER_AUSDC=$(parse_cast_output "$BROKER_AUSDC_RAW")
echo -e "${GREEN}✓ Broker aUSDC: $((BROKER_AUSDC / 1000000)) aUSDC${NC}"

# =============================================================================
# Step 6: Mint wRLP debt
# =============================================================================
echo -e "\n${CYAN}[6/9] Minting $DEBT_AMOUNT wRLP debt...${NC}"

cast send "$BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$DEBT_WEI" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

if [ -n "$POSITION_TOKEN" ]; then
    WRLP_BALANCE_RAW=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
    WRLP_BALANCE=$(parse_cast_output "$WRLP_BALANCE_RAW")
    echo -e "${GREEN}✓ Broker wRLP: $((WRLP_BALANCE / 1000000)) wRLP${NC}"
else
    echo -e "${GREEN}✓ Position opened (wRLP balance unknown)${NC}"
fi

# =============================================================================
# Step 7: Withdraw tokens for LP
# =============================================================================
if [ -z "$POSITION_TOKEN" ]; then
    echo -e "\n${YELLOW}⚠ Skipping LP: PositionToken not found${NC}"
else
    echo -e "\n${CYAN}[7/9] Withdrawing tokens for LP...${NC}"
    
    # Withdraw wRLP (position token) from broker to deployer
    cast send "$BROKER" "withdrawPositionToken(address,uint256)" "$DEPLOYER" "$LP_WEI" \
        --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null
    
    # Withdraw aUSDC (collateral) from broker to deployer
    cast send "$BROKER" "withdrawCollateral(address,uint256)" "$DEPLOYER" "$LP_WEI" \
        --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null
    
    DEPLOYER_WRLP_RAW=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")
    DEPLOYER_WRLP=$(parse_cast_output "$DEPLOYER_WRLP_RAW")
    DEPLOYER_AUSDC_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")
    DEPLOYER_AUSDC=$(parse_cast_output "$DEPLOYER_AUSDC_RAW")
    
    echo -e "${GREEN}✓ Deployer wRLP: $((DEPLOYER_WRLP / 1000000)) wRLP${NC}"
    echo -e "${GREEN}✓ Deployer aUSDC: $((DEPLOYER_AUSDC / 1000000)) aUSDC${NC}"

# =============================================================================
# Step 8: Approve Permit2 and PositionManager
# =============================================================================
    echo -e "\n${CYAN}[8/9] Approving V4 contracts...${NC}"
    
    # Max values for uint160 and uint48
    MAX_UINT160="1461501637330902918203684832716283019655932542975"  # 2^160 - 1
    MAX_UINT48="281474976710655"  # 2^48 - 1
    
    # Approve Permit2 for both tokens
    cast send "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$(cast max-uint)" \
        --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null
    
    cast send "$AUSDC" "approve(address,uint256)" "$PERMIT2" "$(cast max-uint)" \
        --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null
    
    # Approve PositionManager via Permit2
    cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
        "$POSITION_TOKEN" "$V4_POSITION_MANAGER" "$MAX_UINT160" "$MAX_UINT48" \
        --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null
    
    cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
        "$AUSDC" "$V4_POSITION_MANAGER" "$MAX_UINT160" "$MAX_UINT48" \
        --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null
    
    echo -e "${GREEN}✓ Tokens approved for V4 PositionManager${NC}"

# =============================================================================
# Step 9: Add liquidity to V4 pool via Forge script
# =============================================================================
    echo -e "\n${CYAN}[9/9] Adding concentrated liquidity (price range 2-20)...${NC}"
    
    # Call AddLiquidity.s.sol with environment variables
    export POSITION_TOKEN="$POSITION_TOKEN"
    export TWAMM_HOOK="$TWAMM"
    export WRLP_AMOUNT="$DEPLOYER_WRLP"
    export AUSDC_AMOUNT="$DEPLOYER_AUSDC"
    
    echo "  Running AddLiquidity.s.sol..."
    
    LP_RESULT=$(forge script script/AddLiquidity.s.sol \
        --rpc-url "$RPC_URL" \
        --broadcast \
        --skip-simulation \
        -vvv 2>&1)
    
    if echo "$LP_RESULT" | grep -q "LP Position Created"; then
        TOKEN_ID=$(echo "$LP_RESULT" | grep "Token ID:" | tail -1 | awk '{print $NF}')
        echo -e "${GREEN}✓ V4 LP Position Created!${NC}"
        echo -e "  Token ID: $TOKEN_ID"
    else
        echo -e "${YELLOW}⚠ LP provision encountered issues:${NC}"
        echo "$LP_RESULT" | tail -20
    fi
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                   Position Summary                         ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

NAV_RAW=$(cast call "$BROKER" "getNetAccountValue()(uint256)" --rpc-url "$RPC_URL")
NAV=$(parse_cast_output "$NAV_RAW")

echo -e "  ${GREEN}Broker:${NC}      $BROKER"

if [ -n "$POSITION_TOKEN" ]; then
    FINAL_WRLP_RAW=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
    FINAL_WRLP=$(parse_cast_output "$FINAL_WRLP_RAW")
    FINAL_AUSDC_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
    FINAL_AUSDC=$(parse_cast_output "$FINAL_AUSDC_RAW")
    
    echo -e "  ${GREEN}Collateral:${NC}  $((FINAL_AUSDC / 1000000)) aUSDC"
    echo -e "  ${GREEN}Debt:${NC}        $((FINAL_WRLP / 1000000)) wRLP"
else
    echo -e "  ${GREEN}Collateral:${NC}  (query PositionToken to see)"
fi

echo -e "  ${GREEN}NAV:${NC}         $((NAV / 1000000)) USDC-equivalent"

if [ -n "$POSITION_TOKEN" ]; then
    echo ""
    echo -e "  ${CYAN}For LP:${NC}"
    echo -e "  Deployer wRLP: $((DEPLOYER_WRLP / 1000000))"
    echo -e "  Deployer aUSDC: $((DEPLOYER_AUSDC / 1000000))"
fi
echo ""

# Save info
cat > "$RLD_ROOT/shared/lp_position_info.json" << EOF
{
  "broker": "$BROKER",
  "position_token": "$POSITION_TOKEN",
  "deployer_wrlp": $((DEPLOYER_WRLP / 1000000)),
  "deployer_ausdc": $((DEPLOYER_AUSDC / 1000000)),
  "market_id": "$MARKET_ID",
  "twamm_hook": "$TWAMM",
  "v4_position_manager": "$V4_POSITION_MANAGER",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF

echo -e "${GREEN}Position info saved to:${NC} shared/lp_position_info.json"
echo ""
