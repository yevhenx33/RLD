#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# setup_simulation.sh — Reth Simulation User Setup
# ═══════════════════════════════════════════════════════════════
# Single, self-contained script that provisions 3 simulation
# users on Reth:
#
#   LP     — $100M waUSDC, broker, wRLP minted, V4 LP position
#   MM     — $10M waUSDC, broker, wRLP minted
#   CHAOS  — $10M waUSDC, broker, wRLP minted
#
# Funding: Whale (Anvil #9, ~$900M USDC in genesis) sends USDC
# to SimFunder which atomically converts USDC → waUSDC.
#
# Every step is verified with Python assertions.
#
# Prerequisites:
#   - Reth running with protocol genesis
#   - deployment.json populated
#   - .env with DEPLOYER_KEY, USER_A_KEY, MM_KEY, CHAOS_KEY
#
# Usage:
#   ./docker/reth/setup_simulation.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Paths & Config ──────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$(dirname "$SCRIPT_DIR")"
RLD_ROOT="$(dirname "$DOCKER_DIR")"
ENV_FILE="$DOCKER_DIR/.env"
DEPLOY_JSON="$DOCKER_DIR/deployment.json"
RPC_URL="${RPC_URL:-http://localhost:8545}"
GAS_LIMIT=1000000

# ─── Colors ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; MAGENTA='\033[0;35m'
DIM='\033[2m'; NC='\033[0m'

header() { echo -e "\n${BLUE}═══════════════════════════════════════════════════${NC}"; echo -e "${BLUE}  $1${NC}"; echo -e "${BLUE}═══════════════════════════════════════════════════${NC}"; }
step()   { echo -e "\n${YELLOW}[$1] $2${NC}"; }
ok()     { echo -e "${GREEN}  ✓ $1${NC}"; }
fail()   { echo -e "${RED}  ✗ $1${NC}"; exit 1; }
info()   { echo -e "${CYAN}  ℹ $1${NC}"; }

# ─── Load environment ────────────────────────────────────────
source <(grep -E '^(DEPLOYER_KEY|USER_A_KEY|MM_KEY|CHAOS_KEY)=' "$ENV_FILE" | sed 's/^/export /')

# Whale = Anvil account #9 (pre-funded with ~$900M USDC in genesis)
WHALE_KEY="0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"

# ─── Derive addresses ────────────────────────────────────────
LP_KEY="${USER_A_KEY}"
LP_ADDR=$(cast wallet address --private-key "$LP_KEY")
MM_ADDR=$(cast wallet address --private-key "$MM_KEY")
CHAOS_ADDR=$(cast wallet address --private-key "$CHAOS_KEY")
WHALE_ADDR=$(cast wallet address --private-key "$WHALE_KEY")
DEPLOYER_ADDR=$(cast wallet address --private-key "$DEPLOYER_KEY")

# ─── Load contract addresses ─────────────────────────────────
WAUSDC=$(jq -r '.wausdc' "$DEPLOY_JSON")
POSITION_TOKEN=$(jq -r '.position_token' "$DEPLOY_JSON")
BROKER_FACTORY=$(jq -r '.broker_factory' "$DEPLOY_JSON")
MOCK_ORACLE=$(jq -r '.mock_oracle' "$DEPLOY_JSON")
SWAP_ROUTER=$(jq -r '.swap_router' "$DEPLOY_JSON")
TWAMM_HOOK=$(jq -r '.twamm_hook' "$DEPLOY_JSON")
MARKET_ID=$(jq -r '.market_id' "$DEPLOY_JSON")
V4_POS_MGR=$(jq -r '.v4_position_manager' "$DEPLOY_JSON")
POOL_MANAGER="0x000000000004444c5dc75cB358380D2e3dE08A90"
PERMIT2="0x000000000022D473030F116dDEE9F6B43aC78BA3"
USDC="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC="0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# SimFunder from deployment snapshot
SIM_FUNDER=$(jq -r '.sim_funder // empty' "$DEPLOY_JSON")
[ -z "$SIM_FUNDER" ] && SIM_FUNDER=$(jq -r '.sim_funder // empty' "$SCRIPT_DIR/deployment-snapshot.json" 2>/dev/null)

