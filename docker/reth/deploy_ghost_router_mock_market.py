#!/usr/bin/env python3
"""
deploy_ghost_router_mock_market.py
==================================

Deploy a GhostRouter-only simulation market over a vanilla Uniswap V4 pool:
  1. Deploy two MockERC20 tokens.
  2. Sort currencies into canonical V4 order.
  3. Initialize a hookless V4 pool at a target price (default 5:1 token1/token0).
  4. Deploy GhostRouter.
  5. Initialize the GhostRouter market with Uniswap spot oracle mode.
  6. Verify each step on-chain.

This script does NOT deploy RLDCore/Factory; it only validates GhostRouter + V4 vanilla market plumbing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from decimal import Decimal, ROUND_FLOOR, getcontext
from pathlib import Path
from typing import Any, Sequence

from eth_abi import encode as abi_encode
from eth_account import Account
from web3 import Web3


SCRIPT_DIR = Path(__file__).resolve().parent
RLD_ROOT = SCRIPT_DIR.parent.parent
CONTRACTS_DIR = RLD_ROOT / "contracts"
DOCKER_ENV = SCRIPT_DIR.parent / ".env"

DEFAULT_RPC_URL = "http://localhost:8545"
DEFAULT_POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Keep enough precision for sqrt(price) * 2^96 math.
getcontext().prec = 100


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
        "type": "function",
        "name": "extsload",
        "stateMutability": "view",
        "inputs": [{"name": "slot", "type": "bytes32"}],
        "outputs": [{"name": "value", "type": "bytes32"}],
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


GHOST_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "initializeMarketWithUniswapOracle",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "vanillaKey",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            }
        ],
        "outputs": [{"name": "marketId", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "getSpotPrice",
        "stateMutability": "view",
        "inputs": [{"name": "marketId", "type": "bytes32"}],
        "outputs": [{"name": "price", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "markets",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [
            {"name": "token0", "type": "address"},
            {"name": "token1", "type": "address"},
            {"name": "oracle", "type": "address"},
            {"name": "oracleMode", "type": "uint8"},
            {
                "name": "vanillaKey",
                "type": "tuple",
                "components": [
                    {"name": "currency0", "type": "address"},
                    {"name": "currency1", "type": "address"},
                    {"name": "fee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                    {"name": "hooks", "type": "address"},
                ],
            },
        ],
    },
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


def normalize_private_key(value: str | None) -> str:
    if not value:
        die("Private key is required (use --private-key, DEPLOYER_KEY env, or docker/.env DEPLOYER_KEY).")
    key = value.strip()
    if not key.startswith("0x"):
        key = f"0x{key}"
    if len(key) != 66:
        die("Private key must be 32 bytes (64 hex chars).")
    return key


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if proc.returncode != 0:
        joined = " ".join(cmd)
        tail = (proc.stderr or proc.stdout)[-1000:]
        die(f"Command failed: {joined}\n{tail}")
    return f"{proc.stdout}\n{proc.stderr}".strip()


def deploy_contract_with_forge(contract: str, private_key: str, rpc_url: str, constructor_args: Sequence[Any]) -> str:
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

    output = run_cmd(cmd, cwd=CONTRACTS_DIR)
    match = re.search(r"Deployed to:\s*(0x[a-fA-F0-9]{40})", output)
    if not match:
        die(f"Could not parse deployed address for {contract}. Output tail:\n{output[-1200:]}")
    return Web3.to_checksum_address(match.group(1))


def has_code(w3: Web3, addr: str) -> bool:
    return len(w3.eth.get_code(Web3.to_checksum_address(addr))) > 0


def parse_ratio(ratio_text: str) -> tuple[Decimal, int, int]:
    if ":" not in ratio_text:
        die("Price ratio must be in 'A:B' format, e.g. 5:1.")
    left, right = ratio_text.split(":", 1)
    try:
        num = int(left.strip())
        den = int(right.strip())
    except ValueError as exc:
        die(f"Invalid ratio '{ratio_text}': {exc}")
    if num <= 0 or den <= 0:
        die("Ratio parts must be positive integers.")
    return Decimal(num) / Decimal(den), num, den


def sqrt_price_x96_from_ratio(token1_per_token0: Decimal) -> int:
    scaled = token1_per_token0.sqrt() * Decimal(2**96)
    return int(scaled.to_integral_value(rounding=ROUND_FLOOR))


def spot_wad_from_sqrt_price_x96(sqrt_price_x96: int) -> int:
    # price(token1/token0) scaled by 1e18
    return (sqrt_price_x96 * sqrt_price_x96 * 10**18) // (1 << 192)


def send_contract_tx(w3: Web3, sender_key: str, function_call: Any, label: str, gas_cap: int = 2_000_000) -> Any:
    account = Account.from_key(sender_key)
    sender = account.address
    nonce = w3.eth.get_transaction_count(sender, "pending")
    gas_price = max(w3.eth.gas_price, Web3.to_wei(2, "gwei"))

    try:
        estimated = function_call.estimate_gas({"from": sender})
        gas_limit = min(gas_cap, max(estimated + (estimated // 5), 120_000))
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
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        die(f"{label} reverted (tx={tx_hash.hex()})")
    ok(f"{label}: tx={tx_hash.hex()} gas={receipt.gasUsed:,}")
    return receipt


def ensure_checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy GhostRouter + vanilla V4 pool with two MockERC20 tokens and verify setup."
    )
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument("--private-key", default=None, help="Deployer private key (falls back to DEPLOYER_KEY).")
    parser.add_argument("--pool-manager", default=DEFAULT_POOL_MANAGER)
    parser.add_argument("--fee", type=int, default=3000)
    parser.add_argument("--tick-spacing", type=int, default=60)
    parser.add_argument("--price", default="5:1", help="token1:token0 price ratio (default: 5:1).")
    parser.add_argument("--decimals", type=int, default=18)
    parser.add_argument("--token-a-name", default="Ghost Mock Token A")
    parser.add_argument("--token-a-symbol", default="gMOCKA")
    parser.add_argument("--token-b-name", default="Ghost Mock Token B")
    parser.add_argument("--token-b-symbol", default="gMOCKB")
    parser.add_argument(
        "--out",
        default=str(SCRIPT_DIR / "ghost-router-mock-market.json"),
        help="Output JSON report path.",
    )
    args = parser.parse_args()

    if not (0 <= args.fee <= 1_000_000):
        die("--fee must be in [0, 1_000_000].")
    if args.tick_spacing == 0:
        die("--tick-spacing must be non-zero.")
    if not (0 <= args.decimals <= 255):
        die("--decimals must fit in uint8.")

    raw_key = args.private_key or os.environ.get("DEPLOYER_KEY") or read_key_from_env_file(DOCKER_ENV, "DEPLOYER_KEY")
    deployer_key = normalize_private_key(raw_key)
    deployer = Account.from_key(deployer_key).address

    pool_manager_addr = ensure_checksum(args.pool_manager)

    step("Step 0: Preflight")
    info(f"RPC: {args.rpc_url}")
    info(f"Deployer: {deployer}")
    info(f"PoolManager: {pool_manager_addr}")

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC endpoint: {args.rpc_url}")
    ok(f"Connected. chainId={w3.eth.chain_id}, latestBlock={w3.eth.block_number}")

    if not has_code(w3, pool_manager_addr):
        die(f"No code at pool manager {pool_manager_addr}.")
    ok("PoolManager has deployed code.")

    eth_balance = w3.eth.get_balance(deployer)
    if eth_balance == 0:
        die(f"Deployer {deployer} has 0 ETH.")
    ok(f"Deployer ETH balance: {w3.from_wei(eth_balance, 'ether')} ETH")

    step("Step 1: Deploy two MockERC20 tokens")
    token_a = deploy_contract_with_forge(
        "test/dex/mocks/MockERC20.sol:MockERC20",
        deployer_key,
        args.rpc_url,
        [args.token_a_name, args.token_a_symbol, args.decimals],
    )
    token_b = deploy_contract_with_forge(
        "test/dex/mocks/MockERC20.sol:MockERC20",
        deployer_key,
        args.rpc_url,
        [args.token_b_name, args.token_b_symbol, args.decimals],
    )
    if not has_code(w3, token_a) or not has_code(w3, token_b):
        die("Token deployment verification failed (missing runtime bytecode).")
    ok(f"Token A deployed: {token_a}")
    ok(f"Token B deployed: {token_b}")

    step("Step 2: Compute canonical pool key and 5:1 price init")
    token0, token1 = sorted([token_a, token_b], key=lambda x: int(x, 16))
    if token0 == token1:
        die("Token addresses unexpectedly identical.")
    ok(f"Canonical ordering: token0={token0}, token1={token1}")

    ratio, ratio_num, ratio_den = parse_ratio(args.price)
    sqrt_price_x96 = sqrt_price_x96_from_ratio(ratio)
    expected_spot_wad = spot_wad_from_sqrt_price_x96(sqrt_price_x96)
    target_spot_wad = int((ratio * Decimal(10**18)).to_integral_value(rounding=ROUND_FLOOR))
    ratio_delta = target_spot_wad - expected_spot_wad
    if abs(ratio_delta) > 10:
        die(
            f"Computed sqrtPriceX96 is not close enough to requested ratio. "
            f"target={target_spot_wad}, encoded={expected_spot_wad}, delta={ratio_delta}"
        )
    ok(
        f"Price target {ratio_num}:{ratio_den} => sqrtPriceX96={sqrt_price_x96}, "
        f"encoded spot={expected_spot_wad} (delta={ratio_delta})"
    )

    pool_key = {
        "currency0": token0,
        "currency1": token1,
        "fee": args.fee,
        "tickSpacing": args.tick_spacing,
        "hooks": ZERO_ADDRESS,
    }
    # PoolId = keccak256(abi.encode(PoolKey)).
    # Use full ABI encoding (not abi.encodePacked).
    pool_id_bytes = Web3.keccak(
        abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [token0, token1, args.fee, args.tick_spacing, ZERO_ADDRESS],
        )
    )
    pool_id_hex = Web3.to_hex(pool_id_bytes)
    ok(f"Computed poolId: {pool_id_hex}")

    step("Step 3: Initialize hookless vanilla Uniswap V4 pool")
    pool_manager = w3.eth.contract(address=pool_manager_addr, abi=POOL_MANAGER_ABI)
    init_receipt = send_contract_tx(
        w3,
        deployer_key,
        pool_manager.functions.initialize(pool_key, sqrt_price_x96),
        label="PoolManager.initialize",
    )

    initialize_events = pool_manager.events.Initialize().process_receipt(init_receipt)
    if len(initialize_events) != 1:
        die(f"Expected 1 Initialize event, got {len(initialize_events)}")
    init_args = initialize_events[0]["args"]

    event_pool_id = bytes(init_args["id"])
    event_currency0 = ensure_checksum(init_args["currency0"])
    event_currency1 = ensure_checksum(init_args["currency1"])
    event_hooks = ensure_checksum(init_args["hooks"])
    event_fee = int(init_args["fee"])
    event_tick_spacing = int(init_args["tickSpacing"])
    event_sqrt = int(init_args["sqrtPriceX96"])
    event_tick = int(init_args["tick"])
    slot0_spot = spot_wad_from_sqrt_price_x96(event_sqrt)

    if event_pool_id != pool_id_bytes:
        die(f"Initialize event poolId mismatch: expected {pool_id_hex}, got {Web3.to_hex(event_pool_id)}")
    if event_currency0 != token0 or event_currency1 != token1:
        die("Initialize event currency ordering mismatch.")
    if event_hooks != ensure_checksum(ZERO_ADDRESS):
        die("Initialize event hooks is not zero-address (pool must be vanilla/hookless).")
    if event_fee != args.fee or event_tick_spacing != args.tick_spacing:
        die("Initialize event fee/tickSpacing mismatch.")
    if event_sqrt != sqrt_price_x96:
        die(f"Initialize event sqrtPrice mismatch: expected {sqrt_price_x96}, got {event_sqrt}")
    if slot0_spot != expected_spot_wad:
        die(f"Initialize event price mismatch: expected {expected_spot_wad}, got {slot0_spot}")

    ok(f"Initialize event verified: poolId={pool_id_hex}, tick={event_tick}, sqrtPriceX96={event_sqrt}")
    ok(f"Initialize event spot(token1/token0, 1e18) verified: {slot0_spot}")

    step("Step 4: Deploy GhostRouter")
    ghost_router = deploy_contract_with_forge(
        "src/dex/GhostRouter.sol:GhostRouter",
        deployer_key,
        args.rpc_url,
        [pool_manager_addr, deployer],
    )
    if not has_code(w3, ghost_router):
        die("GhostRouter deployment verification failed (no code).")
    ok(f"GhostRouter deployed: {ghost_router}")

    step("Step 5: Initialize GhostRouter market with Uniswap oracle")
    router = w3.eth.contract(address=ghost_router, abi=GHOST_ROUTER_ABI)
    preview_market_id = router.functions.initializeMarketWithUniswapOracle(pool_key).call({"from": deployer})
    if preview_market_id != pool_id_bytes:
        die(
            f"MarketId precheck mismatch: expected {pool_id_hex}, "
            f"preview={Web3.to_hex(preview_market_id)}"
        )
    ok(f"initializeMarket precheck marketId matches poolId: {pool_id_hex}")

    send_contract_tx(
        w3,
        deployer_key,
        router.functions.initializeMarketWithUniswapOracle(pool_key),
        label="GhostRouter.initializeMarketWithUniswapOracle",
    )

    market = router.functions.markets(pool_id_bytes).call()
    market_token0 = ensure_checksum(market[0])
    market_token1 = ensure_checksum(market[1])
    market_oracle = ensure_checksum(market[2])
    oracle_mode = int(market[3])
    vanilla = market[4]

    if market_token0 != token0 or market_token1 != token1:
        die(
            "Router market token ordering mismatch: "
            f"expected ({token0}, {token1}), got ({market_token0}, {market_token1})"
        )
    if ensure_checksum(vanilla[0]) != token0 or ensure_checksum(vanilla[1]) != token1:
        die("Router vanillaKey currency ordering mismatch.")
    if int(vanilla[2]) != args.fee or int(vanilla[3]) != args.tick_spacing:
        die("Router vanillaKey fee/tickSpacing mismatch.")
    if ensure_checksum(vanilla[4]) != ensure_checksum(ZERO_ADDRESS):
        die("Router vanillaKey hooks is not zero-address (pool is not hookless).")
    if oracle_mode != 1:
        die(f"Oracle mode mismatch. Expected UniswapV4Spot(1), got {oracle_mode}")
    if market_oracle != ensure_checksum(ZERO_ADDRESS):
        die(f"Expected zero external oracle in Uniswap mode, got {market_oracle}")
    ok("GhostRouter market struct verified (tokens, hookless key, oracle mode).")

    router_spot = int(router.functions.getSpotPrice(pool_id_bytes).call())
    if abs(router_spot - target_spot_wad) > 10:
        die(
            f"GhostRouter spot price mismatch vs target ratio. "
            f"target={target_spot_wad}, router={router_spot}"
        )
    ok(f"GhostRouter spot price verified: {router_spot} (target {target_spot_wad})")

    report = {
        "rpc_url": args.rpc_url,
        "chain_id": w3.eth.chain_id,
        "deployer": deployer,
        "pool_manager": pool_manager_addr,
        "ghost_router": ghost_router,
        "token_a": token_a,
        "token_b": token_b,
        "token0": token0,
        "token1": token1,
        "price_ratio_token1_per_token0": f"{ratio_num}:{ratio_den}",
        "sqrt_price_x96": str(sqrt_price_x96),
        "target_spot_wad": str(target_spot_wad),
        "slot0_spot_wad": str(slot0_spot),
        "initialize_tick": event_tick,
        "router_spot_wad": str(router_spot),
        "pool_key": pool_key,
        "pool_id": pool_id_hex,
        "market_id": pool_id_hex,
    }

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    step("Done")
    ok("GhostRouter-only deployment verification complete.")
    ok(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()

