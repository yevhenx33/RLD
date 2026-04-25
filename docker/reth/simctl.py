#!/usr/bin/env python3
"""
Phase-based controller for the RLD Reth simulation stack.

The old restart-reth.sh flow grew into a large orchestration script.  This CLI
keeps the same operational steps, but each phase is independently runnable so a
failure in users, indexing, or bots does not require rebuilding genesis.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent
RLD_ROOT = DOCKER_DIR.parent

COMPOSE_ANVIL = DOCKER_DIR / "docker-compose.yml"
COMPOSE_RETH = SCRIPT_DIR / "docker-compose.reth.yml"
ENVIO_COMPOSE = RLD_ROOT / "data-pipeline" / "docker-compose.yml"
ENV_FILE = DOCKER_DIR / ".env"

DEPLOY_JSON = DOCKER_DIR / "deployment.json"
GENESIS_FILE = SCRIPT_DIR / "genesis.json"
DEPLOY_SNAPSHOT = SCRIPT_DIR / "deployment-snapshot.json"
SNAPSHOT_DIR = SCRIPT_DIR / "snapshots"
STATE_DIR = SCRIPT_DIR / ".sim"
STATE_FILE = STATE_DIR / "state.json"

LOCK_FILE = Path("/tmp/rld-reth-simctl.lock")
ANVIL_PID_FILE = Path("/tmp/rld-restart-reth.anvil.pid")
ANVIL_LOG = Path("/tmp/anvil.log")
ANVIL_STATE_DIR = Path("/tmp/anvil-state")
ANVIL_DUMP_JSON = Path("/tmp/anvil-dump.json")

RETH_PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "reth")
CHAIN_ID = 31337
ANVIL_PORT = 8545
ANVIL_RPC = f"http://localhost:{ANVIL_PORT}"
RETH_RPC = f"http://localhost:{os.getenv('RETH_PORT', '8545')}"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
V4_DEPENDENCIES = [
    "0x000000000004444c5dc75cB358380D2e3dE08A90",
    "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e",
    "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203",
    "0x7ffe42c4a5deea5b0fec41c94c136cf115597227",
    "0xd1428ba554f4c8450b763a0b2040a4935c63f06c",
    "0x66a9893cc07d91d95644aedd05d03f95e1dba8af",
    "0x000000000022D473030F116dDEE9F6B43aC78BA3",
]
WARM_CONTRACTS = [
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "0x43506849D7C04F9138D1A2050bbF3A0c054402dd",
    "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e",
    "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497",
    *V4_DEPENDENCIES,
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb",
]
PATCH_CONTRACTS = [
    *V4_DEPENDENCIES,
    "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
    "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
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
RETRY_MARKERS = (
    "temporary internal error",
    "please retry",
    "failed to get account",
    "http error 500",
    "code\":19",
    "code: 19",
    "nonce too low",
    "replacement transaction underpriced",
    "contract was not deployed",
)


class SimctlError(RuntimeError):
    pass


@dataclass
class Options:
    no_build: bool = False
    fresh: bool = False
    with_users: bool = False
    with_bots: bool = False
    skip_e2e: bool = False
    skip_genesis: bool = False
    from_snapshot: bool = False
    env: dict[str, str] = field(default_factory=dict)
    rates_api_url: str = ""
    deploy_rate_max_age_seconds: int = 7200


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(msg, flush=True)


def header(msg: str) -> None:
    log(f"\n=== {msg} ===")


def info(msg: str) -> None:
    log(f"[..] {msg}")


def ok(msg: str) -> None:
    log(f"[OK] {msg}")


def warn(msg: str) -> None:
    log(f"[WARN] {msg}")


def fail(msg: str) -> None:
    raise SimctlError(msg)


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def merged_env(extra: dict[str, str] | None = None, base: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(base or load_env_file())
    if extra:
        env.update({k: str(v) for k, v in extra.items() if v is not None})
    return env


def retryable_output(output: str) -> bool:
    lower = output.lower()
    return any(marker in lower for marker in RETRY_MARKERS)


def run(
    cmd: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    retries: int = 0,
    retry_predicate: Callable[[str], bool] | None = None,
    timeout: int | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    attempt = 0
    last: subprocess.CompletedProcess[str] | None = None
    while attempt <= retries:
        attempt += 1
        try:
            proc = subprocess.run(
                list(cmd),
                cwd=str(cwd) if cwd else None,
                env=env,
                text=True,
                timeout=timeout,
                capture_output=capture,
            )
        except subprocess.TimeoutExpired as exc:
            output = f"{exc.stdout or ''}\n{exc.stderr or ''}"
            if attempt <= retries and (retry_predicate is None or retry_predicate(output)):
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            raise

        last = proc
        if proc.returncode == 0:
            return proc

        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if attempt <= retries and (retry_predicate is None or retry_predicate(output)):
            sleep_s = min(2 ** (attempt - 1), 8)
            info(f"Transient command failure ({attempt}/{retries + 1}), retrying in {sleep_s}s")
            time.sleep(sleep_s)
            continue
        break

    assert last is not None
    if check:
        output = ((last.stdout or "") + "\n" + (last.stderr or "")).strip()
        fail(f"Command failed ({last.returncode}): {' '.join(cmd)}\n{output[-1200:]}")
    return last


def http_json(url: str, timeout: int = 5) -> dict[str, Any] | list[Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_status(
    url: str,
    *,
    method: str = "GET",
    timeout: int = 5,
    headers: dict[str, str] | None = None,
) -> int:
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError):
        return 0


def wait_http(url: str, timeout_seconds: int = 60, expected: Iterable[int] = (200,)) -> None:
    expected_set = set(expected)
    last_status = 0
    for i in range(1, timeout_seconds + 1):
        last_status = http_status(url, timeout=3)
        if last_status in expected_set:
            return
        if i % 15 == 0:
            info(f"Waiting for {url} (last_status={last_status})")
        time.sleep(1)
    fail(f"HTTP endpoint did not become ready: {url} (last_status={last_status})")


def docker_compose(
    compose_file: Path,
    *args: str,
    capture: bool = False,
    check: bool = True,
    env_file: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-f", str(compose_file)]
    if env_file and ENV_FILE.exists():
        cmd += ["--env-file", str(ENV_FILE)]
    cmd += list(args)
    return run(cmd, capture=capture, check=check, env=merged_env())


def compose_cid(service: str) -> str:
    proc = docker_compose(COMPOSE_RETH, "ps", "-q", service, capture=True, check=False)
    return (proc.stdout or "").strip().splitlines()[0] if proc.stdout.strip() else ""


def inspect_container(cid: str) -> dict[str, Any]:
    if not cid:
        return {"state": "missing", "health": None}
    proc = run(["docker", "inspect", cid], capture=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"state": "missing", "health": None}
    data = json.loads(proc.stdout)[0]
    state = data.get("State", {})
    health = state.get("Health", {}).get("Status") if state.get("Health") else None
    return {"state": state.get("Status", "unknown"), "health": health}


def service_status(service: str) -> dict[str, Any]:
    cid = compose_cid(service)
    status = inspect_container(cid)
    status["id"] = cid
    return status


def wait_container(
    service: str,
    *,
    state: str = "running",
    health: str | None = "healthy",
    timeout_seconds: int = 90,
) -> None:
    last = {"state": "missing", "health": None}
    for i in range(1, timeout_seconds + 1):
        last = service_status(service)
        state_ok = last["state"] == state
        health_ok = health is None or last["health"] in (None, health)
        if state_ok and health_ok:
            ok(f"{service} ready (state={last['state']}, health={last['health'] or 'none'})")
            return
        if i % 15 == 0:
            info(f"Waiting for {service} (state={last['state']}, health={last['health']})")
        time.sleep(1)
    docker_compose(COMPOSE_RETH, "logs", service, "--tail", "40", check=False)
    fail(f"{service} did not become ready (state={last['state']}, health={last['health']})")


def cast_call(*args: str, rpc_url: str = RETH_RPC, check: bool = True) -> str:
    proc = run(["cast", *args, "--rpc-url", rpc_url], capture=True, check=check, retries=2, retry_predicate=retryable_output)
    return (proc.stdout or "").strip()


def cast_block_number(rpc_url: str = RETH_RPC) -> str:
    return cast_call("block-number", rpc_url=rpc_url)


def cast_code(addr: str, rpc_url: str = RETH_RPC) -> str:
    return cast_call("code", addr, rpc_url=rpc_url)


def has_code(addr: str, rpc_url: str = RETH_RPC) -> bool:
    code = cast_code(addr, rpc_url=rpc_url)
    return bool(code) and code != "0x" and len(code) > 4


def state_template() -> dict[str, Any]:
    return {
        "phase": "unknown",
        "fresh": False,
        "with_users": False,
        "with_bots": False,
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "artifacts": {
            "deployment_json": str(DEPLOY_JSON.relative_to(RLD_ROOT)),
            "genesis_json": str(GENESIS_FILE.relative_to(RLD_ROOT)),
            "deployment_snapshot": str(DEPLOY_SNAPSHOT.relative_to(RLD_ROOT)),
            "snapshot": "",
        },
        "services": {},
        "runtime": "unknown",
        "error": "",
    }


def read_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return state_template()
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return state_template()


def write_state(**updates: Any) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = read_state()
    state.update(updates)
    state["updated_at"] = now_iso()
    services = updates.get("services")
    if services is None:
        state["services"] = collect_services(compact=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(STATE_FILE)


def collect_services(*, compact: bool = False) -> dict[str, Any]:
    services = {}
    for service in ["reth", "postgres", "indexer", "faucet", "mm-daemon", "chaos-trader"]:
        status = service_status(service)
        if compact:
            if status["state"] == "missing":
                value = "missing"
            elif status["health"]:
                value = status["health"]
            else:
                value = status["state"]
            services[service.replace("-", "_")] = value
        else:
            services[service.replace("-", "_")] = {
                "state": status["state"],
                "health": status["health"],
            }
    return services


def acquire_lock() -> Any:
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock = LOCK_FILE.open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fail(f"Another simctl run is active (lock: {LOCK_FILE})")
    return lock


def kill_pid(pid: int | str | None, label: str) -> None:
    if not pid:
        return
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return
    try:
        os.kill(pid_int, 0)
    except OSError:
        return
    warn(f"Stopping {label} (pid={pid_int})")
    try:
        os.kill(pid_int, 15)
    except OSError:
        return
    for _ in range(20):
        try:
            os.kill(pid_int, 0)
        except OSError:
            return
        time.sleep(0.1)
    try:
        os.kill(pid_int, 9)
    except OSError:
        pass


def cleanup_tracked_anvil() -> None:
    if ANVIL_PID_FILE.exists():
        kill_pid(ANVIL_PID_FILE.read_text().strip(), "tracked temporary Anvil")
        ANVIL_PID_FILE.unlink(missing_ok=True)


def volume_name(short_name: str) -> str:
    return f"{RETH_PROJECT}_{short_name}"


def ensure_commands(commands: Sequence[str]) -> None:
    missing = [cmd for cmd in commands if shutil.which(cmd) is None]
    if missing:
        fail(f"Missing required command(s): {', '.join(missing)}")


def build_options(args: argparse.Namespace) -> Options:
    env = load_env_file()
    rates_port = env.get("RATES_API_PORT") or env.get("ENVIO_API_PORT") or env.get("ENVIO_PORT") or "5000"
    rates_url = (
        env.get("RATES_API_BASE_URL")
        or env.get("ENVIO_API_URL")
        or env.get("API_URL")
        or env.get("RATES_API_URL")
        or f"http://localhost:{rates_port}"
    )
    with_bots = bool(getattr(args, "with_bots", False))
    with_users = bool(getattr(args, "with_users", False)) or with_bots
    deploy_rate_max_age_seconds = int(env.get("DEPLOY_RATE_MAX_AGE_SECONDS") or "7200")
    return Options(
        no_build=bool(getattr(args, "no_build", False)),
        fresh=bool(getattr(args, "fresh", False)),
        with_users=with_users,
        with_bots=with_bots,
        skip_e2e=bool(getattr(args, "skip_e2e", False)),
        skip_genesis=bool(getattr(args, "skip_genesis", False)),
        from_snapshot=bool(getattr(args, "from_snapshot", False)),
        env=env,
        rates_api_url=rates_url,
        deploy_rate_max_age_seconds=deploy_rate_max_age_seconds,
    )


def preflight(opts: Options, *, need_genesis: bool = False) -> None:
    header("Preflight")
    ensure_commands(["docker", "cast", "jq", "curl", "python3", "tar", "sha256sum"])
    run(["docker", "compose", "version"], capture=True)
    if not ENV_FILE.exists():
        fail(f"{ENV_FILE} not found")
    if need_genesis:
        ensure_commands(["anvil"])
        if not opts.env.get("MAINNET_RPC_URL"):
            fail(f"MAINNET_RPC_URL is required in {ENV_FILE} to generate genesis")
    if not opts.env.get("INDEXER_ADMIN_TOKEN") and opts.env.get("INDEXER_ALLOW_UNSAFE_ADMIN_RESET", "false").lower() != "true":
        fail("INDEXER_ADMIN_TOKEN is required unless INDEXER_ALLOW_UNSAFE_ADMIN_RESET=true")
    ok("Required tools and environment are available")
    write_state(phase="preflight", fresh=opts.fresh, with_users=opts.with_users, with_bots=opts.with_bots, error="")


def teardown(*, fresh: bool) -> None:
    header("Teardown")
    docker_compose(COMPOSE_RETH, "down", check=False)
    docker_compose(COMPOSE_ANVIL, "down", "-v", check=False)
    run(["docker", "network", "create", "rld_shared"], check=False, capture=True)
    cleanup_tracked_anvil()
    DEPLOY_JSON.write_text("{}\n")
    if fresh:
        run(["docker", "volume", "rm", volume_name("reth-datadir"), volume_name("postgres-data-reth")], check=False, capture=True)
        GENESIS_FILE.unlink(missing_ok=True)
        DEPLOY_SNAPSHOT.unlink(missing_ok=True)
    ok("Simulation stack stopped; infra/frontend left untouched")
    write_state(phase="teardown")


def restore_or_reuse_genesis(*, from_snapshot: bool = False) -> None:
    header("Genesis")
    if not GENESIS_FILE.exists() or from_snapshot:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        snapshots = sorted(SNAPSHOT_DIR.glob("genesis-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not snapshots:
            fail("No genesis.json and no genesis snapshots found; run restart --fresh first")
        snapshot = snapshots[0]
        checksum_file = snapshot.with_suffix(snapshot.suffix + ".sha256")
        if checksum_file.exists():
            expected = checksum_file.read_text().split()[0]
            actual = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            if expected != actual:
                fail(f"Snapshot checksum validation failed for {snapshot.name}")
            ok(f"Snapshot checksum verified: {snapshot.name}")
        with tarfile.open(snapshot, "r:gz") as tar:
            tar.extractall(SCRIPT_DIR)
        ok(f"Genesis restored from snapshot: {snapshot.name}")
        write_state(phase="restore-genesis", artifacts={**read_state().get("artifacts", {}), "snapshot": str(snapshot.relative_to(RLD_ROOT))})
    else:
        ok(f"Reusing existing genesis.json ({GENESIS_FILE.stat().st_size / 1024 / 1024:.1f} MiB)")
        write_state(phase="restore-genesis")
    if not DEPLOY_SNAPSHOT.exists():
        fail("deployment-snapshot.json not found; regenerate genesis without --skip-genesis")
    shutil.copyfile(DEPLOY_SNAPSHOT, DEPLOY_JSON)
    ok("deployment.json restored from deployment-snapshot.json")


def ensure_rates_api(opts: Options, max_attempts: int = 45) -> str:
    if ENVIO_COMPOSE.exists():
        docker_compose(ENVIO_COMPOSE, "up", "-d", "graphql_api", check=False, env_file=False)

    fallback = f"http://localhost:{opts.env.get('RATES_API_PORT') or opts.env.get('ENVIO_API_PORT') or opts.env.get('ENVIO_PORT') or '5000'}"
    for candidate, attempts in [(opts.rates_api_url, 20), (fallback, max_attempts)]:
        if not candidate:
            continue
        for i in range(1, attempts + 1):
            if http_status(f"{candidate.rstrip('/')}/healthz", timeout=4) == 200:
                ok(f"Rates API reachable at {candidate}")
                wait_http(f"{candidate.rstrip('/')}/readyz", timeout_seconds=max_attempts)
                validate_rate_oracle(candidate, opts.deploy_rate_max_age_seconds)
                ok(f"Rates API ready at {candidate}/readyz")
                return candidate
            if i == 10 and candidate == opts.rates_api_url:
                run(["docker", "restart", "rld_graphql_api"], check=False, capture=True)
            time.sleep(1)
        if candidate != fallback:
            warn(f"Primary rates API unavailable at {candidate}; trying fallback {fallback}")
    fail("Rates API is unavailable or not ready")


def validate_rate_oracle(base_url: str, max_age_seconds: int) -> None:
    """Check the deploy-time oracle sample with a window suitable for hourly rates."""
    payload = http_json(f"{base_url.rstrip('/')}/api/v1/oracle/usdc-borrow-apy", timeout=5)
    if not isinstance(payload, dict):
        fail(f"Rates oracle returned unexpected payload: {type(payload)}")
    borrow_apy = payload.get("borrow_apy")
    try:
        rate = float(borrow_apy)
    except (TypeError, ValueError):
        fail(f"Rates oracle returned invalid borrow_apy: {borrow_apy!r}")
    if rate < 0:
        fail(f"Rates oracle returned negative borrow_apy: {rate}")
    age = payload.get("age_seconds")
    if age is not None and int(age) > max_age_seconds:
        fail(
            "Rates oracle sample is stale for deployment "
            f"(age={age}s > {max_age_seconds}s; set DEPLOY_RATE_MAX_AGE_SECONDS to override)"
        )
    ok(f"Rates oracle sample accepted for deployment (borrow_apy={rate}, age={age}s)")


def start_temporary_anvil(opts: Options) -> subprocess.Popen[str]:
    fork_block = opts.env.get("FORK_BLOCK")
    if not fork_block and (RLD_ROOT / ".env").exists():
        for line in (RLD_ROOT / ".env").read_text().splitlines():
            if line.startswith("FORK_BLOCK="):
                fork_block = line.split("=", 1)[1].strip()
                break
    fork_block = fork_block or "24660000"
    ANVIL_STATE_DIR.mkdir(parents=True, exist_ok=True)
    ANVIL_LOG.unlink(missing_ok=True)
    log_file = ANVIL_LOG.open("w")
    proc = subprocess.Popen(
        [
            "anvil",
            "--fork-url",
            opts.env["MAINNET_RPC_URL"],
            "--fork-block-number",
            str(fork_block),
            "--chain-id",
            str(CHAIN_ID),
            "--host",
            "0.0.0.0",
            "--port",
            str(ANVIL_PORT),
            "--code-size-limit",
            "100000",
            "--dump-state",
            str(ANVIL_STATE_DIR / "state.json"),
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        env=merged_env(base=opts.env),
    )
    ANVIL_PID_FILE.write_text(str(proc.pid))
    for _ in range(60):
        if proc.poll() is not None:
            fail(f"Anvil exited early. See {ANVIL_LOG}")
        if run(["cast", "block-number", "--rpc-url", ANVIL_RPC], capture=True, check=False).returncode == 0:
            ok(f"Anvil ready at block {cast_block_number(ANVIL_RPC)} (pid={proc.pid})")
            run(["cast", "rpc", "anvil_setChainId", str(CHAIN_ID), "--rpc-url", ANVIL_RPC], capture=True, check=False)
            return proc
        time.sleep(1)
    fail(f"Anvil did not become ready. See {ANVIL_LOG}")


def deploy_protocol_on_anvil(opts: Options, rate_api_url: str) -> None:
    header("Deploy Protocol On Anvil")
    up_args = ["up", "-d"]
    if not opts.no_build:
        up_args.append("--build")
    up_args += ["postgres", "indexer"]
    docker_compose(COMPOSE_ANVIL, *up_args)

    indexer_port = opts.env.get("INDEXER_PORT", "8080")
    deploy_env = merged_env(
        {
            "RATES_API_BASE_URL": rate_api_url,
            "API_URL": rate_api_url,
            "ENVIO_API_URL": rate_api_url,
            "RATES_API_URL": rate_api_url,
            "REQUIRE_LIVE_RATE": "1",
            "INDEXER_RESET_URL": f"http://localhost:{indexer_port}/admin/reset",
            "INDEXER_ADMIN_TOKEN": opts.env.get("INDEXER_ADMIN_TOKEN", ""),
            "DEPLOYMENT_JSON_OUT": str(DEPLOY_JSON),
            "RPC_URL": ANVIL_RPC,
            "FORK_BLOCK": opts.env.get("FORK_BLOCK", "24660000"),
            "DEPLOYER_KEY": opts.env.get("DEPLOYER_KEY", ""),
            "DEPLOY_BOND_FACTORY": "true",
            # The oracle endpoint is hourly, so deployment uses a deploy-time
            # freshness window without loosening runtime bot policy.
            "MAX_RATE_AGE_SECONDS": str(opts.deploy_rate_max_age_seconds),
        },
        base=opts.env,
    )
    run(
        ["python3", str(DOCKER_DIR / "deployer" / "deploy_protocol_snapshot.py")],
        env=deploy_env,
        retries=1,
        retry_predicate=retryable_output,
        timeout=900,
    )
    ok("Deployment orchestrator completed")


def warm_contract_cache() -> None:
    header("Warm Contract Cache")
    for addr in WARM_CONTRACTS:
        run(["cast", "code", addr, "--rpc-url", ANVIL_RPC], capture=True, check=False)
    run(
        [
            "cast",
            "call",
            "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            "getReserveData(address)",
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "--rpc-url",
            ANVIL_RPC,
        ],
        capture=True,
        check=False,
    )
    deploy = json.loads(DEPLOY_JSON.read_text())
    for key in ["rld_core", "ghost_router", "twap_engine", "twap_engine_lens", "twamm_hook", "wausdc", "position_token", "broker_factory", "broker_router", "swap_router", "mock_oracle"]:
        addr = deploy.get(key)
        if addr and addr != ZERO_ADDRESS:
            run(["cast", "code", addr, "--rpc-url", ANVIL_RPC], capture=True, check=False)
    ok("Contract cache warmed")


def dump_anvil_state() -> None:
    header("Dump Anvil State")
    proc = run(["cast", "rpc", "anvil_dumpState", "--rpc-url", ANVIL_RPC], capture=True, timeout=180)
    raw = (proc.stdout or "").strip()
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if raw.startswith("0x"):
        raw = raw[2:]
    raw_bytes = bytes.fromhex(raw)
    if raw_bytes[:2] == b"\x1f\x8b":
        data = json.loads(gzip.decompress(raw_bytes))
    else:
        data = json.loads(raw_bytes)
    ANVIL_DUMP_JSON.write_text(json.dumps(data))
    accounts = data.get("accounts", data)
    ok(f"Dumped {len(accounts)} accounts")


def convert_state_to_genesis(opts: Options) -> None:
    header("Convert State To Reth Genesis")
    deploy = json.loads(DEPLOY_JSON.read_text())
    fund_keys = [
        opts.env.get(name, "")
        for name in ["DEPLOYER_KEY", "USER_A_KEY", "USER_B_KEY", "USER_C_KEY", "MM_KEY", "CHAOS_KEY"]
        if opts.env.get(name)
    ]
    cmd = [
        "python3",
        str(SCRIPT_DIR / "convert_state.py"),
        "--input",
        str(ANVIL_DUMP_JSON),
        "--output",
        str(GENESIS_FILE),
        "--chain-id",
        str(CHAIN_ID),
        "--fund-keys",
        *fund_keys,
        "--anvil-rpc",
        ANVIL_RPC,
    ]
    wausdc = deploy.get("wausdc")
    sim_funder = deploy.get("sim_funder")
    if wausdc and sim_funder:
        cmd += [
            "--wausdc-address",
            wausdc,
            "--wausdc-reserve-address",
            sim_funder,
            "--wausdc-reserve-amount",
            os.getenv("SIMFUNDER_WAUSDC_RESERVE", "1000000000000000"),
        ]
    cmd += ["--patch-contracts", *PATCH_CONTRACTS]
    run(cmd, timeout=300)
    ANVIL_DUMP_JSON.unlink(missing_ok=True)
    ok(f"Genesis written: {GENESIS_FILE}")


def save_deployment_snapshot() -> None:
    deploy = json.loads(DEPLOY_JSON.read_text())
    normalize_reth_deployment_blocks(deploy)
    DEPLOY_SNAPSHOT.write_text(json.dumps(deploy, indent=2) + "\n")
    shutil.copyfile(DEPLOY_SNAPSHOT, DEPLOY_JSON)
    ok("Deployment snapshot saved and patched for Reth block 0")


def normalize_reth_deployment_blocks(deploy: dict[str, Any]) -> None:
    """Patch snapshot metadata so the simulation indexer follows local Reth blocks."""
    deploy["deploy_block"] = 0
    deploy["fork_block"] = 0
    deploy["session_start_block"] = 0
    deploy["deploy_timestamp"] = 0
    markets = deploy.get("markets")
    if isinstance(markets, dict):
        for entry in markets.values():
            if not isinstance(entry, dict):
                continue
            entry["deploy_block"] = 0
            entry["fork_block"] = 0
            entry["session_start_block"] = 0
            entry["deploy_timestamp"] = 0


def save_genesis_snapshot() -> Path:
    header("Save Genesis Snapshot")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = SNAPSHOT_DIR / f"genesis-{datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
    with tarfile.open(snapshot, "w:gz") as tar:
        tar.add(GENESIS_FILE, arcname="genesis.json")
        tar.add(DEPLOY_SNAPSHOT, arcname="deployment-snapshot.json")
    sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    snapshot.with_suffix(snapshot.suffix + ".sha256").write_text(f"{sha}  {snapshot.name}\n")

    snapshots = sorted(SNAPSHOT_DIR.glob("genesis-*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in snapshots[3:]:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256").unlink(missing_ok=True)

    ok(f"Snapshot saved: {snapshot.relative_to(RLD_ROOT)}")
    return snapshot


def generate_genesis(opts: Options) -> None:
    header("Generate Genesis")
    write_state(phase="generate-genesis")
    anvil_proc: subprocess.Popen[str] | None = None
    try:
        anvil_proc = start_temporary_anvil(opts)
        rate_api_url = ensure_rates_api(opts)
        deploy_protocol_on_anvil(opts, rate_api_url)
        warm_contract_cache()
        dump_anvil_state()
        convert_state_to_genesis(opts)
        save_deployment_snapshot()
    except Exception:
        warn(f"Genesis generation failed; temporary Anvil log kept at {ANVIL_LOG}")
        write_state(phase="generate-genesis", error="genesis generation failed")
        raise
    finally:
        docker_compose(COMPOSE_ANVIL, "down", "-v", check=False)
        if anvil_proc is not None:
            kill_pid(anvil_proc.pid, "temporary Anvil")
        cleanup_tracked_anvil()
    snapshot = save_genesis_snapshot()
    artifacts = read_state().get("artifacts", {})
    artifacts["snapshot"] = str(snapshot.relative_to(RLD_ROOT))
    write_state(phase="generate-genesis", artifacts=artifacts, error="")


def prepare_genesis(opts: Options) -> None:
    if opts.skip_genesis or opts.from_snapshot or (GENESIS_FILE.exists() and not opts.fresh):
        restore_or_reuse_genesis(from_snapshot=opts.from_snapshot)
        return
    generate_genesis(opts)


def verify_reth_contracts() -> None:
    header("Verify Reth Contracts")
    deploy = json.loads(DEPLOY_JSON.read_text())
    found = False
    for key in ["rld_core", "ghost_router", "twap_engine", "twamm_hook"]:
        addr = deploy.get(key)
        if not addr or addr.lower() == ZERO_ADDRESS:
            continue
        if not has_code(addr):
            fail(f"Contract {key} has no code at {addr}")
        found = True
        ok(f"{key} code present: {addr}")
    if not found:
        fail("No protocol contract addresses found in deployment.json")
    for addr in V4_DEPENDENCIES:
        if not has_code(addr):
            fail(f"Missing V4 dependency code at {addr}; regenerate genesis")
    ok("Protocol and V4 dependency code verified")


def start_core(opts: Options) -> None:
    header("Start Core Services")
    if opts.fresh:
        run(["docker", "volume", "rm", volume_name("reth-datadir")], check=False, capture=True)
    docker_compose(COMPOSE_RETH, "up", "-d", "reth")
    for _ in range(60):
        if run(["cast", "block-number", "--rpc-url", RETH_RPC], capture=True, check=False).returncode == 0:
            ok(f"Reth RPC ready at block {cast_block_number(RETH_RPC)}")
            break
        time.sleep(1)
    else:
        docker_compose(COMPOSE_RETH, "logs", "reth", "--tail", "40", check=False)
        fail("Reth RPC did not become ready")
    verify_reth_contracts()

    up_args = ["up", "-d"]
    if not opts.no_build:
        up_args.append("--build")
    up_args += ["postgres", "indexer"]
    docker_compose(COMPOSE_RETH, *up_args)
    wait_container("postgres", timeout_seconds=90)
    wait_container("indexer", timeout_seconds=120)
    write_state(phase="start")


def seed_indexer(opts: Options) -> None:
    header("Seed Indexer")
    indexer_url = f"http://localhost:{opts.env.get('INDEXER_PORT', '8080')}"
    headers = {}
    if opts.env.get("INDEXER_ADMIN_TOKEN"):
        headers["X-Admin-Token"] = opts.env["INDEXER_ADMIN_TOKEN"]
    last_status = 0
    for i in range(1, 31):
        last_status = http_status(f"{indexer_url}/admin/reset", method="POST", timeout=5, headers=headers)
        if last_status == 200:
            ok("Indexer DB seeded via /admin/reset")
            break
        if i % 10 == 0:
            info(f"Indexer reset returned {last_status}; retrying")
        time.sleep(2)
    if last_status != 200:
        docker_compose(COMPOSE_RETH, "logs", "indexer", "--tail", "60", check=False)
        fail(f"Indexer reset failed after retries (status={last_status})")
    wait_http(f"{indexer_url}/config", timeout_seconds=30)
    seed_initial_pool_state()
    write_state(phase="seed-indexer")


def first_int(value: str) -> int:
    return int(value.split()[0], 0)


def seed_initial_pool_state() -> None:
    header("Seed Initial Pool State")
    deploy = json.loads(DEPLOY_JSON.read_text())
    market_id = deploy.get("market_id")
    pool_id = deploy.get("pool_id")
    wausdc = deploy.get("wausdc")
    pos_token = deploy.get("position_token")
    if not all([market_id, pool_id, wausdc, pos_token]):
        warn("Deployment missing pool fields; skipping pool state seed")
        return
    try:
        wausdc_bal = first_int(cast_call("call", wausdc, "balanceOf(address)(uint256)", POOL_MANAGER))
        pos_bal = first_int(cast_call("call", pos_token, "balanceOf(address)(uint256)", POOL_MANAGER))
        wausdc_lower = wausdc.lower()
        pos_lower = pos_token.lower()
        token0_bal, token1_bal = (wausdc_bal, pos_bal) if wausdc_lower < pos_lower else (pos_bal, wausdc_bal)
        slot0_key = run(["cast", "keccak", run(["cast", "abi-encode", "f(bytes32,uint256)", pool_id, "6"], capture=True).stdout.strip()], capture=True).stdout.strip()
        slot0_raw = cast_call("call", POOL_MANAGER, "extsload(bytes32)(bytes32)", slot0_key)
        liq_slot = hex(int(slot0_key, 16) + 3)
        liq_raw = cast_call("call", POOL_MANAGER, "extsload(bytes32)(bytes32)", liq_slot)
        slot0 = int(slot0_raw, 16)
        liquidity = int(liq_raw, 16)
        sqrt_price = slot0 & ((1 << 160) - 1)
        tick_raw = (slot0 >> 160) & ((1 << 24) - 1)
        tick = tick_raw if tick_raw < (1 << 23) else tick_raw - (1 << 24)
        raw_price = (sqrt_price / (2**96)) ** 2
        mark = (1.0 / raw_price) if wausdc_lower < pos_lower and raw_price > 0 else raw_price
        pg_cid = compose_cid("postgres")
        if not pg_cid:
            fail("Postgres container not found for pool state seed")
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
              '0', '0',
              1.0, 0)
            ON CONFLICT (market_id, block_number) DO UPDATE SET
              sqrt_price_x96 = EXCLUDED.sqrt_price_x96,
              tick = EXCLUDED.tick,
              mark_price = EXCLUDED.mark_price,
              liquidity = EXCLUDED.liquidity,
              token0_balance = EXCLUDED.token0_balance,
              token1_balance = EXCLUDED.token1_balance,
              normalization_factor = EXCLUDED.normalization_factor;
        """
        run(["docker", "exec", pg_cid, "psql", "-U", "rld", "-d", "rld_indexer", "-c", sql], capture=True)
        ok(f"Pool state seeded: token0={token0_bal} token1={token1_bal} tick={tick} mark={mark}")
    except Exception as exc:
        warn(f"Could not seed on-chain pool state; TVL may be inaccurate: {exc}")