# Funding amounts (USD, 6 decimals)
LP_FUND_USD=100000000     # $100M
MM_FUND_USD=10000000      # $10M
CHAOS_FUND_USD=10000000   # $10M
TOTAL_FUND_USD=$((LP_FUND_USD + MM_FUND_USD + CHAOS_FUND_USD))

LP_FUND_WEI=$((LP_FUND_USD * 1000000))
MM_FUND_WEI=$((MM_FUND_USD * 1000000))
CHAOS_FUND_WEI=$((CHAOS_FUND_USD * 1000000))
TOTAL_FUND_WEI=$((TOTAL_FUND_USD * 1000000))

# ─── Python verify helper ────────────────────────────────────
# Creates a reusable Python module for on-chain assertions
cat > /tmp/reth_verify.py << 'PYEOF'
"""Reth on-chain verification helpers."""
import subprocess, os, sys, json

RPC = os.environ.get("RPC_URL", "http://localhost:8545")

def cast_call(contract, sig, *args):
    cmd = ["cast", "call", contract, sig] + list(args) + ["--rpc-url", RPC]
    out = subprocess.check_output(cmd, stderr=subprocess.PIPE).decode().strip()
    return out

def cast_code(addr):
    cmd = ["cast", "code", addr, "--rpc-url", RPC]
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode().strip()

def balance_of(token, addr):
    raw = cast_call(token, "balanceOf(address)(uint256)", addr)
    return int(raw.split()[0])

