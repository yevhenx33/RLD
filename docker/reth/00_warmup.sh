#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# 00_warmup.sh — Capture Anvil state for Reth genesis
# ═══════════════════════════════════════════════════════════════
# Dumps state from an ALREADY RUNNING Anvil that has the full
# RLD protocol deployed. Converts to Reth genesis.json.
#
# Prerequisites:
#   - Anvil running on port 8545 with protocol deployed
#     (i.e. you ran ./docker/restart.sh first)
#   - deployment.json exists in docker/
#
# Usage:
#   ./docker/restart.sh                  # 1. Deploy on Anvil first
#   ./docker/reth/00_warmup.sh           # 2. Capture state for Reth
#   ./docker/reth/restart-reth.sh        # 3. Switch to Reth
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
RLD_ROOT="$(dirname "$DOCKER_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

header()  { echo -e "\n${BLUE}═══ $1 ═══${NC}\n"; }
step()    { echo -e "${YELLOW}[$1] $2${NC}"; }
ok()      { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()    { echo -e "${RED}  ✗ $1${NC}"; }
info()    { echo -e "${CYAN}  ℹ $1${NC}"; }

GENESIS_OUT="${SCRIPT_DIR}/genesis.json"
DUMP_FILE="/tmp/anvil-dump-for-reth.json"
ANVIL_RPC="${ANVIL_RPC:-http://localhost:8545}"

# ─── Load env for fund-keys ──────────────────────────────────
if [ -f "$DOCKER_DIR/.env" ]; then
    source <(grep -E '^(DEPLOYER_KEY|USER_A_KEY|USER_B_KEY|USER_C_KEY|MM_KEY|CHAOS_KEY)=' "$DOCKER_DIR/.env" | sed 's/^/export /')
fi

# ═════════════════════════════════════════════════════════════
# STEP 1: Verify Anvil is running with deployed protocol
# ═════════════════════════════════════════════════════════════
header "STEP 1: VERIFY ANVIL"

step "1a" "Checking Anvil is reachable..."
if ! cast block-number --rpc-url "$ANVIL_RPC" > /dev/null 2>&1; then
    fail "Anvil not running on $ANVIL_RPC"
    echo ""
    echo "  Run ./docker/restart.sh first to deploy the protocol on Anvil."
    exit 1
fi
BLOCK=$(cast block-number --rpc-url "$ANVIL_RPC")
ok "Anvil running at block $BLOCK"

step "1b" "Checking protocol is deployed..."
DEPLOY_JSON="$DOCKER_DIR/deployment.json"
if [ ! -f "$DEPLOY_JSON" ] || [ "$(cat "$DEPLOY_JSON")" = "{}" ]; then
    fail "deployment.json is empty — protocol not deployed"
    echo ""
    echo "  Run ./docker/restart.sh first to deploy the protocol on Anvil."
    exit 1
fi

# Verify a key contract has code
VERIFY_ADDR=$(jq -r '.twamm_hook // .rld_core // empty' "$DEPLOY_JSON")
if [ -z "$VERIFY_ADDR" ]; then
    fail "No known contract found in deployment.json (checked twamm_hook, rld_core)"
    exit 1
fi
CODE=$(cast code "$VERIFY_ADDR" --rpc-url "$ANVIL_RPC" 2>/dev/null || echo "0x")
if [ "$CODE" = "0x" ] || [ -z "$CODE" ]; then
    fail "Contract at $VERIFY_ADDR has no code — deployment may have failed"
    exit 1
fi
ok "Protocol verified at $VERIFY_ADDR"

# ═════════════════════════════════════════════════════════════
# STEP 2: Touch external contracts (force Anvil to cache)
# ═════════════════════════════════════════════════════════════
header "STEP 2: WARM-UP (caching contract dependencies)"

step "2a" "Touching key mainnet contracts..."
CONTRACTS=(
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # USDC proxy
    "0x43506849D7C04F9138D1A2050bbF3A0c054402dd"  # USDC implementation
    "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"  # aUSDC
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"  # Aave V3 Pool
    "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e"  # Aave PoolAddressesProvider
    "0x64b761D848206f447Fe2dd461b0c635Ec39EbB27"  # Aave PoolConfigurator
    "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"  # sUSDe
    "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"  # USDC Whale
    "0x000000000004444c5dc75cB358380D2e3dE08A90"  # Uniswap V4 PoolManager
    "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"  # V4 Position Manager
    "0x000000000022D473030F116dDEE9F6B43aC78BA3"  # Permit2
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"  # Morpho Blue
    "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"  # USDe
    "0x6c3ea9036406852006290770BEdFcAbA0e23A0e8"  # PYUSD
)

for addr in "${CONTRACTS[@]}"; do
    cast code "$addr" --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
    cast balance "$addr" --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
done
ok "Touched ${#CONTRACTS[@]} external contracts"

step "2b" "Warming Aave Pool internals..."
cast call "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2" \
    "getReserveData(address)" "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" \
    --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
ok "Aave reserve data cached"

# Also touch deployed contracts from deployment.json
step "2c" "Warming deployed protocol contracts..."
for key in rld_core twamm_hook wausdc position_token broker_factory broker_router swap_router mock_oracle bond_factory basis_trade_factory broker_executor; do
    addr=$(jq -r ".$key // empty" "$DEPLOY_JSON")
    if [ -n "$addr" ] && [ "$addr" != "null" ]; then
        cast code "$addr" --rpc-url "$ANVIL_RPC" > /dev/null 2>&1 || true
    fi
done
ok "Deployed contracts warmed"

# ═════════════════════════════════════════════════════════════
# STEP 3: Dump Anvil state
# ═════════════════════════════════════════════════════════════
header "STEP 3: DUMP STATE"

step "3a" "Calling anvil_dumpState..."
cast rpc anvil_dumpState --rpc-url "$ANVIL_RPC" 2>/dev/null > /tmp/anvil-dump-raw.txt

python3 -c "
import sys, json, gzip

raw = open('/tmp/anvil-dump-raw.txt').read().strip()

# Remove surrounding quotes
if raw.startswith('\"') and raw.endswith('\"'):
    raw = raw[1:-1]

# Remove 0x prefix
if raw.startswith('0x'):
    raw = raw[2:]

# Decode hex → bytes
raw_bytes = bytes.fromhex(raw)

# Check if gzip-compressed (magic bytes 1f 8b)
if raw_bytes[:2] == b'\x1f\x8b':
    print('  Detected gzip-compressed dump, decompressing...')
    decompressed = gzip.decompress(raw_bytes)
    data = json.loads(decompressed)
else:
    data = json.loads(raw_bytes)

# Write to output file
json.dump(data, open('$DUMP_FILE', 'w'))
accounts = data.get('accounts', data)
print(f'  Dumped {len(accounts)} accounts')
"

rm -f /tmp/anvil-dump-raw.txt

DUMP_SIZE=$(du -h "$DUMP_FILE" | cut -f1)
ok "State dumped to $DUMP_FILE ($DUMP_SIZE)"

# ═════════════════════════════════════════════════════════════
# STEP 4: Convert to genesis
# ═════════════════════════════════════════════════════════════
header "STEP 4: CONVERT TO GENESIS"

FUND_ARGS=""
for VAR in DEPLOYER_KEY USER_A_KEY USER_B_KEY USER_C_KEY MM_KEY CHAOS_KEY; do
    if [ -n "${!VAR:-}" ]; then
        FUND_ARGS="$FUND_ARGS ${!VAR}"
    fi
done

step "4a" "Running convert_state.py..."
python3 "$SCRIPT_DIR/convert_state.py" \
    --input "$DUMP_FILE" \
    --output "$GENESIS_OUT" \
    --chain-id 31337 \
    --fund-keys $FUND_ARGS

GENESIS_SIZE=$(du -h "$GENESIS_OUT" | cut -f1)
ok "Genesis written to $GENESIS_OUT ($GENESIS_SIZE)"

# ═════════════════════════════════════════════════════════════
# STEP 5: Save deployment.json snapshot
# ═════════════════════════════════════════════════════════════
header "STEP 5: SAVE DEPLOYMENT CONFIG"

step "5a" "Copying deployment.json..."
cp "$DEPLOY_JSON" "$SCRIPT_DIR/deployment-snapshot.json"
ok "Saved to docker/reth/deployment-snapshot.json"

# Cleanup
rm -f "$DUMP_FILE"
ok "Temporary dump file removed"

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Genesis bootstrap complete!${NC}"
echo -e "${GREEN}   Genesis: $GENESIS_OUT ($GENESIS_SIZE)${NC}"
echo -e "${GREEN}${NC}"
echo -e "${GREEN}   Next steps:${NC}"
echo -e "${GREEN}   1. Stop Anvil stack:  docker compose -f docker/docker-compose.yml down -v${NC}"
echo -e "${GREEN}   2. Start Reth:        ./docker/reth/restart-reth.sh${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
