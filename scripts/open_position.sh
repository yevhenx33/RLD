#!/bin/bash
#
# RLD Protocol - Open Position Script
#
# This script opens a test position after protocol deployment.
# Run AFTER deploy_local.sh completes.
#
# Usage: ./scripts/open_position.sh [COLLATERAL_AMOUNT] [DEBT_AMOUNT]
#
# Example:
#   ./scripts/open_position.sh 10000000 200000   # 10M aUSDC collateral, 200k wRLP debt
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

# Default amounts (6 decimals)
COLLATERAL_AMOUNT=${1:-10000000}  # 10M USDC/aUSDC
DEBT_AMOUNT=${2:-200000}          # 200k wRLP

# Convert to wei (6 decimals)
COLLATERAL_WEI=$(echo "$COLLATERAL_AMOUNT * 1000000" | bc)
DEBT_WEI=$(echo "$DEBT_AMOUNT * 1000000" | bc)

# Mainnet addresses
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0xCFFAd3200574698b78f32232aa9D63eABD290703"

# Helper function to parse cast output (strips scientific notation suffix like "123 [1.23e5]")
parse_cast_output() {
    echo "$1" | awk '{print $1}'
}

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║              RLD Protocol - Open Position                  ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Collateral: ${YELLOW}$COLLATERAL_AMOUNT${NC} aUSDC"
echo -e "  Debt:       ${YELLOW}$DEBT_AMOUNT${NC} wRLP"
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
echo -e "\n${CYAN}[1/6] Loading deployment addresses...${NC}"

if [ ! -f "deployments.json" ]; then
    echo -e "${RED}✗ Error: deployments.json not found. Run deploy_local.sh first.${NC}"
    exit 1
fi

BROKER_FACTORY=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('BrokerFactory', ''))")
MARKET_ID=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('MarketId', ''))")
POSITION_TOKEN=$(cat deployments.json | python3 -c "import sys,json; print(json.load(sys.stdin).get('PositionToken', ''))")

if [ -z "$BROKER_FACTORY" ] || [ -z "$MARKET_ID" ]; then
    echo -e "${RED}✗ Error: Missing BrokerFactory or MarketId in deployments.json${NC}"
    exit 1
fi

echo -e "${GREEN}✓ BrokerFactory: $BROKER_FACTORY${NC}"
echo -e "${GREEN}✓ MarketId: $MARKET_ID${NC}"

# =============================================================================
# Step 1: Impersonate whale and get aUSDC
# =============================================================================
echo -e "\n${CYAN}[2/6] Impersonating USDC whale and depositing to Aave...${NC}"

# Check whale balance (parse to remove scientific notation)
WHALE_BALANCE_RAW=$(cast call "$USDC" "balanceOf(address)(uint256)" "$USDC_WHALE" --rpc-url "$RPC_URL")
WHALE_BALANCE=$(parse_cast_output "$WHALE_BALANCE_RAW")
WHALE_BALANCE_NUM=$((WHALE_BALANCE / 1000000))
echo "  Whale USDC balance: $WHALE_BALANCE_NUM USDC"

if [ "$WHALE_BALANCE" -lt "$COLLATERAL_WEI" ]; then
    echo -e "${RED}✗ Error: Whale has insufficient balance${NC}"
    exit 1
fi

# Impersonate whale
cast rpc anvil_impersonateAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

# Approve Aave Pool
echo "  Approving Aave Pool..."
cast send "$USDC" "approve(address,uint256)" "$AAVE_POOL" "$COLLATERAL_WEI" \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" --quiet > /dev/null

# Supply to Aave (aUSDC goes to deployer)
echo "  Supplying $COLLATERAL_AMOUNT USDC to Aave..."
cast send "$AAVE_POOL" "supply(address,uint256,address,uint16)" \
    "$USDC" "$COLLATERAL_WEI" "$DEPLOYER" 0 \
    --from "$USDC_WHALE" --unlocked --rpc-url "$RPC_URL" --quiet > /dev/null

# Stop impersonation
cast rpc anvil_stopImpersonatingAccount "$USDC_WHALE" --rpc-url "$RPC_URL" > /dev/null

# Verify aUSDC received
AUSDC_BALANCE_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$DEPLOYER" --rpc-url "$RPC_URL")
AUSDC_BALANCE=$(parse_cast_output "$AUSDC_BALANCE_RAW")
AUSDC_BALANCE_NUM=$((AUSDC_BALANCE / 1000000))
echo -e "${GREEN}✓ Deployer aUSDC balance: $AUSDC_BALANCE_NUM aUSDC${NC}"

# =============================================================================
# Step 2: Advance time for TWAMM oracle
# =============================================================================
echo -e "\n${CYAN}[3/6] Priming TWAMM oracle (advancing time)...${NC}"

cast rpc evm_increaseTime 7200 --rpc-url "$RPC_URL" > /dev/null
cast rpc evm_mine --rpc-url "$RPC_URL" > /dev/null

echo -e "${GREEN}✓ Advanced time by 2 hours${NC}"

# =============================================================================
# Step 3: Create PrimeBroker
# =============================================================================
echo -e "\n${CYAN}[4/6] Creating PrimeBroker...${NC}"