def assert_balance_gte(token, addr, min_wei, label):
    bal = balance_of(token, addr)
    usd = bal / 1e6
    if bal < min_wei:
        print(f"  ✗ {label}: {bal:,} (${usd:,.0f}) — EXPECTED >= {min_wei:,}", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ {label}: {bal:,} (${usd:,.0f})")
    return bal

def assert_balance_gt_zero(token, addr, label):
    return assert_balance_gte(token, addr, 1, label)

def assert_has_code(addr, label):
    code = cast_code(addr)
    if not code or code == "0x" or len(code) <= 4:
        print(f"  ✗ {label}: no code at {addr}", file=sys.stderr)
        sys.exit(1)
    print(f"  ✓ {label}: {len(code)//2} bytes at {addr}")

def assert_receipt_ok(output, label):
    """Check cast send --json output for success."""
    try:
        data = json.loads(output)
        status = data.get("status", "0x1")
        if status in ("0x0", "0"):
            print(f"  ✗ {label}: tx reverted (status={status})", file=sys.stderr)
            sys.exit(1)
        tx = data.get("transactionHash", "?")
        gas = int(data.get("gasUsed", "0x0"), 16)
        print(f"  ✓ {label}: tx={tx[:18]}... gas={gas:,}")
        return data
    except json.JSONDecodeError:
        print(f"  ✗ {label}: invalid tx output: {output[:200]}", file=sys.stderr)
        sys.exit(1)

def parse_broker_address(tx_output):
    """Extract broker address from BrokerCreated event in tx receipt."""
    data = json.loads(tx_output)
    event_sig = "0xc418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71"
    for log in data.get("logs", []):
        if log.get("topics", [None])[0] == event_sig:
            raw = log.get("data", "")
            if raw and raw.startswith("0x") and len(raw) >= 66:
                return "0x" + raw[26:66]
    return None

def final_report(users):
    """Print final balance report for all users."""
    wausdc = os.environ["WAUSDC"]
    pos_token = os.environ["POSITION_TOKEN"]
    print()
    print("  ╔═══════════════════════════════════════════════════════╗")
    print("  ║           FINAL USER STATE                           ║")
    print("  ╠═══════════════════════════════════════════════════════╣")
    for name, addr, broker in users:
        w = balance_of(wausdc, addr)
        p = balance_of(pos_token, addr)
        bw = balance_of(wausdc, broker) if broker else 0
        print(f"  ║  {name:8s}  waUSDC=${w/1e6:>12,.0f}  wRLP=${p/1e6:>10,.0f}  ║")
        if broker:
            print(f"  ║  {' ':8s}  broker_waUSDC=${bw/1e6:>8,.0f}  @{broker[:10]}... ║")
    print("  ╚═══════════════════════════════════════════════════════╝")
PYEOF
ok "Python verify module ready"

# ─── safe_send: cast send with receipt check ──────────────────
# Captures JSON output and checks status.
# Callers must pass their own --gas-limit if needed.
safe_send() {
    local LABEL=$1; shift
    local OUTPUT EXIT_CODE
    # The `|| true` prevents `set -e` from killing the script on non-zero exit
    OUTPUT=$(timeout 60s cast send --json --legacy "$@" 2>&1) || true
    EXIT_CODE=${PIPESTATUS[0]:-$?}
    # Check if output looks like valid JSON (success case)
    if [ -z "$OUTPUT" ] || ! echo "$OUTPUT" | grep -q '"status"'; then
        echo -e "  DEBUG: cast send returned no JSON receipt" >&2
        echo "$OUTPUT" | tail -5 >&2
        fail "$LABEL — cast send failed (no receipt)"
    fi
    # Verify receipt with Python
    echo "$OUTPUT" | RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c "
import sys, json
from reth_verify import assert_receipt_ok
output = sys.stdin.read().strip()
assert_receipt_ok(output, '$LABEL')
" || fail "$LABEL — receipt check failed"
    # Export for callers that need the raw output
    LAST_TX_OUTPUT="$OUTPUT"
}

# ═════════════════════════════════════════════════════════════
# STEP 0: PREFLIGHT
# ═════════════════════════════════════════════════════════════
header "STEP 0: PREFLIGHT CHECKS"

step "0.1" "Verifying Reth connectivity..."
BLOCK=$(cast block-number --rpc-url "$RPC_URL" 2>/dev/null) || fail "Reth not reachable at $RPC_URL"
ok "Reth at block $BLOCK"

step "0.2" "Verifying protocol contracts..."
RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
import os; os.environ["RPC_URL"] = os.environ["RPC_URL"]
from reth_verify import assert_has_code
contracts = {
    "waUSDC":          "'"$WAUSDC"'",
    "PositionToken":   "'"$POSITION_TOKEN"'",
    "BrokerFactory":   "'"$BROKER_FACTORY"'",
    "MockOracle":      "'"$MOCK_ORACLE"'",
    "SwapRouter":      "'"$SWAP_ROUTER"'",
    "TWAMMHook":       "'"$TWAMM_HOOK"'",
    "PoolManager":     "'"$POOL_MANAGER"'",
    "USDC":            "'"$USDC"'",
    "AavePool":        "'"$AAVE_POOL"'",
}
for name, addr in contracts.items():
    assert_has_code(addr, name)
' || fail "Contract verification"

step "0.3" "Checking user addresses..."
info "LP:    $LP_ADDR"
info "MM:    $MM_ADDR"
info "CHAOS: $CHAOS_ADDR"
info "WHALE: $WHALE_ADDR"

step "0.4" "Verifying whale USDC balance..."
RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
whale  = "'"$WHALE_ADDR"'"
usdc   = "'"$USDC"'"
needed = '"$TOTAL_FUND_WEI"'
assert_balance_gte(usdc, whale, needed, f"Whale USDC (need ${needed/1e6:,.0f})")
' || fail "Whale has insufficient USDC"

# ═════════════════════════════════════════════════════════════
# STEP 1: DEPLOY / VERIFY SIMFUNDER
# ═════════════════════════════════════════════════════════════
header "STEP 1: SIMFUNDER"

if [ -n "$SIM_FUNDER" ]; then
    step "1.1" "Checking existing SimFunder at $SIM_FUNDER..."
    CODE_LEN=$(cast code "$SIM_FUNDER" --rpc-url "$RPC_URL" 2>/dev/null | wc -c)
    if [ "$CODE_LEN" -gt 4 ]; then
        ok "SimFunder exists ($CODE_LEN bytes)"
    else
        SIM_FUNDER=""
    fi
fi

if [ -z "$SIM_FUNDER" ]; then
    step "1.2" "Deploying SimFunder..."
    cd "$RLD_ROOT/contracts"
    SIM_FUNDER=$(forge create src/periphery/SimFunder.sol:SimFunder \
        --private-key "$DEPLOYER_KEY" \
        --rpc-url "$RPC_URL" \
        --broadcast \
        --constructor-args "$USDC" "$AUSDC" "$WAUSDC" "$AAVE_POOL" \
        2>&1 | grep "Deployed to:" | awk '{print $3}')
    [ -z "$SIM_FUNDER" ] && fail "SimFunder deployment failed"
    ok "SimFunder deployed: $SIM_FUNDER"
fi

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_has_code
assert_has_code("'"$SIM_FUNDER"'", "SimFunder")
' || fail "SimFunder code check"

# ═════════════════════════════════════════════════════════════
# STEP 2: FUND ALL USERS VIA SIMFUNDER
# ═════════════════════════════════════════════════════════════
header "STEP 2: FUND USERS"

step "2.1" "Whale sends \$$((TOTAL_FUND_USD / 1000000))M USDC to SimFunder..."
safe_send "USDC→SimFunder" \
    "$USDC" "transfer(address,uint256)" "$SIM_FUNDER" "$TOTAL_FUND_WEI" \
    --private-key "$WHALE_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$USDC"'", "'"$SIM_FUNDER"'", '"$TOTAL_FUND_WEI"', "SimFunder USDC")
' || fail "SimFunder USDC balance"

step "2.2" "SimFunder.fund(LP, \$${LP_FUND_USD})..."
safe_send "fund(LP)" \
    "$SIM_FUNDER" "fund(address,uint256)" "$LP_ADDR" "$LP_FUND_WEI" \
    --private-key "$WHALE_KEY" --rpc-url "$RPC_URL" --gas-limit 3000000

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
# waUSDC uses shares, so balance may differ slightly from input
assert_balance_gte("'"$WAUSDC"'", "'"$LP_ADDR"'", int('"$LP_FUND_WEI"' * 0.95), "LP waUSDC")
' || fail "LP waUSDC balance"

step "2.3" "SimFunder.fund(MM, \$${MM_FUND_USD})..."
safe_send "fund(MM)" \
    "$SIM_FUNDER" "fund(address,uint256)" "$MM_ADDR" "$MM_FUND_WEI" \
    --private-key "$WHALE_KEY" --rpc-url "$RPC_URL" --gas-limit 3000000

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$WAUSDC"'", "'"$MM_ADDR"'", int('"$MM_FUND_WEI"' * 0.95), "MM waUSDC")
' || fail "MM waUSDC balance"