def verify_core(opts: Options) -> None:
    header("Verify Core")
    indexer_url = f"http://localhost:{opts.env.get('INDEXER_PORT', '8080')}"
    run(
        [
            "python3",
            str(SCRIPT_DIR / "verify_protocol_e2e.py"),
            "--mode",
            "core",
            "--rpc-url",
            RETH_RPC,
            "--indexer-url",
            indexer_url,
            "--deployment-json",
            str(DEPLOY_JSON),
            "--out",
            str(STATE_DIR / "protocol-e2e-core-report.json"),
        ],
        timeout=120,
    )
    write_state(phase="verify-core")


def seed_users(opts: Options, *, sim_funder_only: bool = False) -> None:
    header("Seed Users")
    env = merged_env({"RPC_URL": RETH_RPC}, base=opts.env)
    cmd = ["python3", str(SCRIPT_DIR / "setup_simulation.py")]
    if sim_funder_only:
        cmd += ["--sim-funder-only", "--prime-wausdc-reserve"]
    run(cmd, env=env, timeout=900, retries=1, retry_predicate=retryable_output)
    write_state(phase="seed-users")


def start_runtime(opts: Options, *, with_bots: bool) -> None:
    header("Start Runtime Services")
    up_args = ["up", "-d"]
    if not opts.no_build:
        up_args.append("--build")
    if with_bots:
        try:
            ensure_rates_api(opts, max_attempts=30)
        except SimctlError as exc:
            write_state(phase="start-bots", runtime="degraded", error=str(exc))
            raise
        up_args += ["mm-daemon", "chaos-trader", "faucet"]
    else:
        up_args += ["faucet"]

    try:
        docker_compose(COMPOSE_RETH, *up_args)
        wait_container("faucet", timeout_seconds=90)
        if with_bots:
            wait_container("mm-daemon", timeout_seconds=90)
            wait_container("chaos-trader", health=None, timeout_seconds=90)
        write_state(phase="start-bots" if with_bots else "start-faucet", runtime="healthy" if with_bots else "faucet-only", error="")
    except Exception as exc:
        write_state(phase="start-bots", runtime="degraded", error=str(exc))
        raise


