#!/usr/bin/env python3
"""
restart_reth.py — Full Reth Simulation Orchestrator
════════════════════════════════════════════════════
Single command that:
  1. Tears down everything
  2. Starts Anvil fork → deploys protocol → dumps state
  3. Converts to Reth genesis
  4. Starts Reth
  5. Launches indexer, seeds DB, runs user setup, starts bots

Usage:
  python3 docker/reth/restart_reth.py                    # Full run
  python3 docker/reth/restart_reth.py --fresh --with-users
  python3 docker/reth/restart_reth.py --skip-genesis     # Reuse genesis
  python3 docker/reth/restart_reth.py --no-build         # Skip rebuilds
"""

import argparse
import gzip
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# ═══════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════
SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent
RLD_ROOT = DOCKER_DIR.parent

COMPOSE_ANVIL = DOCKER_DIR / "docker-compose.yml"
COMPOSE_RETH  = SCRIPT_DIR / "docker-compose.reth.yml"
COMPOSE_RATES = DOCKER_DIR / "docker-compose.rates.yml"
ENV_FILE      = DOCKER_DIR / ".env"
DEPLOY_JSON   = DOCKER_DIR / "deployment.json"
GENESIS_FILE  = SCRIPT_DIR / "genesis.json"
DEPLOY_SNAPSHOT = SCRIPT_DIR / "deployment-snapshot.json"

RETH_PORT    = int(os.environ.get("RETH_PORT", 8545))
RETH_RPC     = f"http://localhost:{RETH_PORT}"
RETH_DATADIR = os.environ.get("RETH_DATADIR", str(Path.home() / ".local/share/reth-dev"))
RETH_LOG     = "/tmp/reth.log"

ANVIL_PORT = 8545
ANVIL_RPC  = f"http://localhost:{ANVIL_PORT}"
ANVIL_LOG  = "/tmp/anvil.log"

POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"

# Contracts to patch from mainnet during genesis conversion
PATCH_CONTRACTS = [
    "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203",
    "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    "0xd1428ba554f4c8450b763a0b2040a4935c63f06c",
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af",
    "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3",
    "0x6c3ea9036406852006290770BEdFcAbA0e23A0e8",
    "0x02950460E2b9529D0E00284A5fA2d7bDF3fA4d72",
    "0x383E6b4437b59fff47B619CBA855CA29342A8559",
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",
    "0xE6212D05cB5aF3C821Fef1C1A233a678724F9E7E",
    "0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC",
    "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341",
    "0x64b761D848206f447Fe2dd461b0c635Ec39EbB27",
]

# Contracts to warm up in Anvil cache before state dump
WARMUP_ADDRS = [
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "0x43506849D7C04F9138D1A2050bbF3A0c054402dd",
    "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e",
    "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497",
    "0x000000000004444c5dc75cB358380D2e3dE08A90",
    "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e",
    "0x000000000022D473030F116dDEE9F6B43aC78BA3",
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",
]