step "2.4" "SimFunder.fund(Chaos, \$${CHAOS_FUND_USD})..."
safe_send "fund(CHAOS)" \
    "$SIM_FUNDER" "fund(address,uint256)" "$CHAOS_ADDR" "$CHAOS_FUND_WEI" \
    --private-key "$WHALE_KEY" --rpc-url "$RPC_URL" --gas-limit 3000000

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$WAUSDC"'", "'"$CHAOS_ADDR"'", int('"$CHAOS_FUND_WEI"' * 0.95), "Chaos waUSDC")
' || fail "Chaos waUSDC balance"

ok "All users funded ✅"

# ═════════════════════════════════════════════════════════════
# STEP 3: LP SETUP (Broker + Deposit + Mint + V4 LP)
# ═════════════════════════════════════════════════════════════
header "STEP 3: LP SETUP"

# ── 3.1 Create broker ────────────────────────────────────────
step "3.1" "LP: Creating broker..."
SALT=$(cast keccak "lp-broker-$(date +%s)-$RANDOM")
LAST_TX_OUTPUT=""
safe_send "LP createBroker" \
    "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

LP_BROKER=$(echo "$LAST_TX_OUTPUT" | RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
import sys, json
from reth_verify import parse_broker_address, cast_code
data = sys.stdin.read().strip()
broker = parse_broker_address(data)
if not broker:
    print("  \u2717 Failed to parse broker address from tx logs", file=sys.stderr)
    sys.exit(1)
code = cast_code(broker)
if not code or code == "0x" or len(code) <= 4:
    print(f"  \u2717 No code at broker {broker}", file=sys.stderr)
    sys.exit(1)
print(f"  \u2713 LP Broker: {len(code)//2} bytes at {broker}", file=sys.stderr)
print(broker)
') || fail "LP broker creation"
ok "LP broker: $LP_BROKER"

# ── 3.2 Deposit all waUSDC to broker ─────────────────────────
step "3.2" "LP: Deposit all waUSDC to broker..."
LP_WAUSDC_BAL=$(cast call "$WAUSDC" "balanceOf(address)(uint256)" "$LP_ADDR" --rpc-url "$RPC_URL" | awk '{print $1}')
if [ "$LP_WAUSDC_BAL" = "0" ] || [ -z "$LP_WAUSDC_BAL" ]; then
    fail "LP waUSDC balance is 0 — Reth state is dirty from a prior run. Restart with: ./docker/reth/restart-reth.sh --fresh --with-users"
fi
info "LP waUSDC to deposit: $LP_WAUSDC_BAL"
safe_send "LP deposit" \
    "$WAUSDC" "transfer(address,uint256)" "$LP_BROKER" "$LP_WAUSDC_BAL" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte, balance_of
broker_bal = assert_balance_gte("'"$WAUSDC"'", "'"$LP_BROKER"'", 1, "LP Broker waUSDC")
wallet_bal = balance_of("'"$WAUSDC"'", "'"$LP_ADDR"'")
assert wallet_bal == 0, f"LP wallet should be empty, got {wallet_bal}"
print(f"  ✓ LP wallet waUSDC = 0 (all in broker)")
' || fail "LP deposit verification"

# ── 3.3 Prime oracle ─────────────────────────────────────────
step "3.3" "Priming oracle (advancing blocks)..."
for i in $(seq 1 15); do
    cast send --legacy "$DEPLOYER_ADDR" --value 0 \
        --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
done
sleep 5
ok "Oracle primed (15 blocks advanced)"

# ── 3.4 Mint wRLP ────────────────────────────────────────────
MINT_AMOUNT=$((5500000 * 1000000))  # $5.5M
step "3.4" "LP: Mint \$5.5M wRLP..."
safe_send "LP mint wRLP" \
    "$LP_BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$MINT_AMOUNT" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" --gas-limit 3000000

# ── 3.5 Withdraw $5M wRLP to wallet ──────────────────────────
WITHDRAW_WRLP=$((5000000 * 1000000))  # $5M
step "3.5" "LP: Withdraw \$5M wRLP to wallet..."
safe_send "LP withdraw wRLP" \
    "$LP_BROKER" "withdrawPositionToken(address,uint256)" "$LP_ADDR" "$WITHDRAW_WRLP" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$POSITION_TOKEN"'", "'"$LP_ADDR"'", '"$WITHDRAW_WRLP"' - 1000000, "LP wRLP in wallet")