def verify_runtime(opts: Options) -> None:
    header("Verify Runtime")
    wait_container("faucet", timeout_seconds=10)
    mm = service_status("mm-daemon")
    chaos = service_status("chaos-trader")
    if mm["state"] != "running" or mm["health"] not in (None, "healthy"):
        fail(f"mm-daemon is not healthy: state={mm['state']} health={mm['health']}")
    if chaos["state"] != "running":
        fail(f"chaos-trader is not running: state={chaos['state']}")

    indexer_url = f"http://localhost:{opts.env.get('INDEXER_PORT', '8080')}"
    run(
        [
            "python3",
            str(SCRIPT_DIR / "verify_protocol_e2e.py"),
            "--mode",
            "runtime",
            "--rpc-url",
            RETH_RPC,
            "--indexer-url",
            indexer_url,
            "--deployment-json",
            str(DEPLOY_JSON),
            "--out",
            str(STATE_DIR / "protocol-e2e-runtime-report.json"),
        ],
        timeout=120,
    )
    write_state(phase="running", runtime="healthy")


def smoke_indexer(opts: Options) -> None:
    header("Smoke Simulation Indexer")
    env = merged_env(
        {
            "INDEXER_URL": f"http://localhost:{opts.env.get('INDEXER_PORT', '8080')}",
            "SIM_REQUIRED_MARKET_TYPES": opts.env.get("SIM_REQUIRED_MARKET_TYPES", ""),
        },
        base=opts.env,
    )
    run(["python3", str(RLD_ROOT / "backend" / "indexers" / "scripts" / "smoke_sim_indexer.py")], env=env, timeout=60)


