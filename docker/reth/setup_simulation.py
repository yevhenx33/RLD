#!/usr/bin/env python3
"""
setup_simulation.py — Reth Simulation User Setup
═══════════════════════════════════════════════════
Single Python script that provisions 3 simulation users on Reth:

  LP     — $100M waUSDC, broker, wRLP minted, V4 LP position
  MM     — $10M waUSDC, broker, wRLP minted
  CHAOS  — $10M waUSDC, broker, wRLP minted

Funding: Whale (Anvil #9, ~$10B USDC in genesis) sends USDC
to SimFunder which atomically converts USDC → waUSDC.

Uses web3.py for transactions, subprocess+cast for reads.
Every step is verified with Python assertions.

Usage:
  python3 docker/reth/setup_simulation.py
  # or via restart-reth.sh --with-users
"""

import os, sys, json, time, subprocess, secrets, math, argparse
from pathlib import Path
from web3 import Web3
from eth_account import Account
from eth_abi import encode as abi_encode

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent
RLD_ROOT = DOCKER_DIR.parent
ENV_FILE = DOCKER_DIR / ".env"
DEPLOY_JSON = DOCKER_DIR / "deployment.json"
SNAPSHOT_JSON = SCRIPT_DIR / "deployment-snapshot.json"
RPC_URL = os.environ.get("RPC_URL", "http://localhost:8545")

# Well-known mainnet addresses (baked into genesis)
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
POOL_FEE = 500
TICK_SPACING = 5
MIN_TICK = -887272
MAX_TICK = 887272

# Whale = Anvil account #9 (pre-funded with ~$10B USDC in genesis)
WHALE_KEY = "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"