' || fail "LP wRLP withdrawal"

# ── 3.6 Withdraw $5M waUSDC to wallet ────────────────────────
WITHDRAW_WAUSDC=$((5000000 * 1000000))  # $5M
step "3.6" "LP: Withdraw \$5M waUSDC to wallet..."
safe_send "LP withdraw waUSDC" \
    "$LP_BROKER" "withdrawCollateral(address,uint256)" "$LP_ADDR" "$WITHDRAW_WAUSDC" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$WAUSDC"'", "'"$LP_ADDR"'", '"$WITHDRAW_WAUSDC"' - 1000000, "LP waUSDC in wallet")
assert_balance_gte("'"$POSITION_TOKEN"'", "'"$LP_ADDR"'", '"$WITHDRAW_WRLP"' - 1000000, "LP wRLP in wallet")
print("  ✓ LP now holds both waUSDC and wRLP for LP provision")
' || fail "LP withdrawal verification"

# ── 3.7 Permit2 approvals ────────────────────────────────────
step "3.7" "LP: Setting Permit2 approvals for V4 LP..."
MAX_UINT256=$(python3 -c 'print(2**256-1)')
MAX_UINT160=$(python3 -c 'print(2**160-1)')
MAX_UINT48=$(python3 -c 'print(2**48-1)')