def status_payload() -> dict[str, Any]:
    env = load_env_file()
    services = collect_services(compact=False)
    indexer_port = env.get("INDEXER_PORT", "8080")
    rates_port = env.get("RATES_API_PORT") or env.get("ENVIO_API_PORT") or env.get("ENVIO_PORT") or "5000"
    endpoints = {
        "reth_rpc": run(["cast", "block-number", "--rpc-url", RETH_RPC], capture=True, check=False).returncode == 0,
        "indexer_health": http_status(f"http://localhost:{indexer_port}/healthz", timeout=2) == 200,
        "rates_ready": http_status(f"http://localhost:{rates_port}/readyz", timeout=2) == 200,
    }
    core_healthy = all(
        services[name]["state"] == "running" and services[name]["health"] in (None, "healthy")
        for name in ["reth", "postgres", "indexer"]
    ) and endpoints["reth_rpc"] and endpoints["indexer_health"]
    bots_expected = services["mm_daemon"]["state"] != "missing" or services["chaos_trader"]["state"] != "missing"
    runtime_healthy = (
        services["faucet"]["state"] == "running"
        and services["faucet"]["health"] in (None, "healthy")
        and (not bots_expected or (
            services["mm_daemon"]["state"] == "running"
            and services["mm_daemon"]["health"] in (None, "healthy")
            and services["chaos_trader"]["state"] == "running"
        ))
    )
    state = read_state()
    return {
        "ok": core_healthy,
        "phase": state.get("phase", "unknown"),
        "core": "healthy" if core_healthy else "degraded",
        "runtime": "healthy" if runtime_healthy else ("degraded" if bots_expected else "stopped"),
        "services": services,
        "endpoints": endpoints,
        "artifacts": state.get("artifacts", {}),
        "updated_at": state.get("updated_at"),
        "error": state.get("error", ""),
    }


