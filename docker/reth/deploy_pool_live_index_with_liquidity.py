#!/usr/bin/env python3
"""
deploy_pool_live_index_with_liquidity.py
========================================

Isolated verification script for the new hookless V4 pool path:
  1. Fetch current USDC borrow rate from Envio/data-pipeline.
  2. Convert to oracle semantics via P = K * r (K=100), then to ray for Mock oracle.
  3. Deploy a fresh MockRLDAaveOracle and two MockERC20 tokens.
  4. Initialize a hookless Uniswap V4 pool at the derived initial price.
  5. Seed liquidity via PoolModifyLiquidityTest helper (direct manager.unlock path).
  6. Verify pool state and PoolManager token balances.

This script is intentionally isolated: it does not deploy RLDCore/Factory/GhostRouter.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from decimal import Decimal, getcontext
from pathlib import Path
from typing import Any, Sequence

from eth_abi import encode as abi_encode
from eth_account import Account
from web3 import Web3


SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent
RLD_ROOT = DOCKER_DIR.parent
CONTRACTS_DIR = RLD_ROOT / "contracts"
BACKEND_DIR = RLD_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from rates_client import fetch_valid_rate_sample, policy_from_env

DEFAULT_ENV_FILE = DOCKER_DIR / ".env"
DEFAULT_DEPLOYMENT_JSON = DOCKER_DIR / "deployment.json"

DEFAULT_RPC_URL = "http://localhost:8545"
DEFAULT_API_URL = "http://127.0.0.1:5000"
DEFAULT_POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"

AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
MIN_TICK = -887272
MAX_TICK = 887272

# Keep high precision for decimal logging and conversions.
getcontext().prec = 100


MOCK_ORACLE_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "setRate",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "newRateRay", "type": "uint256"}],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "getIndexPrice",
        "stateMutability": "view",
        "inputs": [
            {"name": "", "type": "address"},
            {"name": "", "type": "address"},
        ],
        "outputs": [{"name": "indexPrice", "type": "uint256"}],
    },
]

MOCK_ERC20_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "mint",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

POOL_MANAGER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "initialize",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "key",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {"name": "sqrtPriceX96", "type": "uint160"},
        ],
        "outputs": [{"name": "tick", "type": "int24"}],
    },
    {
        "type": "event",
        "name": "Initialize",
        "anonymous": False,
        "inputs": [
            {"name": "id", "type": "bytes32", "indexed": True},
            {"name": "currency0", "type": "address", "indexed": True},
            {"name": "currency1", "type": "address", "indexed": True},
            {"name": "fee", "type": "uint24", "indexed": False},
            {"name": "tickSpacing", "type": "int24", "indexed": False},
            {"name": "hooks", "type": "address", "indexed": False},
            {"name": "sqrtPriceX96", "type": "uint160", "indexed": False},
            {"name": "tick", "type": "int24", "indexed": False},
        ],
    },
]

POOL_MODIFY_HELPER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "modifyLiquidity",
        "stateMutability": "payable",
        "inputs": [
            {
                "name": "key",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "tickLower", "type": "int24"},
                    {"name": "tickUpper", "type": "int24"},
                    {"name": "liquidityDelta", "type": "int256"},
                    {"name": "salt", "type": "bytes32"},
                ],
            },
            {"name": "hookData", "type": "bytes"},
        ],
        "outputs": [{"name": "delta", "type": "int256"}],
    }
]


def die(msg: str) -> None:
    print(f"[ERR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def step(title: str) -> None:
    print(f"\n== {title}")


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def info(msg: str) -> None:
    print(f"[..] {msg}")


def normalize_private_key(value: str | None) -> str:
    if not value:
        die("Private key is required (use --private-key, DEPLOYER_KEY, or docker/.env DEPLOYER_KEY).")
    key = value.strip()
    if not key.startswith("0x"):
        key = f"0x{key}"
    if len(key) != 66:
        die("Private key must be 32 bytes (64 hex chars).")
    return key


def read_key_from_env_file(path: Path, key_name: str) -> str | None:
    if not path.exists():
        return None
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == key_name:
            return value.strip().strip('"').strip("'")
    return None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def ensure_checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def has_code(w3: Web3, addr: str) -> bool:
    return len(w3.eth.get_code(ensure_checksum(addr))) > 0


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if proc.returncode != 0:
        joined = " ".join(cmd)
        tail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()[-1200:]
        die(f"Command failed: {joined}\n{tail}")
    return ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()


def deploy_contract_with_forge(
    contract: str, private_key: str, rpc_url: str, constructor_args: Sequence[Any]
) -> str:
    cmd = [
        "forge",
        "create",
        contract,
        "--private-key",
        private_key,
        "--rpc-url",
        rpc_url,
        "--broadcast",
        "--legacy",
    ]
    if constructor_args:
        cmd.extend(["--constructor-args", *[str(x) for x in constructor_args]])

    last_output = ""
    for attempt in range(1, 6):
        output = run_cmd(cmd, cwd=CONTRACTS_DIR)
        last_output = output
        match = re.search(r"Deployed to:\s*(0x[a-fA-F0-9]{40})", output)
        if match:
            return ensure_checksum(match.group(1))

        if "replacement transaction underpriced" in output.lower() or "nonce too low" in output.lower():
            info(f"{contract} transient deploy issue (attempt {attempt}/5), retrying...")
            time.sleep(1)
            continue
        die(f"Could not parse deployment address for {contract}. Output tail:\n{output[-1200:]}")

    die(f"forge create failed after retries for {contract}\n{last_output[-1200:]}")
    return ZERO_ADDRESS


def send_contract_tx(
    w3: Web3,
    sender_key: str,
    function_call: Any,
    label: str,
    gas_cap: int = 3_000_000,
) -> Any:
    account = Account.from_key(sender_key)
    sender = account.address
    nonce = w3.eth.get_transaction_count(sender, "pending")
    gas_price = max(w3.eth.gas_price, Web3.to_wei(2, "gwei"))

    try:
        estimated = function_call.estimate_gas({"from": sender})
        gas_limit = min(gas_cap, max(estimated + (estimated // 5), 150_000))
    except Exception:
        gas_limit = gas_cap

    tx = function_call.build_transaction(
        {
            "from": sender,
            "nonce": nonce,
            "chainId": w3.eth.chain_id,
            "gas": gas_limit,
            "gasPrice": gas_price,
        }
    )
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        die(f"{label} reverted (tx={tx_hash.hex()})")
    ok(f"{label}: tx={tx_hash.hex()} gas={receipt.gasUsed:,}")
    return receipt


def fetch_live_rate_fraction(api_url: str) -> Decimal:
    sample = fetch_valid_rate_sample(
        api_url,
        timeout_seconds=float(os.environ.get("RATES_TIMEOUT", "4")),
        policy=policy_from_env(),
    )
    if sample:
        info(
            f"Fetched live rate from {sample.endpoint}: "
            f"r={sample.rate_fraction} (~{(sample.rate_fraction * 100):.6f}%)"
        )
        return sample.rate_fraction

    die("Could not fetch live rate from any configured endpoint")
    return Decimal(0)


def rate_fraction_to_ray(rate_fraction: Decimal) -> int:
    return int(rate_fraction * Decimal(10**27))


def compute_pool_id(token0: str, token1: str, fee: int, tick_spacing: int, hooks: str) -> bytes:
    return Web3.keccak(
        abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [ensure_checksum(token0), ensure_checksum(token1), fee, tick_spacing, ensure_checksum(hooks)],
        )
    )


def sqrt_price_x96_from_wad_price(price_wad: int) -> int:
    # Mirrors factory math: sqrtPriceX96 = sqrt(priceWad) * 2^96 / 1e9
    return (math.isqrt(price_wad) * (1 << 96)) // 10**9


def spot_wad_from_sqrt_price_x96(sqrt_price_x96: int) -> int:
    return (sqrt_price_x96 * sqrt_price_x96 * 10**18) // (1 << 192)


def align_tick_down(value: int, spacing: int) -> int:
    return math.floor(value / spacing) * spacing


def align_tick_up(value: int, spacing: int) -> int:
    return math.ceil(value / spacing) * spacing


def compute_default_tick_range(spacing: int) -> tuple[int, int]:
    lower = align_tick_up(MIN_TICK, spacing)
    upper = align_tick_down(MAX_TICK, spacing)
    if lower >= upper:
        die(f"Invalid default tick range computed for spacing={spacing}: [{lower}, {upper}]")
    return lower, upper


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy and verify a hookless V4 pool at live index price, then seed liquidity."
    )
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument(
        "--api-url",
        default=(
            os.environ.get("RATES_API_BASE_URL")
            or os.environ.get("ENVIO_API_URL")
            or os.environ.get("API_URL")
            or os.environ.get("RATES_API_URL")
            or DEFAULT_API_URL
        ),
    )
    parser.add_argument("--private-key", default=None)
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--deployment-json", default=str(DEFAULT_DEPLOYMENT_JSON))
    parser.add_argument("--pool-manager", default=None)
    parser.add_argument("--fee", type=int, default=500)
    parser.add_argument("--tick-spacing", type=int, default=5)
    parser.add_argument("--decimals", type=int, default=6)
    parser.add_argument("--position-name", default="Pool Verify Position")
    parser.add_argument("--position-symbol", default="pVERIFY")
    parser.add_argument("--collateral-name", default="Pool Verify Collateral")
    parser.add_argument("--collateral-symbol", default="cVERIFY")
    parser.add_argument("--seed-position-units", type=int, default=5_000_000)
    parser.add_argument("--seed-collateral-units", type=int, default=5_000_000)
    parser.add_argument(
        "--liquidity-delta",
        type=int,
        default=1_000_000,
        help="Positive liquidity delta passed to PoolModifyLiquidityTest.modifyLiquidity.",
    )
    parser.add_argument("--tick-lower", type=int, default=None)
    parser.add_argument("--tick-upper", type=int, default=None)
    parser.add_argument("--out", default=str(SCRIPT_DIR / "pool-live-index-report.json"))
    args = parser.parse_args()

    if not (0 <= args.fee <= 1_000_000):
        die("--fee must be in [0, 1_000_000]")
    if args.tick_spacing == 0:
        die("--tick-spacing must be non-zero")
    if not (0 <= args.decimals <= 255):
        die("--decimals must fit uint8")
    if args.liquidity_delta <= 0:
        die("--liquidity-delta must be > 0")

    env_file = Path(args.env_file)
    deployment_path = Path(args.deployment_json)
    deployment = load_json(deployment_path)

    raw_key = (
        args.private_key
        or os.environ.get("DEPLOYER_KEY")
        or read_key_from_env_file(env_file, "DEPLOYER_KEY")
    )
    deployer_key = normalize_private_key(raw_key)
    deployer = Account.from_key(deployer_key).address

    pool_manager_addr = ensure_checksum(
        args.pool_manager
        or deployment.get("pool_manager")
        or deployment.get("v4_pool_manager")
        or DEFAULT_POOL_MANAGER
    )

    step("Step 0: Preflight")
    info(f"RPC URL: {args.rpc_url}")
    info(f"Preferred API URL: {args.api_url}")
    info(f"Deployer: {deployer}")
    info(f"PoolManager: {pool_manager_addr}")
    info("Liquidity seeding route: PoolModifyLiquidityTest helper")

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC endpoint: {args.rpc_url}")
    ok(f"Connected. chainId={w3.eth.chain_id} block={w3.eth.block_number}")

    if not has_code(w3, pool_manager_addr):
        die(f"PoolManager has no code at {pool_manager_addr}")
    ok("PoolManager code present")

    deployer_eth = w3.eth.get_balance(deployer)
    if deployer_eth == 0:
        die("Deployer has 0 ETH")
    ok(f"Deployer ETH: {w3.from_wei(deployer_eth, 'ether')} ETH")

    step("Step 1: Fetch live rate and derive initial index price")
    rate_fraction = fetch_live_rate_fraction(args.api_url)
    rate_ray = rate_fraction_to_ray(rate_fraction)
    expected_index_price_wad = int((rate_fraction * Decimal(100) * Decimal(10**18)).to_integral_value())
    ok(
        f"Live rate r={rate_fraction} -> ray={rate_ray} -> expected P=100*r={Decimal(expected_index_price_wad) / Decimal(10**18)}"
    )

    step("Step 2: Deploy isolated mock oracle and set live rate")
    mock_oracle_addr = deploy_contract_with_forge(
        "src/rld/modules/oracles/MockRLDAaveOracle.sol:MockRLDAaveOracle",
        deployer_key,
        args.rpc_url,
        [],
    )
    mock_oracle = w3.eth.contract(address=mock_oracle_addr, abi=MOCK_ORACLE_ABI)
    send_contract_tx(
        w3,
        deployer_key,
        mock_oracle.functions.setRate(rate_ray),
        "MockOracle.setRate(live)",
        gas_cap=300_000,
    )
    oracle_index_price_wad = int(
        mock_oracle.functions.getIndexPrice(ensure_checksum(AAVE_POOL), ensure_checksum(USDC)).call()
    )
    if abs(oracle_index_price_wad - expected_index_price_wad) > 1:
        die(
            f"Oracle index mismatch: expected {expected_index_price_wad}, got {oracle_index_price_wad}"
        )
    ok(f"Oracle getIndexPrice verified: {oracle_index_price_wad} (WAD)")

    step("Step 3: Deploy two mock tokens (position + collateral)")
    position_token = deploy_contract_with_forge(
        "test/dex/mocks/MockERC20.sol:MockERC20",
        deployer_key,
        args.rpc_url,
        [args.position_name, args.position_symbol, args.decimals],
    )
    collateral_token = deploy_contract_with_forge(
        "test/dex/mocks/MockERC20.sol:MockERC20",
        deployer_key,
        args.rpc_url,
        [args.collateral_name, args.collateral_symbol, args.decimals],
    )
    ok(f"Position token: {position_token}")
    ok(f"Collateral token: {collateral_token}")

    if position_token == collateral_token:
        die("Token deployment returned identical addresses")

    step("Step 4: Build pool key and derive init sqrtPriceX96")
    currency0, currency1 = sorted([position_token, collateral_token], key=lambda x: int(x, 16))
    index_price_for_pool_wad = oracle_index_price_wad

    # Factory semantics:
    # indexPrice is collateral per position.
    # If position token is currency1, pool stores token1/token0 so invert.
    if ensure_checksum(position_token).lower() == ensure_checksum(currency1).lower():
        index_price_for_pool_wad = (10**36) // index_price_for_pool_wad

    sqrt_price_x96 = sqrt_price_x96_from_wad_price(index_price_for_pool_wad)
    if sqrt_price_x96 <= 0:
        die("Computed sqrtPriceX96 is zero")

    encoded_spot_wad = spot_wad_from_sqrt_price_x96(sqrt_price_x96)
    pool_id_bytes = compute_pool_id(currency0, currency1, args.fee, args.tick_spacing, ZERO_ADDRESS)
    pool_id_hex = Web3.to_hex(pool_id_bytes)

    ok(f"currency0={currency0}")
    ok(f"currency1={currency1}")
    ok(f"poolId={pool_id_hex}")
    ok(f"indexPriceForPoolWad={index_price_for_pool_wad}")
    ok(f"sqrtPriceX96={sqrt_price_x96}")
    ok(f"encoded spot(token1/token0, 1e18)={encoded_spot_wad}")

    step("Step 5: Initialize hookless V4 pool")
    pool_manager = w3.eth.contract(address=pool_manager_addr, abi=POOL_MANAGER_ABI)
    pool_key_tuple = (currency0, currency1, args.fee, args.tick_spacing, ZERO_ADDRESS)
    init_receipt = send_contract_tx(
        w3,
        deployer_key,
        pool_manager.functions.initialize(pool_key_tuple, sqrt_price_x96),
        "PoolManager.initialize",
        gas_cap=2_500_000,
    )

    initialize_events = pool_manager.events.Initialize().process_receipt(init_receipt)
    if len(initialize_events) != 1:
        die(f"Expected exactly 1 Initialize event, got {len(initialize_events)}")
    init_args = initialize_events[0]["args"]

    event_pool_id = bytes(init_args["id"])
    event_currency0 = ensure_checksum(init_args["currency0"])
    event_currency1 = ensure_checksum(init_args["currency1"])
    event_hooks = ensure_checksum(init_args["hooks"])
    event_fee = int(init_args["fee"])
    event_tick_spacing = int(init_args["tickSpacing"])
    event_sqrt = int(init_args["sqrtPriceX96"])
    event_tick = int(init_args["tick"])

    if event_pool_id != pool_id_bytes:
        die(f"Initialize event poolId mismatch: {Web3.to_hex(event_pool_id)} != {pool_id_hex}")
    if event_currency0 != ensure_checksum(currency0) or event_currency1 != ensure_checksum(currency1):
        die("Initialize event currency ordering mismatch")
    if event_hooks != ensure_checksum(ZERO_ADDRESS):
        die("Pool hooks is non-zero (expected hookless)")
    if event_fee != args.fee or event_tick_spacing != args.tick_spacing:
        die("Initialize event fee/tickSpacing mismatch")
    if event_sqrt != sqrt_price_x96:
        die(f"Initialize sqrt mismatch: {event_sqrt} != {sqrt_price_x96}")

    ok(
        f"Initialize event verified: poolId={pool_id_hex}, tick={event_tick}, sqrtPriceX96={event_sqrt}"
    )

    step("Step 6: Deploy liquidity helper + mint/approve seed balances")
    liquidity_helper_addr = deploy_contract_with_forge(
        "lib/v4-core/src/test/PoolModifyLiquidityTest.sol:PoolModifyLiquidityTest",
        deployer_key,
        args.rpc_url,
        [pool_manager_addr],
    )
    liquidity_helper = w3.eth.contract(address=liquidity_helper_addr, abi=POOL_MODIFY_HELPER_ABI)
    ok(f"PoolModifyLiquidityTest helper: {liquidity_helper_addr}")

    pos_contract = w3.eth.contract(address=position_token, abi=MOCK_ERC20_ABI)
    col_contract = w3.eth.contract(address=collateral_token, abi=MOCK_ERC20_ABI)

    pos_seed_raw = args.seed_position_units * (10**args.decimals)
    col_seed_raw = args.seed_collateral_units * (10**args.decimals)
    if pos_seed_raw <= 0 or col_seed_raw <= 0:
        die("Seed amounts must be > 0")

    send_contract_tx(
        w3,
        deployer_key,
        pos_contract.functions.mint(deployer, pos_seed_raw),
        "PositionToken.mint",
        gas_cap=300_000,
    )
    send_contract_tx(
        w3,
        deployer_key,
        col_contract.functions.mint(deployer, col_seed_raw),
        "CollateralToken.mint",
        gas_cap=300_000,
    )

    max_u256 = 2**256 - 1
    for token_contract, label in [
        (pos_contract, "position"),
        (col_contract, "collateral"),
    ]:
        send_contract_tx(
            w3,
            deployer_key,
            token_contract.functions.approve(liquidity_helper_addr, max_u256),
            f"{label}.approve(liquidity helper)",
            gas_cap=200_000,
        )

    pos_bal = int(pos_contract.functions.balanceOf(deployer).call())
    col_bal = int(col_contract.functions.balanceOf(deployer).call())
    if pos_bal < pos_seed_raw or col_bal < col_seed_raw:
        die("Seed balances not minted as expected")
    ok(f"Deployer balances: position={pos_bal}, collateral={col_bal}")

    step("Step 7: Seed LP liquidity via PoolModifyLiquidityTest.modifyLiquidity")
    if args.tick_lower is not None or args.tick_upper is not None:
        if args.tick_lower is None or args.tick_upper is None:
            die("Provide both --tick-lower and --tick-upper, or neither.")
        tick_lower = align_tick_down(args.tick_lower, args.tick_spacing)
        tick_upper = align_tick_up(args.tick_upper, args.tick_spacing)
    else:
        tick_lower, tick_upper = compute_default_tick_range(args.tick_spacing)

    if tick_lower >= tick_upper:
        die(f"Invalid tick range after alignment: [{tick_lower}, {tick_upper}]")

    token0_contract = (
        pos_contract if ensure_checksum(position_token).lower() == ensure_checksum(currency0).lower() else col_contract
    )
    token1_contract = (
        col_contract if ensure_checksum(position_token).lower() == ensure_checksum(currency0).lower() else pos_contract
    )

    pool_manager_token0_before = int(token0_contract.functions.balanceOf(pool_manager_addr).call())
    pool_manager_token1_before = int(token1_contract.functions.balanceOf(pool_manager_addr).call())

    liquidity_params = (tick_lower, tick_upper, int(args.liquidity_delta), b"\x00" * 32)
    info(f"LP tick range: [{tick_lower}, {tick_upper}]")
    info(f"Requested liquidity delta: {args.liquidity_delta}")

    try:
        preview_delta = int(
            liquidity_helper.functions.modifyLiquidity(pool_key_tuple, liquidity_params, b"").call({"from": deployer})
        )
        ok(f"Preview modifyLiquidity delta (int256-packed): {preview_delta}")
    except Exception as exc:
        die(f"modifyLiquidity preview reverted before send: {exc}")

    send_contract_tx(
        w3,
        deployer_key,
        liquidity_helper.functions.modifyLiquidity(pool_key_tuple, liquidity_params, b""),
        "PoolModifyLiquidityTest.modifyLiquidity(seed LP)",
        gas_cap=3_000_000,
    )

    pool_manager_token0_after = int(token0_contract.functions.balanceOf(pool_manager_addr).call())
    pool_manager_token1_after = int(token1_contract.functions.balanceOf(pool_manager_addr).call())

    if pool_manager_token0_after <= pool_manager_token0_before and pool_manager_token1_after <= pool_manager_token1_before:
        die("PoolManager balances did not increase after liquidity seed")

    ok(
        f"LP seeded: liquidityDelta={args.liquidity_delta}, "
        f"pool token0={pool_manager_token0_after}, token1={pool_manager_token1_after}"
    )

    report = {
        "rpc_url": args.rpc_url,
        "api_url": args.api_url,
        "chain_id": w3.eth.chain_id,
        "block_number": w3.eth.block_number,
        "deployer": deployer,
        "pool_manager": pool_manager_addr,
        "liquidity_helper": liquidity_helper_addr,
        "mock_oracle": mock_oracle_addr,
        "position_token": position_token,
        "collateral_token": collateral_token,
        "currency0": currency0,
        "currency1": currency1,
        "fee": args.fee,
        "tick_spacing": args.tick_spacing,
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "pool_id": pool_id_hex,
        "rate_fraction_r": str(rate_fraction),
        "rate_percent": str(rate_fraction * Decimal(100)),
        "rate_ray": str(rate_ray),
        "oracle_index_price_wad": str(oracle_index_price_wad),
        "index_price_for_pool_wad": str(index_price_for_pool_wad),
        "sqrt_price_x96": str(sqrt_price_x96),
        "encoded_spot_wad_token1_per_token0": str(encoded_spot_wad),
        "initialize_tick": event_tick,
        "liquidity_delta_requested": str(args.liquidity_delta),
        "preview_balance_delta": str(preview_delta),
        "seed_position_raw": str(pos_seed_raw),
        "seed_collateral_raw": str(col_seed_raw),
        "pool_manager_token0_before": str(pool_manager_token0_before),
        "pool_manager_token0_after": str(pool_manager_token0_after),
        "pool_manager_token1_before": str(pool_manager_token1_before),
        "pool_manager_token1_after": str(pool_manager_token1_after),
    }

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    step("Done")
    ok("Isolated pool deployment + liquidity seed verification complete.")
    ok(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
