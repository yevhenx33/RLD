#!/usr/bin/env python3
"""
verify_cds_market_live.py
=========================

Phase 9 verification for a live CDS market on the running Reth simulation.

Default behavior is read-only. It verifies:
  - deployment.json contains `markets.cds`
  - Core registered the CDS market
  - Core stored raw USDC collateral, CDS funding model, non-zero decay F,
    and CDS settlement proxy
  - Funding model projects non-increasing, non-zero NF
  - Settlement proxy is wired to the same Core
  - Ghost/V4 pool id and spot price match oracle initialization
  - Optional indexer endpoints surface `?market=cds`

It does not deploy contracts, apply funding, create brokers, or enter settlement.
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
DEFAULT_OUT = SCRIPT_DIR / "cds-market-verification-report.json"

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


CORE_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "isValidMarket",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "getMarketAddresses",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "collateralToken", "type": "address"},
                    {"name": "underlyingToken", "type": "address"},
                    {"name": "underlyingPool", "type": "address"},
                    {"name": "rateOracle", "type": "address"},
                    {"name": "spotOracle", "type": "address"},
                    {"name": "markOracle", "type": "address"},
                    {"name": "fundingModel", "type": "address"},
                    {"name": "curator", "type": "address"},
                    {"name": "liquidationModule", "type": "address"},
                    {"name": "positionToken", "type": "address"},
                    {"name": "settlementModule", "type": "address"},
                ],
            }
        ],
    },
    {
        "type": "function",
        "name": "getMarketConfig",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "minColRatio", "type": "uint64"},
                    {"name": "maintenanceMargin", "type": "uint64"},
                    {"name": "liquidationCloseFactor", "type": "uint64"},
                    {"name": "fundingPeriod", "type": "uint32"},
                    {"name": "badDebtPeriod", "type": "uint32"},
                    {"name": "debtCap", "type": "uint128"},
                    {"name": "minLiquidation", "type": "uint128"},
                    {"name": "liquidationParams", "type": "bytes32"},
                    {"name": "decayRateWad", "type": "uint96"},
                    {"name": "brokerVerifier", "type": "address"},
                ],
            }
        ],
    },
    {
        "type": "function",
        "name": "getMarketState",
        "stateMutability": "view",
        "inputs": [{"name": "id", "type": "bytes32"}],
        "outputs": [
            {
                "name": "",
                "type": "tuple",
                "components": [
                    {"name": "normalizationFactor", "type": "uint128"},
                    {"name": "totalDebt", "type": "uint128"},
                    {"name": "lastUpdateTimestamp", "type": "uint48"},
                    {"name": "globalSettlementTimestamp", "type": "uint48"},
                    {"name": "badDebt", "type": "uint128"},
                ],
            }
        ],
    },
]

FUNDING_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "calculateFunding",
        "stateMutability": "view",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "core", "type": "address"},
            {"name": "currentNormalizationFactor", "type": "uint256"},
            {"name": "lastUpdateTimestamp", "type": "uint48"},
        ],
        "outputs": [
            {"name": "newNormalizationFactor", "type": "uint256"},
            {"name": "fundingRate", "type": "int256"},
        ],
    }
]

SETTLEMENT_PROXY_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "core",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "owner",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "MIN_SETTLEMENT_TRACKS",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
    {
        "type": "function",
        "name": "SUPPORTED_TRACK_MASK",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint8"}],
    },
]

ORACLE_ABI: list[dict[str, Any]] = [
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


def market_id_bytes(market_id: str) -> bytes:
    normalized = market_id[2:] if market_id.startswith("0x") else market_id
    if len(normalized) != 64:
        die(f"invalid market id: {market_id}")
    return bytes.fromhex(normalized)


def compute_pool_id(token0: str, token1: str, fee: int, tick_spacing: int) -> bytes:
    return Web3.keccak(
        abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [checksum(token0), checksum(token1), fee, tick_spacing, checksum(ZERO_ADDRESS)],
        )
    )


def fetch_json(url: str, timeout: int = 5) -> dict[str, Any] | list[Any]:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def verify_equal(name: str, actual: str, expected: str) -> None:
    if checksum(actual).lower() != checksum(expected).lower():
        die(f"{name} mismatch: actual={actual}, expected={expected}")
    ok(f"{name}: {checksum(actual)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify live CDS market wiring.")
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument("--indexer-url", default=os.environ.get("INDEXER_URL", DEFAULT_INDEXER_URL))
    parser.add_argument("--deployment-json", default=str(DEFAULT_DEPLOYMENT_JSON))
    parser.add_argument("--market-key", default="cds")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--skip-indexer", action="store_true")
    parser.add_argument("--allow-missing-cds", action="store_true")
    args = parser.parse_args()

    deployment_path = Path(args.deployment_json)
    if not deployment_path.exists():
        die(f"deployment.json not found: {deployment_path}")
    deploy = json.loads(deployment_path.read_text())

    markets = deploy.get("markets")
    if not isinstance(markets, dict) or args.market_key not in markets:
        message = f"deployment.json has no markets.{args.market_key}; deploy CDS before live verification"
        if args.allow_missing_cds:
            info(message)
            return
        die(message)

    cds = markets[args.market_key]
    market_id = str(cds["market_id"])
    market_bytes = market_id_bytes(market_id)

    step("Preflight")
    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC: {args.rpc_url}")
    ok(f"Connected chainId={w3.eth.chain_id} block={w3.eth.block_number}")

    core_addr = checksum(deploy["rld_core"])
    ghost_addr = checksum(deploy["ghost_router"])
    required_contracts = {
        "RLDCore": core_addr,
        "GhostRouter": ghost_addr,
        "CDS funding model": cds["funding_model"],
        "CDS settlement module": cds["settlement_module"],
        "CDS position token": cds["position_token"],
        "CDS broker factory": cds["broker_factory"],
        "CDS collateral": cds["collateral_token"],
        "CDS rate oracle": cds["rate_oracle"],
    }
    for label, addr in required_contracts.items():
        if not has_code(w3, addr):
            die(f"{label} has no code at {addr}")
        ok(f"{label} code present: {checksum(addr)}")

    core = w3.eth.contract(address=core_addr, abi=CORE_ABI)
    ghost = w3.eth.contract(address=ghost_addr, abi=GHOST_ROUTER_ABI)
    funding = w3.eth.contract(address=checksum(cds["funding_model"]), abi=FUNDING_ABI)
    settlement = w3.eth.contract(address=checksum(cds["settlement_module"]), abi=SETTLEMENT_PROXY_ABI)
    oracle = w3.eth.contract(address=checksum(cds["rate_oracle"]), abi=ORACLE_ABI)

    step("Core market wiring")
    if not core.functions.isValidMarket(market_bytes).call():
        die(f"Core does not recognize CDS market: {market_id}")
    ok("Core recognizes CDS market")

    addresses = core.functions.getMarketAddresses(market_bytes).call()
    config = core.functions.getMarketConfig(market_bytes).call()
    state = core.functions.getMarketState(market_bytes).call()

    verify_equal("collateral token", addresses[0], cds["collateral_token"])
    verify_equal("underlying token", addresses[1], cds["underlying_token"])
    verify_equal("underlying pool", addresses[2], cds["underlying_pool"])
    verify_equal("rate oracle", addresses[3], cds["rate_oracle"])
    verify_equal("funding model", addresses[6], cds["funding_model"])
    verify_equal("position token", addresses[9], cds["position_token"])
    verify_equal("settlement module", addresses[10], cds["settlement_module"])
    verify_equal("raw USDC collateral", addresses[0], USDC)

    decay_rate = int(config[8])
    expected_decay = int(cds["decay_rate_wad"])
    if decay_rate != expected_decay:
        die(f"decayRateWad mismatch: core={decay_rate}, config={expected_decay}")
    if decay_rate <= 0:
        die("decayRateWad must be non-zero for CDS")
    ok(f"decayRateWad verified: {decay_rate}")

    if int(state[3]) != 0:
        die(f"market already in global settlement: timestamp={state[3]}")
    ok("global settlement inactive")

    step("Funding model projection")
    projected_nf, funding_rate = funding.functions.calculateFunding(
        market_bytes,
        core_addr,
        int(state[0]),
        int(state[2]),
    ).call()
    if projected_nf <= 0:
        die(f"funding projection produced non-positive NF: {projected_nf}")
    if projected_nf > int(state[0]):
        die(f"CDS NF should not increase: projected={projected_nf}, current={state[0]}")
    if int(funding_rate) != decay_rate and int(state[2]) != w3.eth.get_block("latest")["timestamp"]:
        die(f"funding rate mismatch: {funding_rate} != {decay_rate}")
    ok(f"funding projection valid: currentNF={state[0]} projectedNF={projected_nf}")

    step("Settlement proxy wiring")
    verify_equal("settlement proxy core", settlement.functions.core().call(), core_addr)
    owner = checksum(settlement.functions.owner().call())
    if owner.lower() == ZERO_ADDRESS.lower():
        die("settlement proxy owner is zero")
    ok(f"settlement proxy owner: {owner}")
    if int(settlement.functions.MIN_SETTLEMENT_TRACKS().call()) != 2:
        die("settlement proxy must require 2 settlement tracks")
    if int(settlement.functions.SUPPORTED_TRACK_MASK().call()) != 7:
        die("settlement proxy supported track mask must be 0b111")
    ok("settlement proxy track policy verified")

    step("Pool and oracle wiring")
    token0 = checksum(cds["token0"])
    token1 = checksum(cds["token1"])
    pool_id_bytes = compute_pool_id(token0, token1, int(cds["pool_fee"]), int(cds["tick_spacing"]))
    pool_id = Web3.to_hex(pool_id_bytes)
    if pool_id.lower() != str(cds["pool_id"]).lower():
        die(f"pool id mismatch: derived={pool_id}, config={cds['pool_id']}")
    ok(f"pool id verified: {pool_id}")

    index_price = int(
        oracle.functions.getIndexPrice(checksum(cds["underlying_pool"]), checksum(cds["underlying_token"])).call()
    )
    if index_price <= 0:
        die(f"oracle index price must be positive: {index_price}")
    expected_spot = index_price
    if checksum(cds["position_token"]).lower() == token1.lower():
        expected_spot = (10**36) // index_price
    spot = int(ghost.functions.getSpotPrice(pool_id_bytes).call())
    tolerance = max(expected_spot // 1_000_000_000, 1_000)
    delta = abs(spot - expected_spot)
    if delta > tolerance:
        die(f"Ghost spot mismatch: spot={spot}, expected={expected_spot}, delta={delta}")
    ok(f"Ghost spot verified: spot={spot}, expected={expected_spot}, delta={delta}")

    indexer_report: dict[str, Any] = {}
    if not args.skip_indexer:
        step("Indexer market selector")
        base = args.indexer_url.rstrip("/")
        try:
            config_payload = fetch_json(f"{base}/config?market={args.market_key}")
            market_payload = fetch_json(f"{base}/api/market-info?market={args.market_key}")
            if not isinstance(config_payload, dict) or not isinstance(market_payload, dict):
                die("indexer returned unexpected payload types")
            config_market_id = str(config_payload.get("market_id") or config_payload.get("marketId") or "")
            market_obj = market_payload.get("market", market_payload)
            info_market_id = str(market_obj.get("market_id") or market_obj.get("marketId") or "")
            if config_market_id.lower() != market_id.lower():
                die(f"/config?market={args.market_key} mismatch: {config_market_id}")
            if info_market_id.lower() != market_id.lower():
                die(f"/api/market-info?market={args.market_key} mismatch: {info_market_id}")
            ok("Indexer CDS selector verified")
            indexer_report = {
                "config_market_id": config_market_id,
                "market_info_market_id": info_market_id,
            }
        except urllib.error.URLError as exc:
            die(f"Indexer CDS selector check failed: {exc}")

    report = {
        "rpc_url": args.rpc_url,
        "indexer_url": args.indexer_url,
        "chain_id": w3.eth.chain_id,
        "block_number": w3.eth.block_number,
        "market_id": market_id,
        "core": core_addr,
        "funding_model": checksum(cds["funding_model"]),
        "settlement_module": checksum(cds["settlement_module"]),
        "collateral_token": checksum(cds["collateral_token"]),
        "position_token": checksum(cds["position_token"]),
        "pool_id": pool_id,
        "oracle_index_price_wad": str(index_price),
        "expected_spot_wad": str(expected_spot),
        "ghost_spot_wad": str(spot),
        "spot_delta": str(delta),
        "current_nf": str(state[0]),
        "projected_nf": str(projected_nf),
        "decay_rate_wad": str(decay_rate),
        "settlement_owner": owner,
        "indexer": indexer_report,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    step("Done")
    ok("CDS market live verification passed")
    ok(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