cast send --legacy "$WAUSDC" "approve(address,uint256)" "$PERMIT2" "$MAX_UINT256" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send --legacy "$POSITION_TOKEN" "approve(address,uint256)" "$PERMIT2" "$MAX_UINT256" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send --legacy "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$WAUSDC" "$V4_POS_MGR" "$MAX_UINT160" "$MAX_UINT48" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" > /dev/null
cast send --legacy "$PERMIT2" "approve(address,address,uint160,uint48)" \
    "$POSITION_TOKEN" "$V4_POS_MGR" "$MAX_UINT160" "$MAX_UINT48" \
    --private-key "$LP_KEY" --rpc-url "$RPC_URL" > /dev/null
ok "Permit2 approvals set"

# ── 3.8 Add V4 LP ────────────────────────────────────────────
LP_WEI=$((5000000 * 1000000))  # $5M each side
step "3.8" "LP: Adding V4 LP (\$5M / \$5M)..."
cd "$RLD_ROOT/contracts"
AUSDC_AMOUNT=$LP_WEI WRLP_AMOUNT=$LP_WEI PRIVATE_KEY=$LP_KEY \
    WAUSDC=$WAUSDC POSITION_TOKEN=$POSITION_TOKEN TWAMM_HOOK=$TWAMM_HOOK \
    TICK_SPACING=5 POOL_FEE=500 \
    forge script script/AddLiquidityWrapped.s.sol --tc AddLiquidityWrappedScript \
    --rpc-url "$RPC_URL" --broadcast --code-size-limit 99999 -v > /tmp/lp_output.log 2>&1 || true

# Verify LP was added by checking pool balances
RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import balance_of
import sys
pm = "'"$POOL_MANAGER"'"
wausdc = "'"$WAUSDC"'"
pos = "'"$POSITION_TOKEN"'"
t0 = balance_of(wausdc, pm)
t1 = balance_of(pos, pm)
# Pool should have tokens from LP
if t0 == 0 and t1 == 0:
    print("  ⚠ WARNING: Pool has no balances — LP may have failed", file=sys.stderr)
    print("  Check /tmp/lp_output.log for details")
    sys.exit(1)
print(f"  ✓ Pool waUSDC={t0:,} (${t0/1e6:,.0f}) wRLP={t1:,} (${t1/1e6:,.0f})")
' || fail "V4 LP verification"

ok "LP setup complete ✅"

# ═════════════════════════════════════════════════════════════
# STEP 4: MM SETUP (Broker + Deposit + Mint)
# ═════════════════════════════════════════════════════════════
header "STEP 4: MM SETUP"

# ── 4.1 Create broker ────────────────────────────────────────
step "4.1" "MM: Creating broker..."
SALT=$(cast keccak "mm-broker-$(date +%s)-$RANDOM")
safe_send "MM createBroker" \
    "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$MM_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