SALT=$(cast keccak "position-$(date +%s)")
BROKER_TX=$(cast send "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --json)

# Extract broker address from BrokerCreated(address broker, address owner, uint256 tokenId) event
# The broker address is in the 'data' field of the log (first 32 bytes = broker address)
# Event signature: 0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71
BROKER=$(echo "$BROKER_TX" | python3 -c "
import sys, json
data = json.load(sys.stdin)
broker_created_sig = '0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71'
for log in data.get('logs', []):
    topics = log.get('topics', [])
    # Look for BrokerCreated event specifically
    if topics and topics[0].lower() == broker_created_sig:
        # Broker address is in the data field
        data_field = log.get('data', '')
        if data_field.startswith('0x') and len(data_field) >= 66:
            # First 64 chars after 0x is the broker address (padded)
            print('0x' + data_field[26:66])
            break
    # Fallback: ERC721 Transfer (from=0, to=owner, tokenId=broker)
    elif len(topics) >= 4 and topics[1] == '0x' + '0'*64:
        # tokenId is the broker address
        addr = '0x' + topics[3][-40:]
        print(addr)
        break
")

if [ -z "$BROKER" ] || [ "$BROKER" = "0x0000000000000000000000000000000000000000" ]; then
    echo -e "${RED}✗ Error: Could not extract broker address from transaction logs${NC}"
    echo "Transaction logs:"
    echo "$BROKER_TX" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin).get('logs',[]),indent=2))"
    exit 1
fi

echo -e "${GREEN}✓ Broker created: $BROKER${NC}"

# Verify broker has code
BROKER_CODE=$(cast code "$BROKER" --rpc-url "$RPC_URL" 2>/dev/null || echo "0x")
if [ "$BROKER_CODE" = "0x" ]; then
    echo -e "${RED}✗ Error: Broker has no code at $BROKER${NC}"
    exit 1
fi

# =============================================================================
# Step 4: Transfer collateral to broker
# =============================================================================
echo -e "\n${CYAN}[5/6] Transferring collateral to broker...${NC}"

cast send "$AUSDC" "transfer(address,uint256)" "$BROKER" "$AUSDC_BALANCE" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

BROKER_AUSDC_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
BROKER_AUSDC=$(parse_cast_output "$BROKER_AUSDC_RAW")
BROKER_AUSDC_NUM=$((BROKER_AUSDC / 1000000))
echo -e "${GREEN}✓ Broker aUSDC balance: $BROKER_AUSDC_NUM aUSDC${NC}"

# =============================================================================
# Step 5: Mint debt (open position)
# =============================================================================
echo -e "\n${CYAN}[6/6] Minting $DEBT_AMOUNT wRLP debt...${NC}"

cast send "$BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$DEBT_WEI" \
    --private-key "$PRIVATE_KEY" --rpc-url "$RPC_URL" --quiet > /dev/null

echo -e "${GREEN}✓ Position opened!${NC}"

# =============================================================================
# Verify position
# =============================================================================
echo ""
echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║                    Position Summary                        ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Get NAV
NAV_RAW=$(cast call "$BROKER" "getNetAccountValue()(uint256)" --rpc-url "$RPC_URL")
NAV=$(parse_cast_output "$NAV_RAW")
NAV_NUM=$((NAV / 1000000))

# Get wRLP balance
if [ -n "$POSITION_TOKEN" ]; then
    WRLP_BALANCE_RAW=$(cast call "$POSITION_TOKEN" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
    WRLP_BALANCE=$(parse_cast_output "$WRLP_BALANCE_RAW")
    WRLP_NUM=$((WRLP_BALANCE / 1000000))
else
    WRLP_NUM=$DEBT_AMOUNT
fi

# Get collateral
FINAL_AUSDC_RAW=$(cast call "$AUSDC" "balanceOf(address)(uint256)" "$BROKER" --rpc-url "$RPC_URL")
FINAL_AUSDC=$(parse_cast_output "$FINAL_AUSDC_RAW")
FINAL_AUSDC_NUM=$((FINAL_AUSDC / 1000000))

echo -e "  ${GREEN}Broker:${NC}      $BROKER"
echo -e "  ${GREEN}Collateral:${NC}  $FINAL_AUSDC_NUM aUSDC"
echo -e "  ${GREEN}Debt:${NC}        $WRLP_NUM wRLP"
echo -e "  ${GREEN}NAV:${NC}         $NAV_NUM USDC-equivalent"
echo ""

# Calculate health
if [ "$WRLP_NUM" -gt 0 ]; then
    HEALTH=$((FINAL_AUSDC_NUM * 100 / WRLP_NUM))
    echo -e "  ${GREEN}Health:${NC}      ${HEALTH}x collateralization"
fi
echo ""

# Save position info
cat > "$RLD_ROOT/shared/position_info.json" << POSITION_JSON
{
  "broker": "$BROKER",
  "collateral_ausdc": $FINAL_AUSDC_NUM,
  "debt_wrlp": $WRLP_NUM,
  "nav_usdc": $NAV_NUM,
  "market_id": "$MARKET_ID",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
POSITION_JSON

echo -e "  ${GREEN}Position info saved to:${NC} shared/position_info.json"
echo ""