def print_status(*, as_json: bool) -> None:
    payload = status_payload()
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    log(json.dumps(payload, indent=2))


def restart(args: argparse.Namespace) -> None:
    opts = build_options(args)
    if opts.with_bots and not getattr(args, "with_users", False):
        info("--with-bots implies --with-users")
    skip_genesis = opts.skip_genesis or opts.from_snapshot or (GENESIS_FILE.exists() and not opts.fresh)
    opts.skip_genesis = skip_genesis
    lock = acquire_lock()
    try:
        write_state(
            phase="starting",
            fresh=opts.fresh,
            with_users=opts.with_users,
            with_bots=opts.with_bots,
            started_at=now_iso(),
            error="",
        )
        preflight(opts, need_genesis=not skip_genesis)
        teardown(fresh=opts.fresh)
        prepare_genesis(opts)
        start_core(opts)
        seed_indexer(opts)
        if not opts.skip_e2e:
            verify_core(opts)
        if opts.with_users:
            seed_users(opts)
        else:
            seed_users(opts, sim_funder_only=True)
        start_runtime(opts, with_bots=opts.with_bots)
        if opts.with_bots and not opts.skip_e2e:
            verify_runtime(opts)
        else:
            write_state(phase="running")
        ok("Simulation restart complete")
    except Exception as exc:
        write_state(error=str(exc))
        raise
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-build", action="store_true", help="Skip Docker image rebuilds")
    parser.add_argument("--fresh", action="store_true", help="Wipe Reth/Postgres volumes and regenerate genesis")
    parser.add_argument("--with-users", action="store_true", help="Provision LP/MM/CHAOS users")
    parser.add_argument("--with-bots", action="store_true", help="Start mm-daemon and chaos-trader")
    parser.add_argument("--skip-e2e", action="store_true", help="Skip protocol verification")
    parser.add_argument("--skip-genesis", action="store_true", help="Reuse existing genesis/deployment snapshot")
    parser.add_argument("--from-snapshot", action="store_true", help="Restore latest genesis snapshot")


