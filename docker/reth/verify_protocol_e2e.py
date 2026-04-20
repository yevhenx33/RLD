#!/usr/bin/env python3
"""
verify_protocol_e2e.py
======================

Read-only end-to-end verification for full protocol deployment.

Checks:
  1) On-chain contracts from deployment.json are present.
  2) poolId derivation matches token ordering + fee/tickSpacing.
  3) Oracle -> pool init price wiring is correct:
       indexPrice = MockOracle.getIndexPrice(AAVE_POOL, USDC)
       expectedSpot = indexPrice (or inverted if positionToken is token1)
       GhostRouter.getSpotPrice(poolId) ~= expectedSpot
  4) Indexer /config and /api/market-info expose matching market addresses.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from eth_abi import encode as abi_encode
from web3 import Web3


SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent

DEFAULT_RPC_URL = "http://localhost:8545"
DEFAULT_INDEXER_URL = "http://localhost:8080"
DEFAULT_DEPLOYMENT_JSON = DOCKER_DIR / "deployment.json"
DEFAULT_OUT = SCRIPT_DIR / "protocol-e2e-report.json"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


MOCK_ORACLE_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "getIndexPrice",
        "stateMutability": "view",
        "inputs": [
            {"name": "", "type": "address"},
            {"name": "", "type": "address"},
        ],
        "outputs": [{"name": "indexPrice", "type": "uint256"}],
    }
]

GHOST_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "getSpotPrice",
        "stateMutability": "view",
        "inputs": [{"name": "marketId", "type": "bytes32"}],
        "outputs": [{"name": "price", "type": "uint256"}],
    }
]


def die(msg: str) -> None:
    print(f"[ERR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def step(msg: str) -> None:
    print(f"\n== {msg}")


def ok(msg: str) -> None:
    print(f"[OK] {msg}")


def info(msg: str) -> None:
    print(f"[..] {msg}")


def checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def has_code(w3: Web3, addr: str) -> bool:
    return len(w3.eth.get_code(checksum(addr))) > 0


def fetch_json(url: str, timeout: int = 5) -> dict[str, Any] | list[Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_indexer_market(payload: dict[str, Any]) -> dict[str, Any]:
    # /api/market-info returns {"market": {...}} in current stack.
    if isinstance(payload.get("market"), dict):
        return payload["market"]
    # Backward-compatible: sometimes payload itself is the market object.
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify full protocol deployment correctness end-to-end.")
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument("--indexer-url", default=os.environ.get("INDEXER_URL", DEFAULT_INDEXER_URL))
    parser.add_argument("--deployment-json", default=str(DEFAULT_DEPLOYMENT_JSON))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    deployment_path = Path(args.deployment_json)
    if not deployment_path.exists():
        die(f"deployment.json not found: {deployment_path}")
    deploy = json.loads(deployment_path.read_text())

    step("Step 0: Preflight")
    info(f"RPC: {args.rpc_url}")
    info(f"Indexer: {args.indexer_url}")

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC: {args.rpc_url}")
    ok(f"Connected. chainId={w3.eth.chain_id}, block={w3.eth.block_number}")

    required_keys = [
        "rld_core",
        "ghost_router",
        "twap_engine",
        "mock_oracle",
        "wausdc",
        "position_token",
        "broker_factory",
        "pool_manager",
        "pool_id",
        "market_id",
    ]
    for key in required_keys:
        if not deploy.get(key):
            die(f"Missing required deployment key: {key}")

    for key in ["rld_core", "ghost_router", "twap_engine", "mock_oracle", "wausdc", "position_token", "broker_factory", "pool_manager"]:
        addr = checksum(deploy[key])
        if not has_code(w3, addr):
            die(f"{key} has no code: {addr}")
        ok(f"{key} code present: {addr}")

    step("Step 1: Verify poolId derivation")
    token0 = checksum(deploy["token0"])
    token1 = checksum(deploy["token1"])
    fee = int(deploy["pool_fee"])
    tick_spacing = int(deploy["tick_spacing"])

    derived_pool_id_bytes = Web3.keccak(
        abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [token0, token1, fee, tick_spacing, ZERO_ADDRESS],
        )
    )
    derived_pool_id = Web3.to_hex(derived_pool_id_bytes)
    stored_pool_id = str(deploy["pool_id"])
    if derived_pool_id.lower() != stored_pool_id.lower():
        die(f"poolId mismatch: derived={derived_pool_id}, deployment={stored_pool_id}")
    ok(f"poolId verified: {derived_pool_id}")

    step("Step 2: Verify oracle -> pool init price wiring")
    ghost_router = w3.eth.contract(address=checksum(deploy["ghost_router"]), abi=GHOST_ROUTER_ABI)
    mock_oracle = w3.eth.contract(address=checksum(deploy["mock_oracle"]), abi=MOCK_ORACLE_ABI)

    oracle_index_price = int(mock_oracle.functions.getIndexPrice(checksum(AAVE_POOL), checksum(USDC)).call())
    if oracle_index_price <= 0:
        die(f"Oracle index price is non-positive: {oracle_index_price}")

    position_token = checksum(deploy["position_token"])
    expected_pool_price = oracle_index_price
    if position_token.lower() == token1.lower():
        expected_pool_price = (10**36) // expected_pool_price

    spot_price = int(ghost_router.functions.getSpotPrice(derived_pool_id_bytes).call())
    tolerance = max(expected_pool_price // 1_000_000_000, 1_000)
    delta = abs(spot_price - expected_pool_price)
    if delta > tolerance:
        die(
            "spot mismatch vs expected oracle-derived pool price: "
            f"spot={spot_price}, expected={expected_pool_price}, delta={delta}, tolerance={tolerance}"
        )
    ok(f"spot price verified: spot={spot_price}, expected={expected_pool_price}, delta={delta}")

    step("Step 3: Verify indexer surfaces deployment")
    indexer_base = args.indexer_url.rstrip("/")
    try:
        config_payload = fetch_json(f"{indexer_base}/config")
    except urllib.error.URLError as exc:
        die(f"Indexer /config fetch failed: {exc}")

    if not isinstance(config_payload, dict):
        die(f"Unexpected /config payload type: {type(config_payload)}")

    config_market_id = str(config_payload.get("market_id") or config_payload.get("marketId") or "")
    if config_market_id.lower() != str(deploy["market_id"]).lower():
        die(f"/config market_id mismatch: {config_market_id} != {deploy['market_id']}")
    ok("/config market_id matches deployment")

    try:
        market_payload = fetch_json(f"{indexer_base}/api/market-info")
    except urllib.error.URLError as exc:
        die(f"Indexer /api/market-info fetch failed: {exc}")

    if not isinstance(market_payload, dict):
        die(f"Unexpected /api/market-info payload type: {type(market_payload)}")

    market = parse_indexer_market(market_payload)
    market_id_idx = str(market.get("marketId") or market.get("market_id") or "")
    ghost_router_idx = str(market.get("ghostRouter") or market.get("ghost_router") or "")
    if market_id_idx.lower() != str(deploy["market_id"]).lower():
        die(f"/api/market-info market_id mismatch: {market_id_idx} != {deploy['market_id']}")
    if ghost_router_idx and ghost_router_idx.lower() != str(deploy["ghost_router"]).lower():
        die(f"/api/market-info ghost_router mismatch: {ghost_router_idx} != {deploy['ghost_router']}")
    ok("/api/market-info wiring matches deployment")

    report = {
        "rpc_url": args.rpc_url,
        "indexer_url": args.indexer_url,
        "chain_id": w3.eth.chain_id,
        "block_number": w3.eth.block_number,
        "market_id": str(deploy["market_id"]),
        "pool_id": derived_pool_id,
        "token0": token0,
        "token1": token1,
        "pool_fee": fee,
        "tick_spacing": tick_spacing,
        "oracle_index_price_wad": str(oracle_index_price),
        "expected_pool_spot_wad": str(expected_pool_price),
        "router_spot_wad": str(spot_price),
        "spot_delta": str(delta),
        "spot_tolerance": str(tolerance),
        "indexer_config_market_id": config_market_id,
        "indexer_market_info_market_id": market_id_idx,
        "indexer_market_info_ghost_router": ghost_router_idx,
    }

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    step("Done")
    ok("Full protocol e2e verification passed.")
    ok(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
