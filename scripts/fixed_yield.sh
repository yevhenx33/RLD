#!/bin/bash
# ============================================================================
# fixed_yield.sh - Create a Fixed Yield Bond
# ============================================================================
#
# Creates a synthetic fixed-yield bond by:
# 1. Acquiring waUSDC from aUSDC (whale impersonation)
# 2. Depositing waUSDC collateral into broker
# 3. Minting wRLP as a hedge
# 4. Setting up TWAMM order to sell wRLP over time
#
# Usage:
#   ./scripts/fixed_yield.sh [PRINCIPAL] [DURATION_DAYS] [HEDGE_RATIO_BPS]
#
# Examples:
#   ./scripts/fixed_yield.sh                    # 50k for 30 days, 1% hedge
#   ./scripts/fixed_yield.sh 100000 90 200      # 100k for 90 days, 2% hedge
#
# Environment:
#   PRIVATE_KEY - Required, deployer private key
#   RPC_URL     - Optional, defaults to local anvil
#
# Prerequisites:
#   1. Start Anvil fork:      ./scripts/deploy_local.sh
#   2. Deploy wrapped market: /deploy-wrapped-lp workflow
#   3. Then run this script
#
# ============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration from args with defaults
PRINCIPAL=${1:-50000}              # Default 50k waUSDC
DURATION_DAYS=${2:-30}             # Default 30 days
HEDGE_RATIO_BPS=${3:-100}          # Default 1% = 100 bps

# Addresses
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0xCFFAd3200574698b78f32232aa9D63eABD290703"

# Path to wrapped market JSON (created by deploy_wrapped_market.sh)
MARKET_JSON="/home/ubuntu/RLD/contracts/wrapped_market.json"

# Convert to wei format (6 decimals for USDC)
PRINCIPAL_WEI=$(echo "$PRINCIPAL * 1000000" | bc)

echo -e "${CYAN}==========================================${NC}"
echo -e "${CYAN}   FIXED YIELD BOND CREATION${NC}"
echo -e "${CYAN}==========================================${NC}"
echo ""
echo "Principal:      $PRINCIPAL waUSDC"
echo "Duration:       $DURATION_DAYS days"
echo "Hedge Ratio:    $HEDGE_RATIO_BPS bps"
echo ""

# Change to contracts directory  
cd /home/ubuntu/RLD/contracts

# Load PRIVATE_KEY from .env
source .env
if [ -z "$PRIVATE_KEY" ]; then
    echo -e "${RED}Error: PRIVATE_KEY environment variable is required${NC}"
    exit 1
fi

# Load addresses from wrapped_market.json if it exists
if [ -f "$MARKET_JSON" ]; then
    echo -e "${GREEN}Loading addresses from wrapped_market.json${NC}"
    WAUSDC=$(jq -r '.waUSDC' "$MARKET_JSON")
    POSITION_TOKEN=$(jq -r '.positionToken' "$MARKET_JSON")
    BROKER_FACTORY=$(jq -r '.brokerFactory' "$MARKET_JSON")
    MARKET_ID=$(jq -r '.marketId' "$MARKET_JSON")
else
    echo -e "${YELLOW}⚠ wrapped_market.json not found, using fallback addresses${NC}"
    echo -e "${YELLOW}  Run ./scripts/deploy_wrapped_market.sh first${NC}"
    # Fallback to hardcoded defaults (from most recent deployment)
    WAUSDC="0xa1da18755De55f3929620a530968a28F16EA981D"
    POSITION_TOKEN="0xC6C7c316E4CBbD73cC36280E2E290487a42C11d0"
    BROKER_FACTORY="0xBB20A1C05f54c9eF20a9f5A20587f345932fF236"
    MARKET_ID="0x56d02bade54cf9cd09b965dbc4e652aed2668f83e0d1686ae3a4c285551755c3"
fi

DEPLOYER=$(cast wallet address --private-key "$PRIVATE_KEY" 2>/dev/null)