def main() -> None:
    parser = argparse.ArgumentParser(description="RLD Reth simulation controller")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser("preflight")
    pre.add_argument("--need-genesis", action="store_true")

    gen = sub.add_parser("generate-genesis")
    add_common_flags(gen)

    start = sub.add_parser("start")
    add_common_flags(start)

    seed_idx = sub.add_parser("seed-indexer")
    add_common_flags(seed_idx)

    seed_usr = sub.add_parser("seed-users")
    add_common_flags(seed_usr)
    seed_usr.add_argument("--sim-funder-only", action="store_true")

    bots = sub.add_parser("start-bots")
    add_common_flags(bots)

    sub.add_parser("verify-core")
    sub.add_parser("verify-runtime")
    sub.add_parser("smoke")

    restart_parser = sub.add_parser("restart")
    add_common_flags(restart_parser)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    args = parser.parse_args()
    try:
        if args.command == "preflight":
            opts = build_options(args)
            preflight(opts, need_genesis=args.need_genesis)
        elif args.command == "generate-genesis":
            opts = build_options(args)
            preflight(opts, need_genesis=True)
            generate_genesis(opts)
        elif args.command == "start":
            opts = build_options(args)
            preflight(opts)
            prepare_genesis(opts)
            start_core(opts)
        elif args.command == "seed-indexer":
            opts = build_options(args)
            seed_indexer(opts)
        elif args.command == "seed-users":
            opts = build_options(args)
            seed_users(opts, sim_funder_only=args.sim_funder_only)
        elif args.command == "start-bots":
            opts = build_options(args)
            start_runtime(opts, with_bots=True)
        elif args.command == "verify-core":
            opts = Options(env=load_env_file())
            verify_core(opts)
        elif args.command == "verify-runtime":
            opts = Options(env=load_env_file())
            verify_runtime(opts)
        elif args.command == "smoke":
            opts = Options(env=load_env_file())
            smoke_indexer(opts)
        elif args.command == "restart":
            restart(args)
        elif args.command == "status":
            print_status(as_json=args.json)
    except SimctlError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
