#!/bin/bash
#
# RLD Protocol - Mint wRLP and Provide V4 LP with waUSDC (wrapped aUSDC)
#
# This script uses the wrapped aUSDC market to avoid rebasing issues.
# Flow:
# 1. Acquire USDC from whale → deposit to Aave → get aUSDC
# 2. Wrap aUSDC → get waUSDC
# 3. Create broker, deposit waUSDC, mint wRLP debt
# 4. Withdraw waUSDC and wRLP for LP
# 5. Provide liquidity to V4 pool
#
# Usage: ./scripts/mint_and_lp_wrapped.sh

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Configuration
RLD_ROOT="/home/ubuntu/RLD"
CONTRACTS_DIR="$RLD_ROOT/contracts"
RPC_URL="http://localhost:8545"

# Amounts (6 decimals)
COLLATERAL_AMOUNT=10000000   # 10M
DEBT_AMOUNT=500000           # 500k wRLP
LP_AMOUNT=100000             # 100k of each for LP

# Convert to wei
COLLATERAL_WEI=$(echo "$COLLATERAL_AMOUNT * 1000000" | bc)
DEBT_WEI=$(echo "$DEBT_AMOUNT * 1000000" | bc)
LP_WEI=$(echo "$LP_AMOUNT * 1000000" | bc)

# Mainnet addresses
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0xCFFAd3200574698b78f32232aa9D63eABD290703"
V4_POSITION_MANAGER="0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"

# Wrapped market addresses (from deployment)
WAUSDC="0xcb68357b50A5e759E9C530f172A8174EfA1E350D"
WRAPPED_MARKET_ID="0x9adc509a91014b06fe2b952bd20a4e188901e2292f3a5c238630e5d0fd313d8f"
WRAPPED_BROKER_FACTORY="0x9554b52516f306360a239746F70f88c23D187b63"
WRAPPED_POSITION_TOKEN="0x9ed4F4724b521326a9d9d2420252440bD05556c4"

parse_cast_output() {
    echo "$1" | awk '{print $1}'
}

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   RLD Protocol - Mint & LP with Wrapped aUSDC (waUSDC)    ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Collateral:  ${YELLOW}$COLLATERAL_AMOUNT${NC} waUSDC"
echo -e "  Debt:        ${YELLOW}$DEBT_AMOUNT${NC} wRLP"
echo -e "  LP Amount:   ${YELLOW}$LP_AMOUNT${NC} each token"
echo ""

# Load environment
cd "$CONTRACTS_DIR"
source .env

if [ -z "$PRIVATE_KEY" ]; then
    echo -e "${RED}✗ Error: PRIVATE_KEY not set${NC}"
    exit 1
fi

DEPLOYER=$(cast wallet address --private-key "$PRIVATE_KEY" 2>/dev/null)
echo -e "  Deployer: ${CYAN}$DEPLOYER${NC}"
echo -e "  waUSDC:   ${CYAN}$WAUSDC${NC}"
echo -e "  MarketId: ${CYAN}$WRAPPED_MARKET_ID${NC}"

# =============================================================================
# Step 1: Acquire aUSDC from whale
# =============================================================================
echo -e "\n${CYAN}[1/10] Acquiring aUSDC from whale...${NC}"

WHALE_BALANCE_RAW=$(cast call "$USDC" "balanceOf(address)(uint256)" "$USDC_WHALE" --rpc-url "$RPC_URL")
WHALE_BALANCE=$(parse_cast_output "$WHALE_BALANCE_RAW")
echo "  Whale USDC: $((WHALE_BALANCE / 1000000))"

cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$COLLATERAL_WEI" \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" --quiet > /dev/null

cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
    "$USDC" "$COLLATERAL_WEI" "$DEPLOYER" 0 \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" --quiet > /dev/null

cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

AUSDC_BALANCE=$(parse_cast_output "$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")")
echo -e "${GREEN}✓ Deployer aUSDC: $((AUSDC_BALANCE / 1000000))${NC}"

# =============================================================================
# Step 2: Wrap aUSDC → waUSDC
# =============================================================================
echo -e "\n${CYAN}[2/10] Wrapping aUSDC → waUSDC...${NC}"

# Approve waUSDC wrapper
cast send "$AUSDC" "approve(address,uint256)" "$WAUSDC" "$AUSDC_BALANCE" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

# Wrap
cast send "$WAUSDC" "wrap(uint256)" "$AUSDC_BALANCE" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

WAUSDC_BALANCE=$(parse_cast_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")")
echo -e "${GREEN}✓ Deployer waUSDC: $((WAUSDC_BALANCE / 1000000)) (shares)${NC}"

# =============================================================================
# Step 3: Advance time for TWAMM
# =============================================================================
echo -e "\n${CYAN}[3/10] Priming TWAMM oracle...${NC}"
cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc evm_mine --rpc-url "$RPC_URL" > /dev/null
echo -e "${GREEN}✓ Advanced time by 2 hours${NC}"

# =============================================================================
# Step 4: Create PrimeBroker
# =============================================================================
echo -e "\n${CYAN}[4/10] Creating PrimeBroker...${NC}"

