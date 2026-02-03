#!/bin/bash
# CHAOTIC 100-Swap Stress Test
# Random sizes, random directions, time warps - pure chaos!

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;35m'
NC='\033[0m'

echo -e "${MAGENTA}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║         RLD Protocol - CHAOTIC Stress Test 🔥                  ║${NC}"
echo -e "${MAGENTA}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

cd "$(dirname "$0")/../contracts"
source .env

RPC_URL="http://localhost:8545"
USER_B_PRIVATE_KEY="0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
USER_B_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

if [ -z "$WAUSDC" ]; then
    echo -e "${RED}Error: WAUSDC not set. Run mint_and_lp_executor.sh first.${NC}"
    exit 1
fi

echo "Config:"
echo "  🎲 Chaos Mode: ENABLED"
echo "  📊 Swaps: 100"
echo "  🎯 Random sizes: 10-1000 tokens"
echo "  🐋 Whale swaps: 5x (10% chance)"
echo "  🧹 Dust swaps: 1 token (5% chance)"
echo "  ⏰ Time warps: 1-60s per swap"
echo ""

# Fund trader generously for chaos
WAUSDC_BAL=$(cast call --rpc-url "$RPC_URL" "$WAUSDC" 'balanceOf(address)(uint256)' "$USER_B_ADDRESS" 2>/dev/null | awk '{print $1}' || echo "0")
WAUSDC_BAL=${WAUSDC_BAL:-0}

if [ "$WAUSDC_BAL" -lt "50000000000" ] 2>/dev/null || [ "$WAUSDC_BAL" = "0" ]; then
    echo -e "${YELLOW}Funding trader heavily for chaos...${NC}"
    
    USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    USDC_WHALE="0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"
    AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
    FUND_AMOUNT="100000000000"  # 100k
    
    cast rpc anvil_setBalance "$USDC_WHALE" "0x56BC75E2D63100000" --rpc-url "$RPC_URL" > /dev/null
    cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
    
    cast send --rpc-url "$RPC_URL" --unlocked --from "$USDC_WHALE" "$USDC" \
        "transfer(address,uint256)" "$USER_B_ADDRESS" "$FUND_AMOUNT" --gas-limit 100000 > /dev/null
    
    cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null
    
    cast send --rpc-url "$RPC_URL" --private-key "$USER_B_PRIVATE_KEY" "$USDC" \
        "approve(address,uint256)" "$AAVE_POOL" "$FUND_AMOUNT" --gas-limit 100000 > /dev/null
    
    cast send --rpc-url "$RPC_URL" --private-key "$USER_B_PRIVATE_KEY" "$AAVE_POOL" \
        "supply(address,uint256,address,uint16)" "$USDC" "$FUND_AMOUNT" "$USER_B_ADDRESS" "0" --gas-limit 500000 > /dev/null
    
    AUSDC_BAL=$(cast call --rpc-url "$RPC_URL" "$AUSDC" 'balanceOf(address)(uint256)' "$USER_B_ADDRESS" | awk '{print $1}')
    
    cast send --rpc-url "$RPC_URL" --private-key "$USER_B_PRIVATE_KEY" "$AUSDC" \
        "approve(address,uint256)" "$WAUSDC" "$AUSDC_BAL" --gas-limit 100000 > /dev/null
    
    cast send --rpc-url "$RPC_URL" --private-key "$USER_B_PRIVATE_KEY" "$WAUSDC" \
        "wrap(uint256)" "$AUSDC_BAL" --gas-limit 200000 > /dev/null
    
    echo -e "${GREEN}✓ Funded with 100k waUSDC${NC}"
    
    # Also get some wRLP for selling
    export WAUSDC POSITION_TOKEN TWAMM_HOOK USER_B_PRIVATE_KEY
    export SWAP_AMOUNT="20000000000"
    forge script script/GoLongWRLP.s.sol --tc GoLongWRLP \
        --rpc-url "$RPC_URL" --broadcast --skip-simulation > /dev/null 2>&1 || true
    
    echo -e "${GREEN}✓ Acquired wRLP for sells${NC}"
fi

echo ""
echo -e "${MAGENTA}🔥 UNLEASHING CHAOS...${NC}"
echo ""

export WAUSDC POSITION_TOKEN TWAMM_HOOK USER_B_PRIVATE_KEY

RESULT=$(forge script script/ChaoticSwapTest.s.sol --tc ChaoticSwapTest \
    --rpc-url "$RPC_URL" \
    --broadcast \
    --skip-simulation \
    -vvv 2>&1)

if echo "$RESULT" | grep -q "CHAOS RESULTS"; then
    echo -e "${GREEN}✓ Chaos test completed!${NC}"
    echo ""
    echo "$RESULT" | grep -A 5 "=== CHAOS RESULTS ===" 
    echo ""
    echo "$RESULT" | grep -A 8 "=== TICK VOLATILITY ===" 
    echo ""
    echo "$RESULT" | grep -A 5 "=== NET P&L ===" 
else
    echo -e "${RED}✗ Chaos test failed${NC}"
    echo "$RESULT" | tail -40
    exit 1
fi