# ═══════════════════════════════════════════════════════════════
# COLORS & LOGGING
# ═══════════════════════════════════════════════════════════════
RED = '\033[0;31m'; GREEN = '\033[0;32m'; YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'; BLUE = '\033[0;34m'; MAGENTA = '\033[0;35m'
DIM = '\033[2m'; NC = '\033[0m'

def header(msg):   print(f"\n{BLUE}═══ {msg} ═══{NC}\n")
def step(n, msg):  print(f"{YELLOW}[{n}] {msg}{NC}")
def ok(msg):       print(f"{GREEN}  ✓ {msg}{NC}")
def info(msg):     print(f"{CYAN}  ℹ {msg}{NC}")
def warn(msg):     print(f"{YELLOW}  ⚠ {msg}{NC}")
def dim(msg):      print(f"{DIM}    {msg}{NC}")

def fail(msg):
    print(f"{RED}  ✗ {msg}{NC}", file=sys.stderr)
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════
def run(cmd, check=True, capture=False, **kwargs):
    """Run a shell command. Returns CompletedProcess."""
    if isinstance(cmd, str):
        cmd = cmd.split()
    # start_new_session prevents child from receiving signals meant for our process group
    return subprocess.run(cmd, capture_output=capture, text=True, check=check,
                          start_new_session=True, **kwargs)

def run_quiet(cmd, **kwargs):
    """Run silently, swallow errors."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False,
                              start_new_session=True, **kwargs)
    except Exception:
        return None

def cast(*args) -> str:
    """Run a cast command, return stdout."""
    result = subprocess.run(
        ["cast"] + list(args) + ["--rpc-url", RETH_RPC],
        capture_output=True, text=True, check=True
    )
    return result.stdout.strip()

def cast_rpc(method, *params, rpc_url=None) -> str:
    """Call cast rpc."""
    url = rpc_url or RETH_RPC
    cmd = ["cast", "rpc", method] + list(params) + ["--rpc-url", url]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()

def docker_compose(compose_file, *args, check=True):
    """Run docker compose with the given compose file."""
    cmd = ["docker", "compose", "-f", str(compose_file), "--env-file", str(ENV_FILE)] + list(args)
    # start_new_session: docker compose down sends SIGTERM to its process group;
    # without this, it kills the parent Python script (exit 0, no error).
    return subprocess.run(cmd, capture_output=True, text=True, check=check,
                          start_new_session=True)

def wait_for_rpc(url, timeout=60, label="RPC"):
    """Wait for an RPC endpoint to respond."""
    for i in range(1, timeout + 1):
        try:
            result = subprocess.run(
                ["cast", "block-number", "--rpc-url", url],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        if i % 15 == 0:
            dim(f"Waiting for {label}... ({i}/{timeout}s)")
        time.sleep(1)
    fail(f"{label} not reachable after {timeout}s")

def http_get(url) -> int:
    """GET a URL, return status code."""
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status
    except Exception:
        return 0

def http_post(url) -> int:
    """POST to a URL, return status code."""
    try:
        req = urllib.request.Request(url, data=b'', method='POST')
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status
    except Exception:
        return 0

def load_env() -> dict:
    """Load keys from .env file."""
    env = {}
    wanted = {
        'MAINNET_RPC_URL', 'FORK_BLOCK', 'DEPLOYER_KEY', 'USER_A_KEY',
        'USER_B_KEY', 'USER_C_KEY', 'MM_KEY', 'CHAOS_KEY',
        'INDEXER_PORT', 'DB_PORT'
    }
    for f in [ENV_FILE, RLD_ROOT / ".env"]:
        if f.exists():
            for line in f.read_text().splitlines():
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    if k in wanted and k not in env:
                        env[k] = v.strip().strip('"').strip("'")
    return env

def load_deploy() -> dict:
    with open(DEPLOY_JSON) as f:
        return json.load(f)

# ═══════════════════════════════════════════════════════════════
# STEP 1: TEAR DOWN
# ═══════════════════════════════════════════════════════════════
def teardown(fresh: bool):
    header("STEP 1: TEAR DOWN")

    # Ignore SIGTERM during teardown — docker compose down and pkill
    # can send stray signals that kill this orchestrator script.
    original_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    step("1a", "Stopping simulation stacks (rates-indexer untouched)...")
    docker_compose(COMPOSE_RETH, "down", "-v", check=False)
    docker_compose(COMPOSE_ANVIL, "down", "-v", check=False)
    # NOTE: rates-indexer is intentionally NOT touched here.
    # It is an independent persistent service with its own data volume.
    ok("Simulation stacks stopped (rates-indexer preserved)")

    step("1b", "Killing Reth/Anvil/faucet processes...")
    run_quiet(["pkill", "-f", "reth.*--dev"])
    run_quiet(["pkill", "-f", "anvil"])
    run_quiet(["pkill", "-f", "faucet_server.py"])
    time.sleep(2)
    ok("Processes killed")

    # Restore default SIGTERM handler
    signal.signal(signal.SIGTERM, original_sigterm)

    step("1c", "Clearing deployment.json...")
    DEPLOY_JSON.write_text("{}")
    ok("deployment.json cleared")

    if fresh:
        step("1d", "Wiping Reth datadir (--fresh)...")
        import shutil
        for p in [RETH_DATADIR, str(GENESIS_FILE), str(DEPLOY_SNAPSHOT)]:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.isfile(p):
                os.remove(p)
        ok("Clean slate")

# ═══════════════════════════════════════════════════════════════
# STEP 2: GENERATE GENESIS
# ═══════════════════════════════════════════════════════════════
def generate_genesis(env: dict, no_build: bool):
    header("STEP 2: GENERATE GENESIS")
    fork_block = env.get("FORK_BLOCK", "24660000")
    mainnet_rpc = env.get("MAINNET_RPC_URL", "")
    if not mainnet_rpc:
        fail("MAINNET_RPC_URL not set in .env")

    anvil_pid = None
    try:
        # 2a. Start Anvil
        step("2a", f"Starting Anvil fork (block {fork_block})...")
        os.makedirs("/tmp/anvil-state", exist_ok=True)
        anvil_proc = subprocess.Popen(
            ["anvil",
             "--fork-url", mainnet_rpc,
             "--fork-block-number", fork_block,
             "--chain-id", "31337",
             "--host", "0.0.0.0",
             "--port", str(ANVIL_PORT),
             "--code-size-limit", "100000",
             "--dump-state", "/tmp/anvil-state/state.json"],
            stdout=open(ANVIL_LOG, "w"),
            stderr=subprocess.STDOUT
        )
        anvil_pid = anvil_proc.pid

        block = wait_for_rpc(ANVIL_RPC, timeout=60, label="Anvil")
        ok(f"Anvil ready at block {block} (PID: {anvil_pid})")

        cast_rpc("anvil_setChainId", "31337", rpc_url=ANVIL_RPC)

        # 2a½. Ensure rates-indexer is running (independent service — scrapes Aave from mainnet)
        # Needed in 2b so the deployer can prime the oracle with live rates.
        step("2a½", "Ensuring rates-indexer is running...")
        rates_check = run_quiet(["docker", "inspect", "--format", "{{.State.Status}}",
                                 "docker-rates-indexer-1"])
        if rates_check and rates_check.stdout.strip() == "running":
            ok("Rates-indexer already running (preserved from previous session)")
        else:
            info("Rates-indexer not running — starting it...")
            docker_compose(COMPOSE_RATES, "up", "-d", check=False)
            time.sleep(3)
            ok("Rates-indexer started")

        # 2b. Deploy protocol
        step("2b", "Deploying protocol on Anvil (via docker compose)...")
        dim("This takes ~5-8 min...")
        build_args = ["up", "-d"]
        if not no_build:
            build_args.append("--build")
        docker_compose(COMPOSE_ANVIL, *build_args)

        # Link rates-indexer to Anvil network
        rates_running = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True
        ).stdout
        if "docker-rates-indexer-1" in rates_running:
            run_quiet(["docker", "network", "connect", "docker_default",
                       "docker-rates-indexer-1", "--alias", "rates-indexer"])
            ok("Linked rates-indexer to deployer network (live Aave rate available)")
        else:
            warn("rates-indexer not running — oracle will use default 5% rate")

        # Wait for deployer
        start_time = time.time()
        while True:
            elapsed = int(time.time() - start_time)
            result = run_quiet(["docker", "inspect", "--format",
                               "{{.State.Status}}|{{.State.ExitCode}}",
                               "docker-deployer-1"])
            if result and result.stdout.strip():
                status, exit_code = result.stdout.strip().split("|")
                if status == "exited":
                    if exit_code == "0":
                        ok(f"Deployer completed ({elapsed}s)")
                        cast_rpc("anvil_setChainId", "31337", rpc_url=ANVIL_RPC)
                        break
                    else:
                        logs = run_quiet(["docker", "logs", "docker-deployer-1", "--tail", "30"])
                        fail(f"Deployer exited with code {exit_code}\n{logs.stdout if logs else ''}")
                elif status == "missing":
                    fail("Deployer container not found")

            if elapsed >= 600:
                fail("Deployer timed out (600s)")

            if elapsed > 0 and elapsed % 30 == 0:
                log_line = run_quiet(["docker", "logs", "docker-deployer-1", "--tail", "1"])
                line = log_line.stdout.strip().split('\n')[-1] if log_line else "..."
                dim(f"[{elapsed}s] {line}")
            time.sleep(5)

        # 2c. Warm up contracts
        step("2c", "Warming up contract cache...")
        for addr in WARMUP_ADDRS:
            run_quiet(["cast", "code", addr, "--rpc-url", ANVIL_RPC])
        # Warm Aave internals
        run_quiet(["cast", "call", "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
                   "getReserveData(address)", "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                   "--rpc-url", ANVIL_RPC])
        # Warm deployed protocol contracts
        try:
            deploy = load_deploy()
            for key in ["rld_core", "twamm_hook", "wausdc", "position_token",
                        "broker_factory", "broker_router", "swap_router", "mock_oracle"]:
                addr = deploy.get(key)
                if addr:
                    run_quiet(["cast", "code", addr, "--rpc-url", ANVIL_RPC])
        except Exception:
            pass
        ok("Contract cache warmed")

        # 2d. Dump Anvil state
        step("2d", "Dumping Anvil state...")
        raw_result = subprocess.run(
            ["cast", "rpc", "anvil_dumpState", "--rpc-url", ANVIL_RPC],
            capture_output=True, text=True, check=True
        )
        raw = raw_result.stdout.strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        if raw.startswith("0x"):
            raw = raw[2:]

        raw_bytes = bytes.fromhex(raw)
        if raw_bytes[:2] == b'\x1f\x8b':
            data = json.loads(gzip.decompress(raw_bytes))
        else:
            data = json.loads(raw_bytes)

        with open("/tmp/anvil-dump.json", "w") as f:
            json.dump(data, f)
        accounts = data.get("accounts", data)
        print(f"  {len(accounts)} accounts dumped")
        ok("State dumped")

        # 2e. Convert to Reth genesis
        step("2e", "Converting to Reth genesis...")
        fund_keys = []
        for var in ["DEPLOYER_KEY", "USER_A_KEY", "USER_B_KEY",
                     "USER_C_KEY", "MM_KEY", "CHAOS_KEY"]:
            if env.get(var):
                fund_keys.append(env[var])

        convert_cmd = [
            "python3", str(SCRIPT_DIR / "convert_state.py"),
            "--input", "/tmp/anvil-dump.json",
            "--output", str(GENESIS_FILE),
            "--chain-id", "31337",
            "--fund-keys", *fund_keys,
            "--anvil-rpc", ANVIL_RPC,
            "--patch-contracts", *PATCH_CONTRACTS,
        ]
        result = run(convert_cmd, capture=True)
        print(result.stdout, end="")
        os.remove("/tmp/anvil-dump.json")
        genesis_size = GENESIS_FILE.stat().st_size // 1024
        ok(f"Genesis: {GENESIS_FILE} ({genesis_size}K)")

        # 2f. Save deployment snapshot
        step("2f", "Saving deployment snapshot...")
        import shutil
        shutil.copy2(DEPLOY_JSON, DEPLOY_SNAPSHOT)
        snap = json.loads(DEPLOY_SNAPSHOT.read_text())
        snap["deploy_block"] = 0
        snap["fork_block"] = 0
        snap["session_start_block"] = 0
        snap["deploy_timestamp"] = 0
        DEPLOY_SNAPSHOT.write_text(json.dumps(snap, indent=2))
        print("  Patched deploy_block/fork_block → 0 for Reth")
        ok("Deployment config saved (block numbers patched for Reth)")
        shutil.copy2(DEPLOY_SNAPSHOT, DEPLOY_JSON)

    finally:
        # 2g. Tear down Anvil
        step("2g", "Tearing down Anvil stack...")
        docker_compose(COMPOSE_ANVIL, "down", "-v", check=False)
        if anvil_pid:
            try:
                os.kill(anvil_pid, signal.SIGTERM)
                os.waitpid(anvil_pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        ok("Anvil stack stopped")

# ═══════════════════════════════════════════════════════════════
# STEP 3: START RETH
# ═══════════════════════════════════════════════════════════════
def start_reth(skip_genesis: bool):
    header("STEP 3: START RETH")

    # Wipe datadir if genesis was just regenerated
    if not skip_genesis:
        import shutil
        shutil.rmtree(RETH_DATADIR, ignore_errors=True)

    step("3a", "Starting Reth node...")
    result = run(["bash", str(SCRIPT_DIR / "start_reth.sh"), "--background"], capture=True)
    print(result.stdout, end="")

    block_str = wait_for_rpc(RETH_RPC, timeout=60, label="Reth")
    ok(f"Reth ready at block {block_str}")

    # Verify contracts
    step("3b", "Verifying protocol contracts...")
    deploy = load_deploy()
    verify_addr = deploy.get("twamm_hook") or deploy.get("rld_core")
    if verify_addr:
        code = cast("code", verify_addr)
        if len(code) > 4:
            ok(f"Protocol verified at {verify_addr}")
        else:
            fail(f"Contract at {verify_addr} has no code — genesis incomplete")

# ═══════════════════════════════════════════════════════════════
# FAUCET SERVER
# ═══════════════════════════════════════════════════════════════
WHALE_KEY = "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"
USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC_ADDR = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
AAVE_POOL_ADDR = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
FAUCET_FUND = 1_000_000_000 * 10**6   # $1B USDC for faucet SimFunder

def start_faucet(deploy: dict):
    """Deploy SimFunder (if needed), fund with $1B, and start faucet_server.py."""
    step("4h", "Starting faucet server...")

    wausdc = deploy.get("wausdc", "")
    sim_funder_addr = deploy.get("sim_funder", "")

    # Deploy SimFunder if missing or no code
    if sim_funder_addr:
        code = cast("code", sim_funder_addr)
        if len(code) <= 4:
            warn(f"SimFunder at {sim_funder_addr} has no code — redeploying")
            sim_funder_addr = ""

    if not sim_funder_addr:
        info("Deploying SimFunder...")
        env = load_env()
        deployer_key = env.get("DEPLOYER_KEY", "")
        if not deployer_key:
            warn("No DEPLOYER_KEY — cannot deploy SimFunder, skipping faucet")
            return
        result = subprocess.run(
            ["forge", "create", "src/periphery/SimFunder.sol:SimFunder",
             "--private-key", deployer_key,
             "--rpc-url", RETH_RPC,
             "--broadcast", "--legacy",
             "--constructor-args", USDC_ADDR, AUSDC_ADDR, wausdc, AAVE_POOL_ADDR],
            cwd=str(RLD_ROOT / "contracts"),
            capture_output=True, text=True
        )
        for line in result.stdout.split('\n'):
            if 'Deployed to:' in line:
                sim_funder_addr = line.split('Deployed to:')[1].strip()
                break
        if not sim_funder_addr:
            warn(f"SimFunder deploy failed: {result.stderr[-200:]}")
            return
        ok(f"SimFunder deployed: {sim_funder_addr}")

        # Persist to deployment files
        for path in [DEPLOY_JSON, DEPLOY_SNAPSHOT]:
            if path.exists():
                data = json.loads(path.read_text())
                data["sim_funder"] = sim_funder_addr
                path.write_text(json.dumps(data, indent=2))

    # Fund SimFunder with $1B USDC from whale (idempotent — tops up if needed)
    try:
        bal_raw = cast("call", USDC_ADDR, "balanceOf(address)(uint256)", sim_funder_addr)
        current_bal = int(bal_raw.split()[0])
        if current_bal < FAUCET_FUND:
            top_up = FAUCET_FUND - current_bal
            info(f"Funding SimFunder with ${top_up / 10**6:,.0f} USDC (current: ${current_bal / 10**6:,.0f})...")
            result = subprocess.run(
                ["cast", "send", "--legacy", "--json",
                 USDC_ADDR, "transfer(address,uint256)", sim_funder_addr, str(top_up),
                 "--private-key", WHALE_KEY, "--rpc-url", RETH_RPC],
                capture_output=True, text=True, check=True
            )
            receipt = json.loads(result.stdout)
            if receipt.get("status") != "0x1":
                warn("SimFunder USDC funding tx failed")
            else:
                ok(f"SimFunder funded: ${FAUCET_FUND / 10**6:,.0f} USDC")
        else:
            ok(f"SimFunder already funded: ${current_bal / 10**6:,.0f} USDC")
    except Exception as e:
        warn(f"Could not fund SimFunder: {e}")

    # Faucet server lifecycle is now managed by Docker Compose
    # (reth-faucet-1 container with restart: unless-stopped).
    # The container is started automatically via docker-compose.reth.yml.
    # To restart manually: docker compose -f docker/reth/docker-compose.reth.yml up -d faucet

    # Verify health (whether Docker-managed or already running)
    for i in range(1, 16):
        if http_get("http://localhost:8088/health") == 200:
            ok("Faucet server healthy on :8088 (Docker-managed)")
            return
        time.sleep(1)
    warn("Faucet server did not respond to /health within 15s — check: docker logs reth-faucet-1")


# ═══════════════════════════════════════════════════════════════
# STEP 4: LAUNCH SERVICES
# ═══════════════════════════════════════════════════════════════
def launch_services(no_build: bool, with_users: bool):
    header("STEP 4: LAUNCH SERVICES")
    deploy = load_deploy()
    indexer_port = int(os.environ.get("INDEXER_PORT", 8080))
    indexer_url = f"http://localhost:{indexer_port}"

    # 4a. Start indexer + postgres only
    step("4a", "Starting indexer + postgres...")
    build_args = ["up", "-d"]
    if not no_build:
        build_args.append("--build")
    build_args += ["postgres", "indexer"]
    r = docker_compose(COMPOSE_RETH, *build_args)
    if r.stdout:
        for line in r.stdout.strip().split('\n')[-3:]:
            print(f"  {line}")

    # 4b. Wait for indexer health
    step("4b", "Waiting for indexer health...")
    for i in range(1, 121):
        result = run_quiet(["docker", "inspect", "--format",
                           "{{.State.Health.Status}}", "reth-indexer-1"])
        status = result.stdout.strip() if result and result.stdout else "starting"
        if status == "healthy":
            ok(f"Indexer healthy ({i}s)")
            break
        if i % 20 == 0:
            dim(f"Waiting for indexer... ({status})")
        time.sleep(1)

    # 4c. Seed indexer DB
    step("4c", "Seeding indexer DB (POST /admin/reset)...")
    for i in range(1, 31):
        code = http_post(f"{indexer_url}/admin/reset")
        if code == 200:
            ok("Indexer DB seeded successfully")
            break
        if i % 10 == 0:
            dim(f"Indexer reset returned {code}, retrying...")
        time.sleep(2)

    # 4d. Verify /config
    step("4d", "Verifying /config endpoint...")
    config_ok = False
    for _ in range(15):
        if http_get(f"{indexer_url}/config") == 200:
            ok("/config returns 200 — daemons will connect")
            config_ok = True
            break
        time.sleep(2)
    if not config_ok:
        warn("/config not returning 200 — daemons may be slow to start")

    # 4e. Seed initial pool state
    seed_pool_state(deploy)

    # 4f. User setup
    if with_users:
        step("4f", "Running simulation user setup (--with-users)...")
        run(["python3", str(SCRIPT_DIR / "setup_simulation.py")], check=True)
    else:
        info("User setup skipped (use --with-users to create brokers/LP on Reth)")

    # 4g. Start trading bots (after users are funded)
    step("4g", "Starting mm-daemon + chaos-trader...")
    bot_args = ["up", "-d"]
    if not no_build:
        bot_args.append("--build")
    bot_args += ["mm-daemon", "chaos-trader"]
    docker_compose(COMPOSE_RETH, *bot_args)
    ok("Trading bots started")

    # 4h. Ensure rates-indexer is running and bridged (standalone — scrapes live Aave rates)
    step("4h", "Ensuring rates-indexer is running...")
    rates_check = run_quiet(["docker", "inspect", "--format", "{{.State.Status}}",
                              "docker-rates-indexer-1"])
    if not (rates_check and rates_check.stdout.strip() == "running"):
        info("Rates-indexer not running — starting it...")
        docker_compose(COMPOSE_RATES, "up", "-d", check=False)
        time.sleep(3)
    # Bridge rates-indexer to the reth network so bots can reach it
    run_quiet(["docker", "network", "connect", "reth_default",
               "docker-rates-indexer-1", "--alias", "rates-indexer"])
    ok("Rates-indexer running (port 8081, bridged to reth network)")

    # 4i. Start faucet server
    start_faucet(deploy)

# ═══════════════════════════════════════════════════════════════
# POOL STATE SEEDING
# ═══════════════════════════════════════════════════════════════
def seed_pool_state(deploy: dict):
    """Read pool state from Reth and seed the indexer's block_states table."""
    step("4e", "Seeding initial pool state from on-chain data...")

    market_id = deploy.get("market_id")
    pool_id = deploy.get("pool_id")
    wausdc = deploy.get("wausdc")
    pos_token = deploy.get("position_token")

    if not market_id or market_id == "null":
        warn("No market_id in deployment.json — skipping pool state seed")
        return

    try:
        # Read token balances
        t0_raw = cast("call", wausdc, "balanceOf(address)(uint256)", POOL_MANAGER)
        t1_raw = cast("call", pos_token, "balanceOf(address)(uint256)", POOL_MANAGER)
        t0_bal = int(t0_raw.split()[0])
        t1_bal = int(t1_raw.split()[0])

        # Token order (token0 = lower address)
        if wausdc.lower() < pos_token.lower():
            token0_bal, token1_bal = t0_bal, t1_bal
        else:
            token0_bal, token1_bal = t1_bal, t0_bal

        # Read Slot0 via extsload
        slot0_input = subprocess.run(
            ["cast", "abi-encode", "f(bytes32,uint256)", pool_id, "6"],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        slot0_key = subprocess.run(
            ["cast", "keccak", slot0_input],
            capture_output=True, text=True, check=True
        ).stdout.strip()

        slot0_raw = cast("call", POOL_MANAGER, "extsload(bytes32)(bytes32)", slot0_key)
        liq_slot = hex(int(slot0_key, 16) + 3)
        liq_raw = cast("call", POOL_MANAGER, "extsload(bytes32)(bytes32)", liq_slot)

        # Decode Slot0: sqrtPriceX96(160) | tick(24) | protocolFee(24) | lpFee(24)
        slot0 = int(slot0_raw, 16)
        liquidity = int(liq_raw, 16)
        sqrt_price = slot0 & ((1 << 160) - 1)
        tick_raw = (slot0 >> 160) & ((1 << 24) - 1)
        tick = tick_raw if tick_raw < (1 << 23) else tick_raw - (1 << 24)

        # Mark price
        raw_price = (sqrt_price / (2**96))**2
        if wausdc.lower() < pos_token.lower():
            mark = 1.0 / raw_price if raw_price > 0 else 0
        else:
            mark = raw_price

        # Insert into block_states via psql
        sql = f"""
            INSERT INTO block_states
              (market_id, block_number, block_timestamp,
               sqrt_price_x96, tick, mark_price, liquidity,
               token0_balance, token1_balance,
               fee_growth_global0, fee_growth_global1,
               normalization_factor, total_debt)
            VALUES (
              '{market_id}', 0, 0,
              '{sqrt_price}', {tick}, {mark}, '{liquidity}',
              {token0_bal}, {token1_bal},
              '0', '0', 1.0, 0)
            ON CONFLICT (market_id, block_number) DO UPDATE SET
              sqrt_price_x96 = EXCLUDED.sqrt_price_x96,
              tick           = EXCLUDED.tick,
              mark_price     = EXCLUDED.mark_price,
              liquidity      = EXCLUDED.liquidity,
              token0_balance = EXCLUDED.token0_balance,
              token1_balance = EXCLUDED.token1_balance,
              normalization_factor = EXCLUDED.normalization_factor;
        """
        subprocess.run(
            ["docker", "exec", "reth-postgres-1",
             "psql", "-U", "rld", "-d", "rld_indexer", "-c", sql],
            capture_output=True, text=True, check=True
        )
        ok(f"Pool state seeded: token0={token0_bal} token1={token1_bal} tick={tick} mark={mark}")

    except Exception as e:
        warn(f"Could not read on-chain pool state — TVL may be inaccurate ({e})")

# ═══════════════════════════════════════════════════════════════
# STATUS REPORT
# ═══════════════════════════════════════════════════════════════
def status_report():
    header("STATUS REPORT")

    reth_block = "?"
    try:
        reth_block = cast("block-number")
    except Exception:
        pass

    reth_pid = "?"
    try:
        r = subprocess.run(["pgrep", "-f", "reth.*--dev"],
                          capture_output=True, text=True)
        reth_pid = r.stdout.strip().split('\n')[0] if r.stdout.strip() else "?"
    except Exception:
        pass

    reth_mem = "?"
    if reth_pid != "?":
        try:
            r = subprocess.run(["ps", "-o", "rss=", "-p", reth_pid],
                              capture_output=True, text=True)
            reth_mem = f"{int(r.stdout.strip()) // 1024}" if r.stdout.strip() else "?"
        except Exception:
            pass

    print(f"{MAGENTA}╔═══════════════════════════════════════════════════════════╗{NC}")
    print(f"{MAGENTA}║           RLD SIMULATION — RETH MODE 🦀                  ║{NC}")
    print(f"{MAGENTA}╠═══════════════════════════════════════════════════════════╣{NC}")
    print(f"{MAGENTA}║{NC}  {'Reth':<8}  ✅  Block: {reth_block}  PID: {reth_pid}  RSS: {reth_mem}MB{MAGENTA}       ║{NC}")
    print(f"{MAGENTA}╠═══════════════════════════════════════════════════════════╣{NC}")

    result = run_quiet(["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}"])
    if result and result.stdout:
        for line in result.stdout.strip().split('\n'):
            if "NAMES" in line:
                continue
            parts = line.split(None, 1)
            name = parts[0] if parts else ""
            status_text = parts[1] if len(parts) > 1 else ""
            icon = "❌" if any(x in status_text for x in ["unhealthy", "Exited"]) else "✅"
            print(f"{MAGENTA}║{NC}  {name:<28} {icon} {status_text:<22}{MAGENTA}║{NC}")

    print(f"{MAGENTA}╚═══════════════════════════════════════════════════════════╝{NC}")
    print()
    print(f"{GREEN}✅ All systems operational! (RSS: {reth_mem} MB){NC}")
    print()
    print(f"{DIM}Commands:{NC}")
    print(f"  Logs:     docker compose -f {COMPOSE_RETH} logs -f")
    print(f"  Stop:     docker compose -f {COMPOSE_RETH} down -v && pkill -f 'reth.*--dev'")
    print(f"  Restart:  python3 {__file__} --skip-genesis   (fast)")
    print(f"  Rebuild:  python3 {__file__} --fresh          (full)")
    print(f"  Reth log: tail -f {RETH_LOG}")

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Restart Reth simulation environment")
    parser.add_argument("--no-build",     action="store_true", help="Skip Docker image rebuilds")
    parser.add_argument("--skip-genesis", action="store_true", help="Reuse existing genesis.json")
    parser.add_argument("--fresh",        action="store_true", help="Wipe Reth datadir & regenerate")
    parser.add_argument("--with-users",   action="store_true", help="Run broker/LP setup on Reth")
    args = parser.parse_args()

    # Auto-detect: if genesis exists and not --fresh, skip genesis
    if args.fresh:
        args.skip_genesis = False
    elif not args.skip_genesis and GENESIS_FILE.exists():
        args.skip_genesis = True

    # Preflight
    header("PREFLIGHT CHECKS")
    for cmd in ["docker", "cast", "reth"]:
        if not subprocess.run(["which", cmd], capture_output=True).returncode == 0:
            fail(f"{cmd} not found in PATH")
    ok("Required tools found (docker, cast, reth)")

    r = subprocess.run(["docker", "compose", "version"], capture_output=True)
    if r.returncode != 0:
        fail("docker compose v2 not available")
    ok("Docker compose v2 available")

    if not ENV_FILE.exists():
        fail(f"{ENV_FILE} not found")

    env = load_env()

    if not args.skip_genesis:
        if not env.get("MAINNET_RPC_URL"):
            fail("MAINNET_RPC_URL not set in .env")
        if not subprocess.run(["which", "anvil"], capture_output=True).returncode == 0:
            fail("anvil not found — needed to generate genesis")
        ok("Anvil + mainnet RPC available for genesis generation")
    else:
        ok("Will reuse existing genesis.json (--skip-genesis)")

    # Execute steps
    teardown(args.fresh)

    if not args.skip_genesis:
        generate_genesis(env, args.no_build)
    else:
        header("STEP 2: GENESIS (SKIPPED)")
        ok(f"Reusing existing genesis.json ({GENESIS_FILE.stat().st_size // 1024}K)")
        if not DEPLOY_SNAPSHOT.exists():
            fail("deployment-snapshot.json not found — run without --skip-genesis first")
        import shutil
        shutil.copy2(DEPLOY_SNAPSHOT, DEPLOY_JSON)
        ok("deployment.json restored from snapshot (deploy_block=0)")

    start_reth(args.skip_genesis)
    launch_services(args.no_build, args.with_users)
    status_report()

if __name__ == "__main__":
    main()