# Funding amounts (raw USDC, 6 decimals)
LP_FUND =    100_000_000 * 10**6   # $100M
# Main-branch MM profile was: fund=$10M, deposit=$6.5M.
# Scale that profile to a $100M initial broker deposit.
MM_INITIAL_DEPOSIT = 100_000_000 * 10**6
MM_FUND = ((10_000_000 * MM_INITIAL_DEPOSIT) // 6_500_000)   # ~$153.846M total MM funding
CHAOS_FUND =  50_000_000 * 10**6   # $50M
TOTAL_FUND = LP_FUND + MM_FUND + CHAOS_FUND
FAUCET_RESERVE = 1_000_000_000 * 10**6  # $1B for faucet SimFunder
TX_DELAY_SECONDS = float(os.getenv("TX_DELAY_SECONDS", "1"))  # Reth pacing to avoid nonce races

# MM debt target restored to main-branch value.
MM_MINT = 1_000_000 * 10**6
CHAOS_MINT = 1_000_000 * 10**6

# BrokerCreated event topic
BROKER_CREATED_SIG = "c418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71"

# ═══════════════════════════════════════════════════════════════
# COLORS & LOGGING
# ═══════════════════════════════════════════════════════════════
RED = '\033[0;31m'; GREEN = '\033[0;32m'; YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'; BLUE = '\033[0;34m'; NC = '\033[0m'

def header(msg):
    print(f"\n{BLUE}{'═'*51}{NC}")
    print(f"{BLUE}  {msg}{NC}")
    print(f"{BLUE}{'═'*51}{NC}")

def step(num, msg):  print(f"\n{YELLOW}[{num}] {msg}{NC}")
def ok(msg):         print(f"{GREEN}  ✓ {msg}{NC}")
def info(msg):       print(f"{CYAN}  ℹ {msg}{NC}")

def fail(msg):
    print(f"{RED}  ✗ {msg}{NC}", file=sys.stderr)
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# CONFIG LOADING
# ═══════════════════════════════════════════════════════════════
def load_env() -> dict:
    """Load private keys from .env file."""
    keys = {}
    wanted = {'DEPLOYER_KEY', 'USER_A_KEY', 'MM_KEY', 'CHAOS_KEY'}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                if k in wanted:
                    keys[k] = v.strip().strip('"').strip("'")
    for k in wanted:
        if k not in keys:
            fail(f"Missing {k} in {ENV_FILE}")
    return keys

def load_deployment() -> dict:
    """Load contract addresses from deployment.json."""
    with open(DEPLOY_JSON) as f:
        return json.load(f)


def persist_sim_funder(sim_funder: str):
    """Persist sim_funder to both runtime and snapshot deployment files."""
    for path in [DEPLOY_JSON, SNAPSHOT_JSON]:
        if path.exists():
            data = json.loads(path.read_text())
            data["sim_funder"] = sim_funder
            path.write_text(json.dumps(data, indent=2))
    ok("sim_funder persisted to deployment files")

# ═══════════════════════════════════════════════════════════════
# CAST HELPERS (reads — no ABI needed)
# ═══════════════════════════════════════════════════════════════
def cast_call(contract: str, sig: str, *args) -> str:
    """Read-only contract call via cast."""
    cmd = ['cast', 'call', contract, sig] + [str(a) for a in args] + ['--rpc-url', RPC_URL]
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode().strip()

def cast_calldata(sig: str, *args) -> str:
    """Encode function calldata via cast."""
    cmd = ['cast', 'calldata', sig] + [str(a) for a in args]
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode().strip()

def cast_code(addr: str) -> str:
    cmd = ['cast', 'code', addr, '--rpc-url', RPC_URL]
    return subprocess.check_output(cmd, stderr=subprocess.PIPE).decode().strip()

def balance_of(token: str, addr: str) -> int:
    raw = cast_call(token, "balanceOf(address)(uint256)", addr)
    return int(raw.split()[0])

def has_code(addr: str) -> bool:
    code = cast_code(addr)
    return bool(code) and code != "0x" and len(code) > 4


def is_zero_address(addr: str | None) -> bool:
    if not addr:
        return True
    norm = addr.lower()
    return norm in ("0x", "0x0", ZERO_ADDRESS.lower())


def aligned_tick_floor(tick: int, spacing: int) -> int:
    return math.floor(tick / spacing) * spacing


def aligned_tick_ceil(tick: int, spacing: int) -> int:
    return math.ceil(tick / spacing) * spacing


def compute_lp_ticks(wausdc_is_token0: bool) -> tuple[int, int]:
    """
    Compute tick range for waUSDC/wRLP price band [2, 20], mapped into pool raw units.
    """
    if wausdc_is_token0:
        # raw pool price = token1/token0 = wRLP/waUSDC = 1 / (waUSDC per wRLP)
        raw_price_low = 1 / 20
        raw_price_high = 1 / 2
    else:
        # raw pool price = token1/token0 = waUSDC/wRLP
        raw_price_low = 2
        raw_price_high = 20

    lower_tick = aligned_tick_floor(int(math.floor(math.log(raw_price_low) / math.log(1.0001))), TICK_SPACING)
    upper_tick = aligned_tick_ceil(int(math.ceil(math.log(raw_price_high) / math.log(1.0001))), TICK_SPACING)

    min_tick_aligned = aligned_tick_ceil(MIN_TICK, TICK_SPACING)
    max_tick_aligned = aligned_tick_floor(MAX_TICK, TICK_SPACING)
    lower_tick = max(lower_tick, min_tick_aligned)
    upper_tick = min(upper_tick, max_tick_aligned)

    if lower_tick >= upper_tick:
        fail(f"Invalid LP tick range computed: [{lower_tick}, {upper_tick}]")

    return lower_tick, upper_tick

# ═══════════════════════════════════════════════════════════════
# WEB3 TRANSACTION HELPERS (writes)
# ═══════════════════════════════════════════════════════════════
def send_tx(w3: Web3, to: str, calldata: str, private_key: str,
            label: str, gas: int = 1_000_000, value: int = 0):
    """
    Send a transaction via web3.py. Returns receipt.
    Uses legacy (type-0) transactions for Reth dev compatibility.
    """
    account = Account.from_key(private_key)
    # Use 'pending' to include in-flight txs and avoid nonce collisions
    nonce = w3.eth.get_transaction_count(account.address, 'pending')

    # Aggressive gas pricing: 10x base fee, minimum 10 gwei.
    # Prevents "replacement transaction underpriced" when bots are active.
    base_price = w3.eth.gas_price or 1_000_000_000
    aggressive_price = max(base_price * 10, 10_000_000_000)  # 10x or 10 gwei

    tx = {
        'to': Web3.to_checksum_address(to),
        'data': calldata,
        'gas': gas,
        'gasPrice': aggressive_price,
        'nonce': nonce,
        'value': value,
        'chainId': w3.eth.chain_id,
    }

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)

    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    except Exception as e:
        fail(f"{label}: timeout waiting for receipt — {e}")

    if receipt.status != 1:
        fail(f"{label}: tx REVERTED (tx={tx_hash.hex()[:18]}... gas={receipt.gasUsed:,})")

    ok(f"{label}: tx={tx_hash.hex()[:18]}... gas={receipt.gasUsed:,}")
    if TX_DELAY_SECONDS > 0:
        # Small pacing gap helps avoid nonce contention on very fast local Reth.
        time.sleep(TX_DELAY_SECONDS)
    return receipt

# ═══════════════════════════════════════════════════════════════
# VERIFICATION HELPERS
# ═══════════════════════════════════════════════════════════════
def assert_balance_gte(token: str, addr: str, min_wei: int, label: str) -> int:
    bal = balance_of(token, addr)
    usd = bal / 1e6
    if bal < min_wei:
        fail(f"{label}: {bal:,} (${usd:,.0f}) — EXPECTED >= {min_wei:,}")
    ok(f"{label}: {bal:,} (${usd:,.0f})")
    return bal

def assert_has_code(addr: str, label: str):
    if not has_code(addr):
        fail(f"{label}: no code at {addr}")
    code_len = len(cast_code(addr)) // 2
    ok(f"{label}: {code_len} bytes at {addr}")

def parse_broker_address(receipt) -> str:
    """Extract broker address from BrokerCreated event in tx receipt."""
    for log in receipt.logs:
        topics = [t.hex() for t in log.topics]
        if topics and topics[0] == BROKER_CREATED_SIG:
            data_hex = log.data.hex()
            if len(data_hex) >= 64:
                return Web3.to_checksum_address("0x" + data_hex[24:64])
    return None

# ═══════════════════════════════════════════════════════════════
# STEP 0: PREFLIGHT
# ═══════════════════════════════════════════════════════════════
def preflight(w3, deploy, keys, required_total_fund):
    header("STEP 0: PREFLIGHT CHECKS")

    step("0.1", "Verifying Reth connectivity...")
    try:
        block = w3.eth.block_number
        ok(f"Reth at block {block}")
    except Exception as e:
        fail(f"Reth not reachable at {RPC_URL} — {e}")

    step("0.2", "Verifying protocol contracts...")
    required_contracts = {
        "waUSDC": deploy["wausdc"],
        "PositionToken": deploy["position_token"],
        "BrokerFactory": deploy["broker_factory"],
        "MockOracle": deploy["mock_oracle"],
        "PoolManager": POOL_MANAGER,
        "USDC": USDC,
        "AavePool": AAVE_POOL,
    }
    for name, addr in required_contracts.items():
        assert_has_code(addr, name)

    optional_contracts = {
        "SwapRouter": deploy.get("swap_router"),
        "TWAMMHook": deploy.get("twamm_hook"),
        "GhostRouter": deploy.get("ghost_router"),
        "TwapEngine": deploy.get("twap_engine"),
    }
    for name, addr in optional_contracts.items():
        if is_zero_address(addr):
            info(f"{name}: disabled/not deployed")
            continue
        assert_has_code(addr, name)

    # V4 periphery is only required for LP path, not for SimFunder/faucet flows.
    if deploy.get("v4_position_manager") and has_code(deploy["v4_position_manager"]):
        assert_has_code(deploy["v4_position_manager"], "V4PositionManager")
    else:
        info("V4PositionManager: disabled/not deployed")
    if has_code(PERMIT2):
        assert_has_code(PERMIT2, "Permit2")
    else:
        info("Permit2: disabled/not deployed")

    step("0.3", "Checking user addresses...")
    lp_addr = Account.from_key(keys["USER_A_KEY"]).address
    mm_addr = Account.from_key(keys["MM_KEY"]).address
    chaos_addr = Account.from_key(keys["CHAOS_KEY"]).address
    whale_addr = Account.from_key(WHALE_KEY).address
    info(f"LP:    {lp_addr}")
    info(f"MM:    {mm_addr}")
    info(f"CHAOS: {chaos_addr}")
    info(f"WHALE: {whale_addr}")

    step("0.4", "Verifying whale USDC balance...")
    assert_balance_gte(USDC, whale_addr, required_total_fund,
                       f"Whale USDC (need ${required_total_fund/1e6:,.0f})")

# ═══════════════════════════════════════════════════════════════
# STEP 1: SIMFUNDER
# ═══════════════════════════════════════════════════════════════
def setup_simfunder(w3, deploy, keys):
    header("STEP 1: SIMFUNDER")

    # Check existing SimFunder
    sim_funder = deploy.get("sim_funder", "")
    if not sim_funder and SNAPSHOT_JSON.exists():
        snap = json.loads(SNAPSHOT_JSON.read_text())
        sim_funder = snap.get("sim_funder", "")

    if sim_funder and has_code(sim_funder):
        step("1.1", f"SimFunder exists at {sim_funder}")
        assert_has_code(sim_funder, "SimFunder")
        return sim_funder

    # Deploy new SimFunder
    step("1.2", "Deploying SimFunder...")
    result = subprocess.run(
        ["forge", "create", "src/periphery/SimFunder.sol:SimFunder",
         "--private-key", keys["DEPLOYER_KEY"],
         "--rpc-url", RPC_URL,
         "--broadcast", "--legacy",
         "--constructor-args", USDC, AUSDC, deploy["wausdc"], AAVE_POOL],
        cwd=str(RLD_ROOT / "contracts"),
        capture_output=True, text=True
    )
    if result.returncode != 0:
        fail(f"SimFunder deployment failed:\n{result.stderr[-500:]}")

    # Parse deployed address
    for line in result.stdout.split('\n'):
        if 'Deployed to:' in line:
            sim_funder = line.split('Deployed to:')[1].strip()
            break
    if not sim_funder:
        fail("Could not parse SimFunder address from forge output")

    assert_has_code(sim_funder, "SimFunder")
    return sim_funder


def ensure_simfunder_reserve(
    w3,
    deploy,
    keys,
    min_reserve: int = FAUCET_RESERVE,
    prime_wausdc: bool = False,
):
    """
    Ensure SimFunder exists and has enough USDC reserve for faucet requests.
    """
    header("STEP 1: SIMFUNDER RESERVE")
    sim_funder = setup_simfunder(w3, deploy, keys)
    persist_sim_funder(sim_funder)

    usdc_reserve = balance_of(USDC, sim_funder)
    wausdc_reserve = balance_of(deploy["wausdc"], sim_funder)

    if usdc_reserve < min_reserve and wausdc_reserve < int(min_reserve * 0.90):
        top_up = min_reserve - usdc_reserve
        step("1.3", f"Top-up SimFunder USDC reserve (${top_up/1e6:,.0f})...")
        calldata = cast_calldata("transfer(address,uint256)", sim_funder, top_up)
        send_tx(w3, USDC, calldata, WHALE_KEY, "USDC→SimFunder reserve top-up")
        usdc_reserve = balance_of(USDC, sim_funder)
    else:
        info(
            f"SimFunder already funded (USDC=${usdc_reserve/1e6:,.0f}, "
            f"waUSDC=${wausdc_reserve/1e6:,.0f})"
        )

    if prime_wausdc and wausdc_reserve < int(min_reserve * 0.90):
        # Prime reserve into waUSDC on an Anvil fork where Aave path is live.
        assert_has_code(AAVE_POOL, "AavePool")
        step("1.4", f"Priming SimFunder waUSDC reserve (${min_reserve/1e6:,.0f})...")
        calldata = cast_calldata("primeReserve(uint256)", min_reserve)
        try:
            send_tx(w3, sim_funder, calldata, keys["DEPLOYER_KEY"], "SimFunder.primeReserve", gas=3_000_000)
            wausdc_reserve = balance_of(deploy["wausdc"], sim_funder)
        except SystemExit:
            info("SimFunder prime via Aave path failed; continuing with USDC reserve fallback")
    elif prime_wausdc:
        info(f"SimFunder already has waUSDC reserve (${wausdc_reserve/1e6:,.0f}), skipping prime")

    if wausdc_reserve >= int(min_reserve * 0.90):
        assert_balance_gte(deploy["wausdc"], sim_funder, int(min_reserve * 0.90), "SimFunder waUSDC reserve")
    else:
        assert_balance_gte(USDC, sim_funder, min_reserve, "SimFunder USDC reserve")
    ok("SimFunder reserve ready ✅")
    return sim_funder

# ═══════════════════════════════════════════════════════════════
# STEP 2: FUND USERS
# ═══════════════════════════════════════════════════════════════
def fund_users(w3, deploy, sim_funder, keys, include_mm=True, include_chaos=True):
    header("STEP 2: FUND USERS")
    wausdc = deploy["wausdc"]

    users = [("LP", Account.from_key(keys["USER_A_KEY"]).address, LP_FUND)]
    if include_mm:
        users.append(("MM", Account.from_key(keys["MM_KEY"]).address, MM_FUND))
    if include_chaos:
        users.append(("CHAOS", Account.from_key(keys["CHAOS_KEY"]).address, CHAOS_FUND))

    total_target = sum(amount for _, _, amount in users)
    if total_target <= 0:
        fail("No users selected for funding")

    # Determine incremental top-ups so this script is rerunnable on dirty state.
    funding_plan = []
    total_missing = 0
    for name, addr, target in users:
        current = balance_of(wausdc, addr)
        missing = max(0, target - current)
        funding_plan.append((name, addr, target, current, missing))
        total_missing += missing

    # 2.1 Whale sends only required incremental USDC to SimFunder
    if total_missing > 0:
        step("2.1", f"Whale sends ${total_missing/1e6:,.0f} USDC to SimFunder...")
        calldata = cast_calldata("transfer(address,uint256)", sim_funder, total_missing)
        send_tx(w3, USDC, calldata, WHALE_KEY, "USDC→SimFunder")
        assert_balance_gte(USDC, sim_funder, total_missing, "SimFunder USDC")
    else:
        step("2.1", "Whale top-up skipped (all users already funded)")
        info("No incremental waUSDC funding required")

    # 2.2-2.4 Fund each user
    for i, (name, addr, target, current, missing) in enumerate(funding_plan, 2):
        if missing <= 0:
            step(f"2.{i}", f"SimFunder.fund({name}) skipped (already ${current/1e6:,.0f})")
            assert_balance_gte(wausdc, addr, int(target * 0.90), f"{name} waUSDC")
            continue

        step(f"2.{i}", f"SimFunder.fund({name}, ${missing/1e6:,.0f})...")
        calldata = cast_calldata("fund(address,uint256)", addr, missing)
        send_tx(w3, sim_funder, calldata, WHALE_KEY, f"fund({name})", gas=3_000_000)
        # waUSDC shares ≈ USDC (within ~5% due to exchange rate)
        assert_balance_gte(wausdc, addr, int(target * 0.90), f"{name} waUSDC")

    ok("All users funded ✅")

    # 2.5 Maintain SimFunder USDC reserve at target floor for faucet
    current_reserve = balance_of(USDC, sim_funder)
    reserve_top_up = max(0, FAUCET_RESERVE - current_reserve)
    if reserve_top_up > 0:
        step("2.5", f"Loading SimFunder with ${reserve_top_up/1e6:,.0f} for faucet...")
        calldata = cast_calldata("transfer(address,uint256)", sim_funder, reserve_top_up)
        send_tx(w3, USDC, calldata, WHALE_KEY, "USDC→SimFunder (faucet reserve)")
    else:
        step("2.5", "Faucet reserve top-up skipped (already funded)")
    assert_balance_gte(USDC, sim_funder, FAUCET_RESERVE, "SimFunder faucet reserve")
    ok("Faucet reserve loaded ✅")

# ═══════════════════════════════════════════════════════════════
# STEP 3: LP SETUP
# ═══════════════════════════════════════════════════════════════
def setup_lp(w3, deploy, keys):
    header("STEP 3: LP SETUP")
    lp_key = keys["USER_A_KEY"]
    lp_addr = Account.from_key(lp_key).address
    deployer_key = keys["DEPLOYER_KEY"]
    deployer_addr = Account.from_key(deployer_key).address
    wausdc = deploy["wausdc"]
    pos_token = deploy["position_token"]
    market_id = deploy["market_id"]
    broker_factory = deploy["broker_factory"]

    # 3.1 Create broker
    step("3.1", "LP: Creating broker...")
    salt = "0x" + secrets.token_hex(32)
    calldata = cast_calldata("createBroker(bytes32)", salt)
    receipt = send_tx(w3, broker_factory, calldata, lp_key, "LP createBroker")
    broker = parse_broker_address(receipt)
    if not broker:
        fail("Failed to parse LP broker address from tx logs")
    assert_has_code(broker, "LP Broker")

    # 3.2 Deposit all waUSDC to broker
    step("3.2", "LP: Deposit all waUSDC to broker...")
    lp_bal = balance_of(wausdc, lp_addr)
    if lp_bal == 0:
        fail("LP waUSDC balance is 0 — state may be dirty")
    info(f"LP waUSDC to deposit: {lp_bal:,} (${lp_bal/1e6:,.0f})")
    calldata = cast_calldata("transfer(address,uint256)", broker, lp_bal)
    send_tx(w3, wausdc, calldata, lp_key, "LP deposit")
    assert_balance_gte(wausdc, broker, lp_bal - 1_000_000, "LP Broker waUSDC")

    # 3.3 Prime oracle
    step("3.3", "Priming oracle (advancing blocks)...")
    for i in range(15):
        try:
            send_tx(w3, deployer_addr, "0x", deployer_key,
                    f"block {i+1}", gas=21_000, value=0)
        except SystemExit:
            pass  # some may fail, that's ok
    time.sleep(3)
    ok("Oracle primed (15 blocks advanced)")

    # 3.4 Mint $5.5M wRLP
    mint_amount = 5_500_000 * 10**6
    step("3.4", "LP: Mint $5.5M wRLP...")
    calldata = cast_calldata("modifyPosition(bytes32,int256,int256)",
                             market_id, 0, mint_amount)
    send_tx(w3, broker, calldata, lp_key, "LP mint wRLP", gas=3_000_000)

    # 3.5 Withdraw $5M wRLP to wallet
    withdraw_wrlp = 5_000_000 * 10**6
    step("3.5", "LP: Withdraw $5M wRLP to wallet...")
    calldata = cast_calldata("withdrawToken(address,address,uint256)",
                             pos_token, lp_addr, withdraw_wrlp)
    send_tx(w3, broker, calldata, lp_key, "LP withdraw wRLP")
    assert_balance_gte(pos_token, lp_addr, withdraw_wrlp - 10**6,
                       "LP wRLP in wallet")

    # 3.6 Withdraw $5M waUSDC to wallet
    withdraw_wausdc = 5_000_000 * 10**6
    step("3.6", "LP: Withdraw $5M waUSDC to wallet...")
    calldata = cast_calldata("withdrawToken(address,address,uint256)",
                             wausdc, lp_addr, withdraw_wausdc)
    send_tx(w3, broker, calldata, lp_key, "LP withdraw waUSDC")
    assert_balance_gte(wausdc, lp_addr, withdraw_wausdc - 10**6,
                       "LP waUSDC in wallet")

    # 3.7 Permit2 approvals + V4 LP mint (optional if periphery contracts unavailable)
    v4_pos_mgr = deploy.get("v4_position_manager")
    if not v4_pos_mgr or not has_code(v4_pos_mgr):
        info("V4PositionManager not deployed — skipping LP mint stage")
        ok("LP setup complete ✅ (broker/wallet funded; no V4 LP minted)")
        return broker
    if not has_code(PERMIT2):
        info("Permit2 not deployed — skipping LP mint stage")
        ok("LP setup complete ✅ (broker/wallet funded; no V4 LP minted)")
        return broker

    step("3.7", "LP: Setting Permit2 approvals for V4 LP...")
    MAX_U256 = 2**256 - 1
    MAX_U160 = 2**160 - 1
    MAX_U48 = 2**48 - 1

    for token_addr in [wausdc, pos_token]:
        calldata = cast_calldata("approve(address,uint256)", PERMIT2, MAX_U256)
        send_tx(w3, token_addr, calldata, lp_key, f"approve Permit2", gas=100_000)

    for token_addr in [wausdc, pos_token]:
        calldata = cast_calldata("approve(address,address,uint160,uint48)",
                                 token_addr, v4_pos_mgr, MAX_U160, MAX_U48)
        send_tx(w3, PERMIT2, calldata, lp_key, f"Permit2→PosMgr", gas=100_000)
    ok("Permit2 approvals set")

    # 3.8 Add V4 LP via PositionManager.modifyLiquidities (no forge script dependency)
    lp_wei = 5_000_000 * 10**6
    step("3.8", f"LP: Adding V4 LP (${lp_wei/1e6:,.0f} / ${lp_wei/1e6:,.0f})...")
    pool_hook = deploy.get("twamm_hook")
    if is_zero_address(pool_hook):
        pool_hook = ZERO_ADDRESS
    pool_hook = Web3.to_checksum_address(pool_hook)

    token0 = Web3.to_checksum_address(min(wausdc.lower(), pos_token.lower()))
    token1 = Web3.to_checksum_address(max(wausdc.lower(), pos_token.lower()))
    wausdc_is_token0 = token0.lower() == wausdc.lower()
    amount0_in = withdraw_wausdc if wausdc_is_token0 else withdraw_wrlp
    amount1_in = withdraw_wrlp if wausdc_is_token0 else withdraw_wausdc
    tick_lower, tick_upper = compute_lp_ticks(wausdc_is_token0)

    info(f"Pool key token0={token0} token1={token1} hook={pool_hook}")
    info(f"LP ticks: [{tick_lower}, {tick_upper}]")
    info(f"LP settle amounts: token0={amount0_in:,} token1={amount1_in:,}")

    # Record expected tokenId and balances before mint.
    next_token_id = int(cast_call(v4_pos_mgr, "nextTokenId()(uint256)").split()[0])
    pool_wausdc_before = balance_of(wausdc, POOL_MANAGER)
    pool_wrlp_before = balance_of(pos_token, POOL_MANAGER)

    actions = bytes([0x0B, 0x0B, 0x05, 0x11])  # SETTLE, SETTLE, MINT_FROM_DELTAS, TAKE_PAIR
    pool_key = (token0, token1, POOL_FEE, TICK_SPACING, pool_hook)
    params = [
        abi_encode(["address", "uint256", "bool"], [token0, amount0_in, True]),
        abi_encode(["address", "uint256", "bool"], [token1, amount1_in, True]),
        abi_encode(
            ["(address,address,uint24,int24,address)", "int24", "int24", "uint128", "uint128", "address", "bytes"],
            [pool_key, tick_lower, tick_upper, amount0_in, amount1_in, lp_addr, b""],
        ),
        abi_encode(["address", "address", "address"], [token0, token1, lp_addr]),
    ]
    unlock_data = abi_encode(["bytes", "bytes[]"], [actions, params])

    calldata = cast_calldata(
        "modifyLiquidities(bytes,uint256)",
        "0x" + unlock_data.hex(),
        2**256 - 1,
    )
    send_tx(w3, v4_pos_mgr, calldata, lp_key, "LP mint V4 position", gas=2_500_000)

    owner = cast_call(v4_pos_mgr, "ownerOf(uint256)(address)", next_token_id).split()[0]
    if owner.lower() != lp_addr.lower():
        fail(f"LP NFT owner mismatch for tokenId {next_token_id}: {owner} != {lp_addr}")
    ok(f"LP Position Created (tokenId={next_token_id})")

    pool_wausdc = balance_of(wausdc, POOL_MANAGER)
    pool_wrlp = balance_of(pos_token, POOL_MANAGER)
    if pool_wausdc <= pool_wausdc_before and pool_wrlp <= pool_wrlp_before:
        fail("Pool balances did not increase after LP mint")
    ok(f"Pool waUSDC=${pool_wausdc/1e6:,.0f} wRLP=${pool_wrlp/1e6:,.0f}")

    ok("LP setup complete ✅")
    return broker

# ═══════════════════════════════════════════════════════════════
# STEP 4: MM SETUP
# ═══════════════════════════════════════════════════════════════
def setup_mm(w3, deploy, keys):
    header("STEP 4: MM SETUP")
    mm_key = keys["MM_KEY"]
    mm_addr = Account.from_key(mm_key).address
    deployer_key = keys["DEPLOYER_KEY"]
    deployer_addr = Account.from_key(deployer_key).address
    wausdc = deploy["wausdc"]
    pos_token = deploy["position_token"]
    market_id = deploy["market_id"]

    # 4.1 Create broker
    step("4.1", "MM: Creating broker...")
    salt = "0x" + secrets.token_hex(32)
    calldata = cast_calldata("createBroker(bytes32)", salt)
    receipt = send_tx(w3, deploy["broker_factory"], calldata, mm_key, "MM createBroker")
    broker = parse_broker_address(receipt)
    if not broker:
        fail("Failed to parse MM broker address")
    assert_has_code(broker, "MM Broker")

    # 4.2 Deposit scaled MM collateral
    deposit = MM_INITIAL_DEPOSIT
    step("4.2", f"MM: Deposit ${deposit/1e6:,.0f} waUSDC to broker...")
    calldata = cast_calldata("transfer(address,uint256)", broker, deposit)
    send_tx(w3, wausdc, calldata, mm_key, "MM deposit")
    assert_balance_gte(wausdc, broker, deposit - 10**6, "MM Broker waUSDC")

    # 4.3 Prime oracle
    step("4.3", "MM: Priming oracle...")
    for i in range(10):
        try:
            send_tx(w3, deployer_addr, "0x", deployer_key,
                    f"block {i+1}", gas=21_000, value=0)
        except SystemExit:
            pass
    time.sleep(3)

    # 4.4 Mint wRLP (bounded for solvency under live index)
    mint = MM_MINT
    step("4.4", f"MM: Mint ${mint/1e6:,.0f} wRLP...")
    calldata = cast_calldata("modifyPosition(bytes32,int256,int256)",
                             market_id, 0, mint)
    send_tx(w3, broker, calldata, mm_key, "MM mint wRLP", gas=3_000_000)

    # 4.5 Withdraw wRLP to wallet
    step("4.5", "MM: Withdraw wRLP to wallet...")
    calldata = cast_calldata("withdrawToken(address,address,uint256)",
                             pos_token, mm_addr, mint)
    send_tx(w3, broker, calldata, mm_key, "MM withdraw wRLP")

    wrlp_bal = balance_of(pos_token, mm_addr)
    wausdc_bal = balance_of(wausdc, mm_addr)
    ok(f"MM wallet: waUSDC=${wausdc_bal/1e6:,.0f} wRLP=${wrlp_bal/1e6:,.0f}")

    ok("MM setup complete ✅")
    return broker

# ═══════════════════════════════════════════════════════════════
# STEP 5: CHAOS SETUP
# ═══════════════════════════════════════════════════════════════
def setup_chaos(w3, deploy, keys):
    header("STEP 5: CHAOS SETUP")
    chaos_key = keys["CHAOS_KEY"]
    chaos_addr = Account.from_key(chaos_key).address
    deployer_key = keys["DEPLOYER_KEY"]
    deployer_addr = Account.from_key(deployer_key).address
    wausdc = deploy["wausdc"]
    pos_token = deploy["position_token"]
    market_id = deploy["market_id"]

    # 5.1 Create broker
    step("5.1", "Chaos: Creating broker...")
    salt = "0x" + secrets.token_hex(32)
    calldata = cast_calldata("createBroker(bytes32)", salt)
    receipt = send_tx(w3, deploy["broker_factory"], calldata, chaos_key, "Chaos createBroker")
    broker = parse_broker_address(receipt)
    if not broker:
        fail("Failed to parse Chaos broker address")
    assert_has_code(broker, "Chaos Broker")

    # 5.2 Deposit full chaos allocation as collateral ($50M)
    deposit = CHAOS_FUND
    step("5.2", f"Chaos: Deposit ${deposit/1e6:,.0f} waUSDC to broker...")
    calldata = cast_calldata("transfer(address,uint256)", broker, deposit)
    send_tx(w3, wausdc, calldata, chaos_key, "Chaos deposit")
    assert_balance_gte(wausdc, broker, deposit - 10**6, "Chaos Broker waUSDC")

    # 5.3 Prime oracle
    step("5.3", "Chaos: Priming oracle...")
    for i in range(10):
        try:
            send_tx(w3, deployer_addr, "0x", deployer_key,
                    f"block {i+1}", gas=21_000, value=0)
        except SystemExit:
            pass
    time.sleep(3)

    # 5.4 Mint wRLP (bounded for solvency under live index)
    mint = CHAOS_MINT
    step("5.4", f"Chaos: Mint ~${mint/1e6:,.0f} wRLP...")
    calldata = cast_calldata("modifyPosition(bytes32,int256,int256)",
                             market_id, 0, mint)
    send_tx(w3, broker, calldata, chaos_key, "Chaos mint wRLP", gas=3_000_000)

    # 5.5 Withdraw wRLP to wallet
    step("5.5", "Chaos: Withdraw wRLP to wallet...")
    calldata = cast_calldata("withdrawToken(address,address,uint256)",
                             pos_token, chaos_addr, mint)
    send_tx(w3, broker, calldata, chaos_key, "Chaos withdraw wRLP")

    wrlp_bal = balance_of(pos_token, chaos_addr)
    wausdc_bal = balance_of(wausdc, chaos_addr)
    ok(f"Chaos wallet: waUSDC=${wausdc_bal/1e6:,.0f} wRLP=${wrlp_bal/1e6:,.0f}")

    ok("Chaos setup complete ✅")
    return broker

# ═══════════════════════════════════════════════════════════════
# STEP 6: FINAL REPORT
# ═══════════════════════════════════════════════════════════════
def final_report(deploy, keys, brokers):
    header("FINAL REPORT")
    wausdc = deploy["wausdc"]
    pos_token = deploy["position_token"]

    users = [
        ("LP",    Account.from_key(keys["USER_A_KEY"]).address, brokers.get("lp")),
        ("MM",    Account.from_key(keys["MM_KEY"]).address,     brokers.get("mm")),
        ("CHAOS", Account.from_key(keys["CHAOS_KEY"]).address,  brokers.get("chaos")),
    ]

    print()
    print("  ╔══════════════════════════════════════════════════════╗")
    print("  ║           FINAL USER STATE                          ║")
    print("  ╠══════════════════════════════════════════════════════╣")
    for name, addr, broker in users:
        w = balance_of(wausdc, addr)
        p = balance_of(pos_token, addr)
        bw = balance_of(wausdc, broker) if broker else 0
        print(f"  ║  {name:6s} waUSDC=${w/1e6:>12,.0f}  wRLP=${p/1e6:>10,.0f} ║")
        if broker:
            print(f"  ║  {'':6s} broker_waUSDC=${bw/1e6:>8,.0f}  @{broker[:10]}…  ║")
    print("  ╚══════════════════════════════════════════════════════╝")

    print()
    print(f"{GREEN}{'═'*51}{NC}")
    print(f"{GREEN}  ✅ Simulation users ready!{NC}")
    for name, _, broker in users:
        print(f"{GREEN}     {name:6s} broker: {broker}{NC}")
    print(f"{GREEN}{'═'*51}{NC}")

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Provision simulation users and optional LP/MM/CHAOS setup on Reth.")
    parser.add_argument(
        "--lp-only",
        action="store_true",
        help="Run only LP setup path (fund LP user and create LP broker/position).",
    )
    parser.add_argument(
        "--sim-funder-only",
        action="store_true",
        help="Only ensure SimFunder is deployed and funded for faucet usage.",
    )
    parser.add_argument(
        "--prime-wausdc-reserve",
        action="store_true",
        help="Prime SimFunder by minting waUSDC reserve through fund(self, amount).",
    )
    args = parser.parse_args()

    if args.lp_only and args.sim_funder_only:
        fail("Use only one of --lp-only or --sim-funder-only")

    include_mm = not args.lp_only
    include_chaos = not args.lp_only
    if args.sim_funder_only:
        required_total_fund = FAUCET_RESERVE
    else:
        required_total_fund = LP_FUND + (MM_FUND if include_mm else 0) + (CHAOS_FUND if include_chaos else 0)

    keys = load_env()
    deploy = load_deployment()
    w3 = Web3(Web3.HTTPProvider(RPC_URL))

    if not w3.is_connected():
        fail(f"Cannot connect to Reth at {RPC_URL}")

    preflight(w3, deploy, keys, required_total_fund)

    if args.sim_funder_only:
        ensure_simfunder_reserve(
            w3,
            deploy,
            keys,
            FAUCET_RESERVE,
            prime_wausdc=args.prime_wausdc_reserve,
        )
        sys.exit(0)

    sim_funder = setup_simfunder(w3, deploy, keys)
    persist_sim_funder(sim_funder)

    fund_users(w3, deploy, sim_funder, keys, include_mm=include_mm, include_chaos=include_chaos)
    lp_broker = setup_lp(w3, deploy, keys)
    mm_broker = setup_mm(w3, deploy, keys) if include_mm else None
    chaos_broker = setup_chaos(w3, deploy, keys) if include_chaos else None

    final_report(deploy, keys, {
        "lp": lp_broker,
        "mm": mm_broker,
        "chaos": chaos_broker,
    })
