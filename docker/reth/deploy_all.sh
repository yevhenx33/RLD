#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# deploy_all.sh (Reth version) — Thin Orchestrator
# ═══════════════════════════════════════════════════════════════
# Identical to docker/deployer/deploy_all.sh except:
#   - Uses Reth-compatible phase 02, 05 (from docker/reth/deployer/)
#   - Uses Reth-compatible lib_setup.sh
#   - No evm_setAutomine (Reth auto-mines in dev mode)
#   - Phases 01, 03, 04 are reused from original deployer
# ═══════════════════════════════════════════════════════════════

set -e
export FOUNDRY_DISABLE_NIGHTLY_WARNING=1

# ─── Colors & logging ─────────────────────────────────────────
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

for VAR in DEPLOYER_KEY; do
    if [ -z "${!VAR}" ]; then
        log_err "$VAR not set"
    fi
done

# ─── Mainnet constants ────────────────────────────────────────
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC_WHALE="0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"

# ─── Official Uniswap V4 mainnet addresses ────────────────────
POOL_MANAGER="0x000000000004444c5dc75cB358380D2e3dE08A90"
V4_POSITION_MANAGER="0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
V4_QUOTER="0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203"
V4_POSITION_DESCRIPTOR="0xd1428ba554f4c8450b763a0b2040a4935c63f06c"
V4_STATE_VIEW="0x7ffe42c4a5deea5b0fec41c94c136cf115597227"
UNIVERSAL_ROUTER="0x66a9893cc07d91d95644aedd05d03f95e1dba8af"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"

# ─── Basis Trade addresses ─────────────────────────────────────
SUSDE="0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"
USDE="0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"
PYUSD="0x6c3ea9036406852006290770BEdFcAbA0e23A0e8"
CURVE_USDE_USDC_POOL="0x02950460E2b9529D0E00284A5fA2d7bDF3fA4d72"
CURVE_PYUSD_USDC_POOL="0x383E6b4437b59fff47B619CBA855CA29342A8559"

# ─── Morpho Blue ───────────────────────────────────────────────
MORPHO="0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
MORPHO_ORACLE="0xE6212D05cB5aF3C821Fef1C1A233a678724F9E7E"
MORPHO_IRM="0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC"
MORPHO_LLTV="915000000000000000"

# ─── Wait for Reth ────────────────────────────────────────────
log_phase "0" "WAITING FOR RETH"
for i in $(seq 1 60); do
    if cast block-number --rpc-url "$RPC_URL" > /dev/null 2>&1; then
        BLOCK=$(cast block-number --rpc-url "$RPC_URL")
        log_ok "Reth reachable at block $BLOCK"
        break
    fi
    echo "  Waiting for $RPC_URL... ($i/60)"
    sleep 2
done
cast block-number --rpc-url "$RPC_URL" > /dev/null 2>&1 || log_err "Reth not reachable at $RPC_URL"

# Reth dev mode auto-mines — no evm_setAutomine needed
log_ok "Reth dev mode: auto-mining active"

# ─── Source Reth-compatible lib_setup ─────────────────────────
RETH_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$RETH_DIR/deployer/lib_setup.sh"

# ─── Source & run phases ──────────────────────────────────────
# Use original phases 01, 03, 04 (no Anvil-specific RPCs)
# Use Reth-compatible phases 02, 05
ORIG_PHASE_DIR="$(dirname "$RETH_DIR")/deployer/phases"
RETH_PHASE_DIR="$RETH_DIR/deployer/phases"

source "$ORIG_PHASE_DIR/01_protocol.sh"
source "$RETH_PHASE_DIR/02_market.sh"       # Reth version
source "$ORIG_PHASE_DIR/03_periphery.sh"
source "$ORIG_PHASE_DIR/04_finalize.sh"
source "$RETH_PHASE_DIR/05_users.sh"        # Reth version

echo ""
echo -e "${MAGENTA}╔═══════════════════════════════════════════════════╗${NC}"
echo -e "${MAGENTA}║     DEPLOYMENT COMPLETE (Reth mode)               ║${NC}"
echo -e "${MAGENTA}╚═══════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Daemons poll indexer for config and start automatically."
echo ""