# Set RPC URL with default
RPC_URL=${RPC_URL:-http://localhost:8545}

echo "Deployer:       $DEPLOYER"
echo "waUSDC:         $WAUSDC"
echo "wRLP:           $POSITION_TOKEN"
echo "Broker Factory: $BROKER_FACTORY"
echo "RPC:            $RPC_URL"
echo ""

# Check if waUSDC wrapper is deployed
WAUSDC_CODE=$(cast code $WAUSDC --rpc-url $RPC_URL 2>/dev/null || echo "0x")
if [ "$WAUSDC_CODE" == "0x" ]; then
    echo -e "${RED}✗ Error: waUSDC wrapper not deployed at $WAUSDC${NC}"
    echo ""
    echo "Please run the /deploy-wrapped-lp workflow first:"
    echo "  1. Start Anvil:  ./scripts/deploy_local.sh"
    echo "  2. Deploy market: Run /deploy-wrapped-lp workflow"
    echo ""
    exit 1
fi

# ============================================================================
# STEP 1: Acquire waUSDC
# ============================================================================
echo -e "${YELLOW}[1/5] Acquiring waUSDC...${NC}"

# Impersonate whale, get USDC → aUSDC → waUSDC
cast rpc anvil_impersonateAccount $USDC_WHALE --rpc-url $RPC_URL > /dev/null

# Approve Aave pool
echo "  Approving USDC for Aave..."
cast send $USDC "approve(address,uint256)" $AAVE_POOL $PRINCIPAL_WEI \
    --from $USDC_WHALE --unlocked --rpc-url $RPC_URL > /dev/null

# Supply to Aave (get aUSDC for deployer)
echo "  Supplying to Aave..."
cast send $AAVE_POOL "supply(address,uint256,address,uint16)" \
    $USDC $PRINCIPAL_WEI $DEPLOYER 0 \
    --from $USDC_WHALE --unlocked --rpc-url $RPC_URL > /dev/null

cast rpc anvil_stopImpersonatingAccount $USDC_WHALE --rpc-url $RPC_URL > /dev/null

# Wrap aUSDC to waUSDC
echo "  Wrapping aUSDC to waUSDC..."

# Get current aUSDC balance FIRST (might be slightly more due to interest)
AUSDC_BAL=$(cast call $AUSDC "balanceOf(address)" $DEPLOYER --rpc-url $RPC_URL)
AUSDC_BAL_DEC=$(cast --to-dec $AUSDC_BAL)

# Approve the FULL balance (not principal)
cast send $AUSDC "approve(address,uint256)" $WAUSDC $AUSDC_BAL_DEC \
    --private-key $PRIVATE_KEY --rpc-url $RPC_URL > /dev/null

# Use wrap() function
cast send $WAUSDC "wrap(uint256)" $AUSDC_BAL_DEC \
    --private-key $PRIVATE_KEY --rpc-url $RPC_URL > /dev/null

WAUSDC_BAL=$(cast call $WAUSDC "balanceOf(address)" $DEPLOYER --rpc-url $RPC_URL)
WAUSDC_BAL_DEC=$(cast --to-dec $WAUSDC_BAL)
echo -e "  ${GREEN}✓ Acquired $(echo "scale=2; $WAUSDC_BAL_DEC / 1000000" | bc) waUSDC${NC}"

# ============================================================================
# STEP 2: Advance time for oracle + Query current Aave rate
# ============================================================================
echo -e "${YELLOW}[2/5] Priming oracle...${NC}"
cast rpc anvil_mine 1 --rpc-url $RPC_URL > /dev/null
# Advance 2 hours
cast rpc evm_increaseTime 7200 --rpc-url $RPC_URL > /dev/null
cast rpc anvil_mine 1 --rpc-url $RPC_URL > /dev/null
echo -e "  ${GREEN}✓ Advanced time by 2 hours${NC}"

# Query live Aave USDC borrow rate from indexer API
echo -e "${YELLOW}[3/6] Querying Aave borrow rate from API...${NC}"

RATE_FRACTION=$(curl -sf "http://localhost:5000/api/v1/oracle/usdc-borrow-apy" | jq -r '.borrow_apy')
if [ -z "$RATE_FRACTION" ] || [ "$RATE_FRACTION" == "null" ]; then
    echo -e "${RED}✗ Error: Could not fetch live rate from local API. Ensure indexer is running.${NC}"
    exit 1
fi

RATE_PERCENT=$(echo "scale=4; $RATE_FRACTION * 100" | bc)
echo -e "  Current Aave USDC Borrow Rate: ${RATE_PERCENT}%"

# Convert to WAD format (1e18 = 100%)
CURRENT_RATE_WAD=$(python3 -c "print(int($RATE_FRACTION * 10**18))")

# Default utilization and reserve factor (could also query from Aave)
UTILIZATION_WAD="900000000000000000"    # 90% = 0.9e18
RESERVE_FACTOR_WAD="50000000000000000"  # 5% = 0.05e18

echo -e "  ${GREEN}✓ Rate queried: ${CURRENT_RATE_WAD} wei (${RATE_PERCENT}%)${NC}"

# ============================================================================
# STEP 4-6: Run Forge script for broker creation, deposit, and TWAMM order
# ============================================================================
echo -e "${YELLOW}[4-6] Running Fixed Yield Forge script...${NC}"
echo ""

PRINCIPAL=$WAUSDC_BAL_DEC \
    DURATION_DAYS=$DURATION_DAYS \
    CURRENT_RATE=$CURRENT_RATE_WAD \
    UTILIZATION=$UTILIZATION_WAD \
    RESERVE_FACTOR=$RESERVE_FACTOR_WAD \
    POSITION_TOKEN=$POSITION_TOKEN \
    WAUSDC=$WAUSDC \
    BROKER_FACTORY=$BROKER_FACTORY \
    MARKET_ID=$MARKET_ID \
    SKIP_TWAMM=${SKIP_TWAMM:-true} \
    forge script script/FixedYieldBond.s.sol \
        --rpc-url $RPC_URL \
        --broadcast \
        -vvv

echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}   FIXED YIELD BOND CREATED!${NC}"
echo -e "${GREEN}==========================================${NC}"