SALT=$(cast keccak "wrapped-lp-$(date +%s)")
BROKER_TX=$(cast send "$WRAPPED_BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --json)

BROKER=$(echo "$BROKER_TX" | python3 -c "
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

echo -e "${GREEN}✓ Broker: $BROKER${NC}"

# =============================================================================
# Step 5: Transfer waUSDC collateral to broker
# =============================================================================
echo -e "\n${CYAN}[5/10] Transferring waUSDC to broker...${NC}"

cast send "$WAUSDC" "transfer(address,uint256)" "$BROKER" "$WAUSDC_BALANCE" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

BROKER_WAUSDC=$(parse_cast_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")")
echo -e "${GREEN}✓ Broker waUSDC: $((BROKER_WAUSDC / 1000000))${NC}"

# =============================================================================
# Step 6: Mint wRLP debt
# =============================================================================
echo -e "\n${CYAN}[6/10] Minting $DEBT_AMOUNT wRLP debt...${NC}"

cast send "$BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$WRAPPED_MARKET_ID" 0 "$DEBT_WEI" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

BROKER_WRLP=$(parse_cast_output "$(cast call "$WRAPPED_POSITION_TOKEN" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")")
echo -e "${GREEN}✓ Broker wRLP: $((BROKER_WRLP / 1000000))${NC}"

# =============================================================================
# Step 7: Withdraw tokens for LP
# =============================================================================
echo -e "\n${CYAN}[7/10] Withdrawing tokens for LP...${NC}"

cast send "$BROKER" "withdrawPositionToken(address,uint256)" "$DEPLOYER" "$LP_WEI" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

cast send "$BROKER" "withdrawCollateral(address,uint256)" "$DEPLOYER" "$LP_WEI" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

DEPLOYER_WRLP=$(parse_cast_output "$(cast call "$WRAPPED_POSITION_TOKEN" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")")
DEPLOYER_WAUSDC=$(parse_cast_output "$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")")

echo -e "${GREEN}✓ Deployer wRLP:   $((DEPLOYER_WRLP / 1000000))${NC}"
echo -e "${GREEN}✓ Deployer waUSDC: $((DEPLOYER_WAUSDC / 1000000))${NC}"

# =============================================================================
# Step 8: Approve V4 contracts
# =============================================================================
echo -e "\n${CYAN}[8/10] Approving V4 contracts...${NC}"

MAX_UINT160="1461501637330902918203684832716283019655932542975"
MAX_UINT48="281474976710655"

cast send "$WRAPPED_POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$(cast max-uint)" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

cast send "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$(cast max-uint)" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WRAPPED_POSITION_TOKEN" "$V4_POSITION_MANAGER" "$MAX_UINT160" "$MAX_UINT48" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

cast send "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WAUSDC" "$V4_POSITION_MANAGER" "$MAX_UINT160" "$MAX_UINT48" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

echo -e "${GREEN}✓ Tokens approved for V4 PositionManager${NC}"

# =============================================================================
# Step 9: Query pool info
# =============================================================================
echo -e "\n${CYAN}[9/10] Querying V4 pool state...${NC}"

# Get pool info from CheckPoolState or similar
echo "  waUSDC: $WAUSDC"
echo "  wRLP:   $WRAPPED_POSITION_TOKEN"
echo "  V4 PM:  $V4_POSITION_MANAGER"

# =============================================================================
# Step 10: Add LP via Forge script
# =============================================================================
echo -e "\n${CYAN}[10/10] Adding concentrated liquidity via Forge script...${NC}"

# Set environment variables for Forge script
export WAUSDC="$WAUSDC"
export POSITION_TOKEN="$WRAPPED_POSITION_TOKEN"
export TWAMM_HOOK="0x7e0C07EEabb2459D70dba5b8d100Dca44c652aC0"
export WRLP_AMOUNT="$DEPLOYER_WRLP"
export AUSDC_AMOUNT="$DEPLOYER_WAUSDC"

echo "  waUSDC:       $WAUSDC"
echo "  wRLP:         $WRAPPED_POSITION_TOKEN"
echo "  LP waUSDC:    $((DEPLOYER_WAUSDC / 1000000))"
echo "  LP wRLP:      $((DEPLOYER_WRLP / 1000000))"
echo ""

LP_RESULT=$(forge script script/AddLiquidityWrapped.s.sol \
    --rpc-url "$RPC_URL" \
    --broadcast \
    --skip-simulation \
    -vvv 2>&1)

if echo "$LP_RESULT" | grep -q "LP Position Created"; then
    TOKEN_ID=$(echo "$LP_RESULT" | grep "Token ID:" | tail -1 | awk '{print $NF}')
    echo -e "${GREEN}✓ V4 LP Position Created!${NC}"
    echo -e "  Token ID: ${YELLOW}$TOKEN_ID${NC}"
    
    # Get tick range from output
    TICK_LOWER=$(echo "$LP_RESULT" | grep "Tick lower:" | awk '{print $NF}')
    TICK_UPPER=$(echo "$LP_RESULT" | grep "Tick upper:" | awk '{print $NF}')
    echo -e "  Tick Range: [$TICK_LOWER, $TICK_UPPER]"
    echo -e "  Price Range: waUSDC/wRLP = [2, 20]"
else
    echo -e "${RED}✗ LP provision failed${NC}"
    echo "$LP_RESULT" | tail -30
    exit 1
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║               Wrapped Market Position Summary              ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${GREEN}Broker:${NC}         $BROKER"
echo -e "  ${GREEN}Broker waUSDC:${NC}  $((BROKER_WAUSDC / 1000000 - LP_AMOUNT))"
echo -e "  ${GREEN}Broker wRLP:${NC}    $((BROKER_WRLP / 1000000 - LP_AMOUNT))"
echo ""
echo -e "  ${CYAN}For LP:${NC}"
echo -e "  Deployer wRLP:   $((DEPLOYER_WRLP / 1000000))"
echo -e "  Deployer waUSDC: $((DEPLOYER_WAUSDC / 1000000))"
echo ""
echo -e "${GREEN}✓ waUSDC is non-rebasing - V4 LP should work!${NC}"
echo ""
