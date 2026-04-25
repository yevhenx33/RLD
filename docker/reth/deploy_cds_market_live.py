#!/usr/bin/env python3
"""
deploy_cds_market_live.py
=========================

Add a CDS market to an already-running Reth simulation.

This script does not regenerate genesis and does not redeploy the core stack. It
reuses the live RLDCore/RLDMarketFactory/GhostRouter/TwapEngine deployment, then:

  1. Deploys CDSDecayFundingModel.
  2. Deploys CDSSettlementProxy.
  3. Creates a second RLD market with raw USDC collateral.
  4. Verifies Core, Factory, and GhostRouter wiring.
  5. Adds a `markets.cds` entry to docker/deployment.json while preserving the
     existing top-level perp-market keys.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from eth_abi import encode as abi_encode
from eth_account import Account
from web3 import Web3


SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent
RLD_ROOT = DOCKER_DIR.parent
CONTRACTS_DIR = RLD_ROOT / "contracts"
DEFAULT_ENV_FILE = DOCKER_DIR / ".env"
DEFAULT_DEPLOYMENT_JSON = DOCKER_DIR / "deployment.json"

DEFAULT_RPC_URL = "http://localhost:8545"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

# F = -ln(1 - 0.90), WAD scaled.
DEFAULT_DECAY_RATE_WAD = 2_302_585_092_994_045_684


FACTORY_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "owner",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "canonicalMarkets",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "createMarket",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {"name": "underlyingPool", "type": "address"},
                    {"name": "underlyingToken", "type": "address"},
                    {"name": "collateralToken", "type": "address"},
                    {"name": "curator", "type": "address"},
                    {"name": "positionTokenName", "type": "string"},
                    {"name": "positionTokenSymbol", "type": "string"},
                    {"name": "minColRatio", "type": "uint64"},
                    {"name": "maintenanceMargin", "type": "uint64"},
                    {"name": "liquidationCloseFactor", "type": "uint64"},
                    {"name": "liquidationModule", "type": "address"},
                    {"name": "fundingModel", "type": "address"},
                    {"name": "fundingPeriod", "type": "uint32"},
                    {"name": "decayRateWad", "type": "uint96"},
                    {"name": "settlementModule", "type": "address"},
                    {"name": "liquidationParams", "type": "bytes32"},
                    {"name": "spotOracle", "type": "address"},
                    {"name": "rateOracle", "type": "address"},
                    {"name": "oraclePeriod", "type": "uint32"},
                    {"name": "poolFee", "type": "uint24"},
                    {"name": "tickSpacing", "type": "int24"},
                ],
            }
        ],
        "outputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "brokerFactory", "type": "address"},
        ],
    },
]

CORE_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "factory",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
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
        "name": "isEngine",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "address"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "getSpotPrice",
        "stateMutability": "view",
        "inputs": [{"name": "marketId", "type": "bytes32"}],
        "outputs": [{"name": "price", "type": "uint256"}],
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
        die("Private key is required via --private-key, DEPLOYER_KEY, or docker/.env.")
    key = value.strip()
    if not key.startswith("0x"):
        key = f"0x{key}"
    if len(key) != 66:
        die("Private key must be 32 bytes (64 hex chars).")
    return key


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        die(f"deployment json not found: {path}")
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def has_code(w3: Web3, addr: str) -> bool:
    return len(w3.eth.get_code(checksum(addr))) > 0


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        die(f"Command failed: {' '.join(cmd)}\n{output[-1600:]}")
    return output


def deploy_contract_with_forge(
    contract: str,
    private_key: str,
    rpc_url: str,
    constructor_args: Sequence[Any],
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
        cmd.extend(["--constructor-args", *[str(arg) for arg in constructor_args]])

    last_output = ""
    for attempt in range(1, 4):
        output = run_cmd(cmd, cwd=CONTRACTS_DIR)
        last_output = output
        match = re.search(r"Deployed to:\s*(0x[a-fA-F0-9]{40})", output)
        if match:
            return checksum(match.group(1))
        info(f"Could not parse deployment address for {contract} (attempt {attempt}/3)")
        time.sleep(1)

    die(f"Could not parse deployment address for {contract}. Output tail:\n{last_output[-1600:]}")
    return ZERO_ADDRESS


def send_contract_tx(
    w3: Web3,
    private_key: str,
    function_call: Any,
    label: str,
    gas_cap: int,
) -> Any:
    account = Account.from_key(private_key)
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    gas_price = max(w3.eth.gas_price, Web3.to_wei(2, "gwei"))

    try:
        estimated = function_call.estimate_gas({"from": account.address})
        gas_limit = min(gas_cap, max(estimated + estimated // 4, 250_000))
    except Exception:
        gas_limit = gas_cap

    tx = function_call.build_transaction(
        {
            "from": account.address,
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
        die(f"{label} reverted: tx={tx_hash.hex()}")
    ok(f"{label}: tx={tx_hash.hex()} gas={receipt.gasUsed:,}")
    return receipt


def compute_market_id(collateral: str, underlying: str, pool: str) -> bytes:
    return Web3.keccak(
        abi_encode(
            ["address", "address", "address"],
            [checksum(collateral), checksum(underlying), checksum(pool)],
        )
    )


def compute_pool_id(token0: str, token1: str, fee: int, tick_spacing: int) -> bytes:
    return Web3.keccak(
        abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [checksum(token0), checksum(token1), fee, tick_spacing, checksum(ZERO_ADDRESS)],
        )
    )


def market_entry_from_legacy(deploy: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "perp",
        "market_id": deploy["market_id"],
        "pool_id": deploy["pool_id"],
        "collateral_token": deploy["wausdc"],
        "collateral_symbol": "waUSDC",
        "underlying_token": deploy["external_contracts"]["usdc"],
        "underlying_pool": deploy["external_contracts"]["aave_pool"],
        "position_token": deploy["position_token"],
        "position_symbol": "wRLP",
        "broker_factory": deploy["broker_factory"],
        "rate_oracle": deploy["mock_oracle"],
        "funding_model": None,
        "settlement_module": ZERO_ADDRESS,
        "token0": deploy["token0"],
        "token1": deploy["token1"],
        "pool_fee": deploy["pool_fee"],
        "tick_spacing": deploy["tick_spacing"],
        "zero_for_one_long": deploy.get("zero_for_one_long"),
        "deploy_block": deploy.get("deploy_block", 0),
        "session_start_block": deploy.get("session_start_block", 0),
        "min_col_ratio": "1200000000000000000",
        "maintenance_margin": "1090000000000000000",
        "liq_close_factor": "500000000000000000",
        "funding_period_sec": 2592000,
        "debt_cap": str(2**128 - 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy a CDS market to the running Reth node.")
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--deployment-json", default=str(DEFAULT_DEPLOYMENT_JSON))
    parser.add_argument("--private-key", default=None)
    parser.add_argument("--core", default=None)
    parser.add_argument("--factory", default=None)
    parser.add_argument("--owner", default=None, help="Owner for CDSSettlementProxy. Defaults to deployer.")
    parser.add_argument("--rate-oracle", default=None, help="Defaults to current mock_oracle from deployment.json.")
    parser.add_argument("--spot-oracle", default=ZERO_ADDRESS, help="Defaults to zero; Core treats raw USDC as 1:1.")
    parser.add_argument("--collateral", default=USDC)
    parser.add_argument("--underlying-token", default=USDC)
    parser.add_argument("--underlying-pool", default=AAVE_POOL)
    parser.add_argument("--position-name", default="Wrapped CDS RLP: USDC")
    parser.add_argument("--position-symbol", default="wCDSUSDC")
    parser.add_argument("--decay-rate-wad", type=int, default=DEFAULT_DECAY_RATE_WAD)
    parser.add_argument("--pool-fee", type=int, default=500)
    parser.add_argument("--tick-spacing", type=int, default=5)
    parser.add_argument("--oracle-period", type=int, default=3600)
    parser.add_argument("--funding-period", type=int, default=30 * 24 * 60 * 60)
    parser.add_argument("--min-col-ratio", type=int, default=1_200_000_000_000_000_000)
    parser.add_argument("--maintenance-margin", type=int, default=1_090_000_000_000_000_000)
    parser.add_argument("--liquidation-close-factor", type=int, default=500_000_000_000_000_000)
    parser.add_argument("--skip-write", action="store_true", help="Deploy and verify, but do not update deployment.json.")
    args = parser.parse_args()

    env_file = Path(args.env_file)
    deployment_path = Path(args.deployment_json)
    deploy = load_json(deployment_path)

    private_key = normalize_private_key(
        args.private_key or os.environ.get("DEPLOYER_KEY") or read_key_from_env_file(env_file, "DEPLOYER_KEY")
    )
    deployer = Account.from_key(private_key).address
    owner = checksum(args.owner or deployer)

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC: {args.rpc_url}")

    step("Preflight")
    info(f"RPC: {args.rpc_url}")
    info(f"Deployer: {deployer}")
    ok(f"Connected chainId={w3.eth.chain_id} block={w3.eth.block_number}")

    core_addr = checksum(args.core or deploy.get("rld_core", ""))
    if not has_code(w3, core_addr):
        die(f"RLDCore has no code: {core_addr}")

    core = w3.eth.contract(address=core_addr, abi=CORE_ABI)
    factory_addr = checksum(args.factory or core.functions.factory().call())
    if not has_code(w3, factory_addr):
        die(f"Factory has no code: {factory_addr}")
    factory = w3.eth.contract(address=factory_addr, abi=FACTORY_ABI)

    factory_owner = checksum(factory.functions.owner().call())
    if factory_owner.lower() != deployer.lower():
        die(f"DEPLOYER_KEY is not factory owner: deployer={deployer}, factory_owner={factory_owner}")
    ok(f"Factory owner verified: {factory_owner}")

    for key in ("ghost_router", "twap_engine", "pool_manager", "v4_position_manager", "permit2"):
        addr = deploy.get(key)
        if addr and not has_code(w3, addr):
            die(f"{key} has no code: {addr}")
    ok("Existing singleton code verified")

    ghost = w3.eth.contract(address=checksum(deploy["ghost_router"]), abi=GHOST_ROUTER_ABI)
    if not ghost.functions.isEngine(checksum(deploy["twap_engine"])).call():
        die("TwapEngine is not registered in GhostRouter")
    ok("GhostRouter/TwapEngine registration verified")

    candidate_market_id = compute_market_id(args.collateral, args.underlying_token, args.underlying_pool)
    candidate_market_id_hex = Web3.to_hex(candidate_market_id)
    if core.functions.isValidMarket(candidate_market_id).call():
        die(f"CDS market already exists in Core: {candidate_market_id_hex}")
    if int.from_bytes(factory.functions.canonicalMarkets(candidate_market_id).call(), "big") != 0:
        die(f"CDS market already exists in Factory canonical map: {candidate_market_id_hex}")
    ok(f"Candidate market id is free: {candidate_market_id_hex}")

    step("Deploy CDS modules")
    funding_model = deploy_contract_with_forge(
        "src/rld/modules/funding/CDSDecayFundingModel.sol:CDSDecayFundingModel",
        private_key,
        args.rpc_url,
        [],
    )
    ok(f"CDSDecayFundingModel deployed: {funding_model}")

    settlement_proxy = deploy_contract_with_forge(
        "src/rld/modules/settlement/CDSSettlementProxy.sol:CDSSettlementProxy",
        private_key,
        args.rpc_url,
        [core_addr, owner],
    )
    ok(f"CDSSettlementProxy deployed: {settlement_proxy}")

    step("Create CDS market")
    existing_addresses = core.functions.getMarketAddresses(bytes.fromhex(deploy["market_id"][2:])).call()
    existing_config = core.functions.getMarketConfig(bytes.fromhex(deploy["market_id"][2:])).call()
    liquidation_module = checksum(existing_addresses[8])
    liquidation_params = existing_config[7]
    rate_oracle = checksum(args.rate_oracle or deploy["mock_oracle"])
    spot_oracle = checksum(args.spot_oracle)
    oracle = w3.eth.contract(address=rate_oracle, abi=ORACLE_ABI)
    index_price = int(oracle.functions.getIndexPrice(checksum(args.underlying_pool), checksum(args.underlying_token)).call())
    if index_price <= 0:
        die(f"Rate oracle index price is non-positive: {index_price}")

    deploy_params = (
        checksum(args.underlying_pool),
        checksum(args.underlying_token),
        checksum(args.collateral),
        checksum(deployer),
        args.position_name,
        args.position_symbol,
        args.min_col_ratio,
        args.maintenance_margin,
        args.liquidation_close_factor,
        liquidation_module,
        funding_model,
        args.funding_period,
        args.decay_rate_wad,
        settlement_proxy,
        liquidation_params,
        spot_oracle,
        rate_oracle,
        args.oracle_period,
        args.pool_fee,
        args.tick_spacing,
    )

    preview_market_id, preview_broker_factory = factory.functions.createMarket(deploy_params).call({"from": deployer})
    preview_market_id_hex = Web3.to_hex(preview_market_id)
    if preview_market_id_hex.lower() != candidate_market_id_hex.lower():
        die(f"Preview market id mismatch: expected={candidate_market_id_hex}, got={preview_market_id_hex}")
    info(f"createMarket preview marketId={preview_market_id_hex} brokerFactory={preview_broker_factory}")

    receipt = send_contract_tx(
        w3,
        private_key,
        factory.functions.createMarket(deploy_params),
        "Factory.createMarket(CDS)",
        gas_cap=10_000_000,
    )
    deploy_block = int(receipt.blockNumber)
    deploy_timestamp = int(w3.eth.get_block(deploy_block)["timestamp"])

    if not core.functions.isValidMarket(candidate_market_id).call():
        die("Core did not register CDS market")

    market_addresses = core.functions.getMarketAddresses(candidate_market_id).call()
    collateral_token = checksum(market_addresses[0])
    underlying_token = checksum(market_addresses[1])
    underlying_pool = checksum(market_addresses[2])
    position_token = checksum(market_addresses[9])
    if collateral_token.lower() != checksum(args.collateral).lower():
        die(f"Unexpected CDS collateral: {collateral_token}")
    if market_addresses[6].lower() != funding_model.lower():
        die("CDS funding model was not stored in Core")
    if market_addresses[10].lower() != settlement_proxy.lower():
        die("CDS settlement proxy was not stored in Core")

    market_config = core.functions.getMarketConfig(candidate_market_id).call()
    if int(market_config[8]) != args.decay_rate_wad:
        die(f"Unexpected decayRateWad in Core: {market_config[8]}")

    token0, token1 = sorted([collateral_token, position_token], key=lambda addr: int(addr, 16))
    pool_id_bytes = compute_pool_id(token0, token1, args.pool_fee, args.tick_spacing)
    pool_id = Web3.to_hex(pool_id_bytes)
    spot_price = int(ghost.functions.getSpotPrice(pool_id_bytes).call())
    expected_pool_price = index_price
    if position_token.lower() == token1.lower():
        expected_pool_price = (10**36) // index_price
    tolerance = max(expected_pool_price // 1_000_000_000, 1_000)
    delta = abs(spot_price - expected_pool_price)
    if delta > tolerance:
        die(f"Ghost spot mismatch: spot={spot_price}, expected={expected_pool_price}, delta={delta}")

    ok(f"CDS market registered: {candidate_market_id_hex}")
    ok(f"CDS position token: {position_token}")
    ok(f"CDS pool id: {pool_id}")
    ok(f"Ghost spot verified: spot={spot_price}, expected={expected_pool_price}, delta={delta}")

    step("Update deployment config")
    cds_entry = {
        "type": "cds",
        "market_id": candidate_market_id_hex,
        "pool_id": pool_id,
        "collateral_token": collateral_token,
        "collateral_symbol": "USDC",
        "underlying_token": underlying_token,
        "underlying_pool": underlying_pool,
        "position_token": position_token,
        "position_name": args.position_name,
        "position_symbol": args.position_symbol,
        "broker_factory": checksum(preview_broker_factory),
        "rate_oracle": rate_oracle,
        "spot_oracle": spot_oracle,
        "funding_model": funding_model,
        "settlement_module": settlement_proxy,
        "decay_rate_wad": str(args.decay_rate_wad),
        "target_utilization": "0.90",
        "token0": checksum(token0),
        "token1": checksum(token1),
        "pool_fee": args.pool_fee,
        "tick_spacing": args.tick_spacing,
        "oracle_index_price_wad": str(index_price),
        "pool_spot_price_wad": str(spot_price),
        "pool_spot_expected_wad": str(expected_pool_price),
        "zero_for_one_long": collateral_token.lower() == token0.lower(),
        "deploy_block": deploy_block,
        "deploy_timestamp": deploy_timestamp,
        "session_start_block": deploy_block,
        "min_col_ratio": str(market_config[0]),
        "maintenance_margin": str(market_config[1]),
        "liq_close_factor": str(market_config[2]),
        "funding_period_sec": int(market_config[3]),
        "debt_cap": str(market_config[5]),
    }

    markets = dict(deploy.get("markets") or {})
    markets.setdefault("perp", market_entry_from_legacy(deploy))
    markets["cds"] = cds_entry
    deploy["markets"] = markets
    deploy["cds_market_id"] = candidate_market_id_hex
    deploy["cds_pool_id"] = pool_id
    deploy["cds_position_token"] = position_token
    deploy["cds_broker_factory"] = checksum(preview_broker_factory)
    deploy["cds_funding_model"] = funding_model
    deploy["cds_settlement_module"] = settlement_proxy

    if args.skip_write:
        info("--skip-write set; deployment.json not modified")
    else:
        write_json(deployment_path, deploy)
        ok(f"Wrote CDS market config to {deployment_path}")

    print("\n=== CDS Market Deployment Complete ===")
    print(json.dumps(cds_entry, indent=2))


if __name__ == "__main__":
    main()