MM_BROKER=$(echo "$LAST_TX_OUTPUT" | RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
import sys
from reth_verify import parse_broker_address, cast_code
broker = parse_broker_address(sys.stdin.read().strip())
if not broker:
    print("  \u2717 Failed to parse MM broker address", file=sys.stderr)
    sys.exit(1)
code = cast_code(broker)
if not code or code == "0x" or len(code) <= 4:
    print(f"  \u2717 No code at broker {broker}", file=sys.stderr)
    sys.exit(1)
print(f"  \u2713 MM Broker: {len(code)//2} bytes at {broker}", file=sys.stderr)
print(broker)
') || fail "MM broker creation"
ok "MM broker: $MM_BROKER"

# ── 4.2 Deposit $6.5M waUSDC ─────────────────────────────────
MM_DEPOSIT=$((6500000 * 1000000))
step "4.2" "MM: Deposit \$6.5M waUSDC to broker..."
safe_send "MM deposit" \
    "$WAUSDC" "transfer(address,uint256)" "$MM_BROKER" "$MM_DEPOSIT" \
    --private-key "$MM_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$WAUSDC"'", "'"$MM_BROKER"'", '"$MM_DEPOSIT"' - 1000000, "MM Broker waUSDC")
' || fail "MM deposit verification"

# ── 4.3 Prime oracle + Mint $1M wRLP ─────────────────────────
step "4.3" "MM: Priming oracle..."
for i in $(seq 1 10); do
    cast send --legacy "$DEPLOYER_ADDR" --value 0 \
        --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
done
sleep 3

MM_MINT=$((1000000 * 1000000))
step "4.4" "MM: Mint \$1M wRLP..."
safe_send "MM mint wRLP" \
    "$MM_BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$MM_MINT" \
    --private-key "$MM_KEY" --rpc-url "$RPC_URL" --gas-limit 3000000

# ── 4.5 Withdraw wRLP to wallet ──────────────────────────────
step "4.5" "MM: Withdraw wRLP to wallet..."
safe_send "MM withdraw wRLP" \
    "$MM_BROKER" "withdrawPositionToken(address,uint256)" "$MM_ADDR" "$MM_MINT" \
    --private-key "$MM_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte, balance_of
wrlp = assert_balance_gte("'"$POSITION_TOKEN"'", "'"$MM_ADDR"'", '"$MM_MINT"' - 1000000, "MM wRLP")
wausdc = balance_of("'"$WAUSDC"'", "'"$MM_ADDR"'")
print(f"  ✓ MM wallet: waUSDC=${wausdc/1e6:,.0f}  wRLP=${wrlp/1e6:,.0f}")
' || fail "MM wRLP verification"

ok "MM setup complete ✅"

# ═════════════════════════════════════════════════════════════
# STEP 5: CHAOS SETUP (Broker + Deposit + Mint)
# ═════════════════════════════════════════════════════════════
header "STEP 5: CHAOS SETUP"

# ── 5.1 Create broker ────────────────────────────────────────
step "5.1" "Chaos: Creating broker..."
SALT=$(cast keccak "chaos-broker-$(date +%s)-$RANDOM")
safe_send "Chaos createBroker" \
    "$BROKER_FACTORY" "createBroker(bytes32)" "$SALT" \
    --private-key "$CHAOS_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

CHAOS_BROKER=$(echo "$LAST_TX_OUTPUT" | RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
import sys
from reth_verify import parse_broker_address, cast_code
broker = parse_broker_address(sys.stdin.read().strip())
if not broker:
    print("  \u2717 Failed to parse Chaos broker address", file=sys.stderr)
    sys.exit(1)
code = cast_code(broker)
if not code or code == "0x" or len(code) <= 4:
    print(f"  \u2717 No code at broker {broker}", file=sys.stderr)
    sys.exit(1)
print(f"  \u2713 Chaos Broker: {len(code)//2} bytes at {broker}", file=sys.stderr)
print(broker)
') || fail "Chaos broker creation"
ok "Chaos broker: $CHAOS_BROKER"

# ── 5.2 Deposit $5M waUSDC ───────────────────────────────────
CHAOS_DEPOSIT=$((5000000 * 1000000))
step "5.2" "Chaos: Deposit \$5M waUSDC to broker..."
safe_send "Chaos deposit" \
    "$WAUSDC" "transfer(address,uint256)" "$CHAOS_BROKER" "$CHAOS_DEPOSIT" \
    --private-key "$CHAOS_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte
assert_balance_gte("'"$WAUSDC"'", "'"$CHAOS_BROKER"'", '"$CHAOS_DEPOSIT"' - 1000000, "Chaos Broker waUSDC")
' || fail "Chaos deposit verification"

# ── 5.3 Prime oracle + Mint wRLP (price-adjusted) ────────────
step "5.3" "Chaos: Priming oracle..."
for i in $(seq 1 10); do
    cast send --legacy "$DEPLOYER_ADDR" --value 0 \
        --private-key "$DEPLOYER_KEY" --rpc-url "$RPC_URL" > /dev/null 2>&1 || true
done
sleep 3

# Calculate mint amount based on current oracle price
CHAOS_MINT_USD=1000000  # $1M target
CHAOS_MINT_WEI=$((CHAOS_MINT_USD * 1000000))
step "5.4" "Chaos: Mint ~\$${CHAOS_MINT_USD} wRLP..."
safe_send "Chaos mint wRLP" \
    "$CHAOS_BROKER" "modifyPosition(bytes32,int256,int256)" \
    "$MARKET_ID" 0 "$CHAOS_MINT_WEI" \
    --private-key "$CHAOS_KEY" --rpc-url "$RPC_URL" --gas-limit 3000000

# ── 5.5 Withdraw wRLP to wallet ──────────────────────────────
step "5.5" "Chaos: Withdraw wRLP to wallet..."
safe_send "Chaos withdraw wRLP" \
    "$CHAOS_BROKER" "withdrawPositionToken(address,uint256)" "$CHAOS_ADDR" "$CHAOS_MINT_WEI" \
    --private-key "$CHAOS_KEY" --rpc-url "$RPC_URL" --gas-limit $GAS_LIMIT

RPC_URL="$RPC_URL" PYTHONPATH=/tmp python3 -c '
from reth_verify import assert_balance_gte, balance_of
wrlp = assert_balance_gte("'"$POSITION_TOKEN"'", "'"$CHAOS_ADDR"'", '"$CHAOS_MINT_WEI"' - 1000000, "Chaos wRLP")
wausdc = balance_of("'"$WAUSDC"'", "'"$CHAOS_ADDR"'")
print(f"  ✓ Chaos wallet: waUSDC=${wausdc/1e6:,.0f}  wRLP=${wrlp/1e6:,.0f}")
' || fail "Chaos wRLP verification"

ok "Chaos setup complete ✅"

# ═════════════════════════════════════════════════════════════
# STEP 6: FINAL REPORT
# ═════════════════════════════════════════════════════════════
header "FINAL REPORT"

RPC_URL="$RPC_URL" WAUSDC="$WAUSDC" POSITION_TOKEN="$POSITION_TOKEN" \
PYTHONPATH=/tmp python3 -c '
from reth_verify import final_report
users = [
    ("LP",    "'"$LP_ADDR"'",    "'"$LP_BROKER"'"),
    ("MM",    "'"$MM_ADDR"'",    "'"$MM_BROKER"'"),
    ("CHAOS", "'"$CHAOS_ADDR"'", "'"$CHAOS_BROKER"'"),
]
final_report(users)
'

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Simulation users ready!${NC}"
echo -e "${GREEN}     LP broker:    $LP_BROKER${NC}"
echo -e "${GREEN}     MM broker:    $MM_BROKER${NC}"
echo -e "${GREEN}     Chaos broker: $CHAOS_BROKER${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════${NC}"
