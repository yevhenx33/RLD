#!/usr/bin/env python3
"""
verify_protocol_e2e.py
======================

Read-only end-to-end verification for full protocol deployment.

Core mode checks:
  1) On-chain contracts from deployment.json are present.
  2) poolId derivation matches token ordering + fee/tickSpacing.
  3) Oracle -> pool init price wiring is correct:
       indexPrice = MockOracle.getIndexPrice(AAVE_POOL, USDC)
       expectedSpot = indexPrice (or inverted if positionToken is token1)
       GhostRouter.getSpotPrice(poolId) ~= expectedSpot
  4) Indexer /config and /api/market-info expose matching market addresses.

Runtime mode keeps the contract/indexer wiring checks, but only requires the
current spot to be positive because bots can legitimately move it after start.
It also checks indexed runtime state and pool liquidity/balances.
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


def find_positive_number(payload: Any, keys: set[str]) -> float | None:
    """Recursively find a positive numeric field in a JSON payload."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = key.replace("-", "_")
            if normalized in keys:
                try:
                    parsed = float(value)
                except (TypeError, ValueError):
                    parsed = 0
                if parsed > 0:
                    return parsed
            found = find_positive_number(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_positive_number(item, keys)
            if found is not None:
                return found
    return None


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


def deployment_market_entries(deploy: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    markets = deploy.get("markets")
    if isinstance(markets, dict) and markets:
        return [
            (str(key), value)
            for key, value in markets.items()
            if isinstance(value, dict) and value.get("market_id")
        ]
    return [("perp", deploy)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify full protocol deployment correctness end-to-end.")
    parser.add_argument(
        "--mode",
        choices=("core", "runtime"),
        default="core",
        help="core checks deployment invariants; runtime checks post-bot health without initial spot equality.",
    )
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
    info(f"Mode: {args.mode}")
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
    if args.mode == "core" and delta > tolerance:
        die(
            "spot mismatch vs expected oracle-derived pool price: "
            f"spot={spot_price}, expected={expected_pool_price}, delta={delta}, tolerance={tolerance}"
        )
    if args.mode == "core":
        ok(f"spot price verified: spot={spot_price}, expected={expected_pool_price}, delta={delta}")
    else:
        if spot_price <= 0:
            die(f"runtime spot price is non-positive: {spot_price}")
        ok(
            "runtime spot price is live; strict initial deployment equality skipped "
            f"(spot={spot_price}, initial_expected={expected_pool_price}, delta={delta})"
        )

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

    runtime_report: dict[str, Any] = {}
    if args.mode == "runtime":
        step("Step 4: Verify runtime indexer state")
        try:
            status_payload = fetch_json(f"{indexer_base}/api/status")
        except urllib.error.URLError as exc:
            die(f"Indexer /api/status fetch failed: {exc}")
        if not isinstance(status_payload, dict):
            die(f"Unexpected /api/status payload type: {type(status_payload)}")
        status_markets = status_payload.get("markets") if isinstance(status_payload.get("markets"), list) else []
        status_by_id = {
            str(m.get("marketId") or m.get("market_id")): m
            for m in status_markets
            if isinstance(m, dict) and (m.get("marketId") or m.get("market_id"))
        }
        for expected_type, entry in deployment_market_entries(deploy):
            expected_market_id = str(entry.get("market_id") or "")
            if expected_market_id and expected_market_id not in status_by_id:
                die(f"Expected {expected_type} market missing from /api/status: {expected_market_id}")
            if expected_market_id:
                status_type = str(status_by_id[expected_market_id].get("marketType") or status_by_id[expected_market_id].get("market_type") or "")
                if status_type and status_type != expected_type:
                    die(f"Market type mismatch for {expected_market_id}: {status_type} != {expected_type}")
        ok(f"Indexer status includes expected markets: {', '.join(k for k, _ in deployment_market_entries(deploy))}")

        last_indexed_block = int(status_payload.get("last_indexed_block") or 0)
        current_block = int(w3.eth.block_number)
        if last_indexed_block > current_block:
            die(
                "indexer cursor is ahead of the local Reth chain: "
                f"last_indexed_block={last_indexed_block}, chain_block={current_block}"
            )
        ok(f"Indexer cursor is on local chain: last_indexed_block={last_indexed_block}, chain_block={current_block}")

        total_block_states = int(status_payload.get("total_block_states") or 0)
        total_events = int(status_payload.get("total_events") or 0)
        if total_block_states <= 0:
            die(f"runtime indexer has no block_states rows: {total_block_states}")
        ok(f"Indexer block_states present: {total_block_states}")

        if total_events <= 0:
            try:
                events_payload = fetch_json(f"{indexer_base}/api/events?limit=1")
            except urllib.error.URLError as exc:
                die(f"Indexer /api/events fetch failed: {exc}")
            events = events_payload.get("events", []) if isinstance(events_payload, dict) else []
            if not events:
                info("Runtime indexer has no indexed events yet; block state is present, continuing")
            else:
                total_events = len(events)
                ok(f"Indexer events present: {total_events}")
        else:
            ok(f"Indexer events present: {total_events}")

        perp_status = None
        for market_status in status_markets:
            if isinstance(market_status, dict) and market_status.get("marketType") == "perp":
                perp_status = market_status
                break
        if perp_status:
            route_anomalies = int(perp_status.get("routeAnomalies") or 0)
            if route_anomalies:
                die(f"perp route anomalies detected: {route_anomalies}")
            if int(perp_status.get("totalEvents") or 0) > 0:
                if int(perp_status.get("swapCount") or 0) <= 0:
                    die("perp events are present but swapCount is zero")
                if int(perp_status.get("candleRows") or 0) <= 0:
                    die("perp events are present but candleRows is zero")
                ok(
                    "Perp aggregates present: "
                    f"swaps={perp_status.get('swapCount')} candles={perp_status.get('candleRows')}"
                )

        for expected_type, entry in deployment_market_entries(deploy):
            if expected_type != "cds":
                continue
            market_status = status_by_id.get(str(entry.get("market_id")))
            if not market_status:
                continue
            collateral = str(entry.get("collateral_token") or entry.get("wausdc") or "").lower()
            usdc = str(entry.get("underlying_token") or deploy.get("external_contracts", {}).get("usdc") or USDC).lower()
            if collateral and usdc and collateral != usdc:
                die(f"CDS collateral is not raw USDC: {collateral} != {usdc}")
            for key in ("funding_model", "settlement_module"):
                if not entry.get(key):
                    die(f"CDS market missing {key}")
            ok(f"CDS market metadata verified: {entry.get('market_id')}")

        try:
            latest_payload = fetch_json(f"{indexer_base}/api/latest")
        except urllib.error.URLError as exc:
            die(f"Indexer /api/latest fetch failed: {exc}")
        pool_value = find_positive_number(
            latest_payload,
            {"liquidity", "token0_balance", "token1_balance", "tvl", "tvl_usd", "total_value_locked"},
        )
        if pool_value is None:
            pool_value = find_positive_number(status_payload, {"mark_price", "index_price"})
            if pool_value is None:
                die("runtime pool state has no positive liquidity/balance/TVL or price field")
            info("Runtime latest snapshot has no pool balances yet; positive price state is present")
        ok(f"Runtime pool state is non-zero: {pool_value}")
        runtime_report = {
            "total_block_states": total_block_states,
            "total_events": total_events,
            "last_indexed_block": last_indexed_block,
            "chain_block": current_block,
            "pool_value_check": pool_value,
        }

    report = {
        "mode": args.mode,
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
        "runtime": runtime_report,
    }

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    step("Done")
    ok(f"Protocol {args.mode} verification passed.")
    ok(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
