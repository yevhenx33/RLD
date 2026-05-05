#!/usr/bin/env python3
"""
Deploy or replace the BrokerRouter used by an already-created CDS market.

This is a targeted live repair for persistent demo chains. It does not recreate
Core, the CDS market, or the pool. It deploys a raw-collateral deposit adapter,
deploys a BrokerRouter bound to the selected CDS market, sets it as a default
operator for future brokers, and writes the per-market addresses to
docker/deployment.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

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


BROKER_FACTORY_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "DEFAULT_OPERATOR_ADMIN",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "defaultOperators",
        "stateMutability": "view",
        "inputs": [{"name": "", "type": "uint256"}],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "setDefaultOperators",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "operators", "type": "address[]"}],
        "outputs": [],
    },
]

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
]

BROKER_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "brokerFactory",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "marketId",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "collateralToken",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "positionToken",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
    },
    {
        "type": "function",
        "name": "depositAdapter",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "address"}],
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


def checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        die(f"deployment json not found: {path}")
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


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
    output = run_cmd(cmd, cwd=CONTRACTS_DIR)
    match = re.search(r"Deployed to:\s*(0x[a-fA-F0-9]{40})", output)
    if not match:
        die(f"Could not parse deployment address for {contract}. Output tail:\n{output[-1600:]}")
    return checksum(match.group(1))


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
        gas_limit = min(gas_cap, max(estimated + estimated // 4, 200_000))
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


def read_default_operators(factory: Any, limit: int = 16) -> list[str]:
    operators: list[str] = []
    for index in range(limit):
        try:
            operators.append(checksum(factory.functions.defaultOperators(index).call()))
        except Exception:
            break
    return operators


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy BrokerRouter for an existing CDS market.")
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--deployment-json", default=str(DEFAULT_DEPLOYMENT_JSON))
    parser.add_argument("--private-key", default=None)
    parser.add_argument("--market", default="cds")
    parser.add_argument("--replace-router", action="store_true")
    args = parser.parse_args()

    deployment_path = Path(args.deployment_json)
    deploy = load_json(deployment_path)
    markets = deploy.get("markets")
    if not isinstance(markets, dict) or args.market not in markets:
        die(f"deployment.json has no markets.{args.market} entry")
    entry = dict(markets[args.market])

    existing_router = entry.get("broker_router")
    if existing_router and not args.replace_router:
        die(f"markets.{args.market}.broker_router already set: {existing_router}. Pass --replace-router to replace it.")

    private_key = normalize_private_key(
        args.private_key or os.environ.get("DEPLOYER_KEY") or read_key_from_env_file(Path(args.env_file), "DEPLOYER_KEY")
    )
    deployer = checksum(Account.from_key(private_key).address)
    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC: {args.rpc_url}")

    step("Preflight")
    ok(f"Connected chainId={w3.eth.chain_id} block={w3.eth.block_number}")
    info(f"Deployer: {deployer}")

    core_addr = checksum(deploy.get("rld_core", ""))
    ghost_router = checksum(deploy.get("ghost_router", ""))
    permit2 = checksum(deploy.get("permit2", ""))
    for label, addr in (("rld_core", core_addr), ("ghost_router", ghost_router), ("permit2", permit2)):
        if not has_code(w3, addr):
            die(f"{label} has no code: {addr}")

    market_id = str(entry.get("market_id") or "")
    if not market_id.startswith("0x") or len(market_id) != 66:
        die(f"Invalid markets.{args.market}.market_id: {market_id}")
    market_id_bytes = bytes.fromhex(market_id[2:])
    core = w3.eth.contract(address=core_addr, abi=CORE_ABI)
    if not core.functions.isValidMarket(market_id_bytes).call():
        die(f"Core does not recognize market {market_id}")

    market_addresses = core.functions.getMarketAddresses(market_id_bytes).call()
    collateral_token = checksum(entry.get("collateral_token") or market_addresses[0])
    underlying_token = checksum(entry.get("underlying_token") or market_addresses[1])
    position_token = checksum(entry.get("position_token") or market_addresses[9])
    broker_factory = checksum(entry.get("broker_factory", ""))
    if not has_code(w3, broker_factory):
        die(f"broker_factory has no code: {broker_factory}")
    if collateral_token.lower() != checksum(market_addresses[0]).lower():
        die(f"deployment collateral does not match Core: {collateral_token} != {market_addresses[0]}")
    if position_token.lower() != checksum(market_addresses[9]).lower():
        die(f"deployment position token does not match Core: {position_token} != {market_addresses[9]}")
    ok(f"Market verified: {market_id}")

    factory = w3.eth.contract(address=broker_factory, abi=BROKER_FACTORY_ABI)
    admin = checksum(factory.functions.DEFAULT_OPERATOR_ADMIN().call())
    if admin.lower() != deployer.lower():
        die(f"DEPLOYER_KEY is not default operator admin: deployer={deployer}, admin={admin}")

    step("Deploy CDS router")
    deposit_adapter = deploy_contract_with_forge(
        "src/periphery/adapters/DirectDepositAdapter.sol:DirectDepositAdapter",
        private_key,
        args.rpc_url,
        [collateral_token],
    )
    ok(f"DirectDepositAdapter: {deposit_adapter}")

    router_config = (
        f"({broker_factory},{market_id},{collateral_token},{position_token},{underlying_token},{deposit_adapter})"
    )
    broker_router = deploy_contract_with_forge(
        "src/periphery/BrokerRouter.sol:BrokerRouter",
        private_key,
        args.rpc_url,
        [ghost_router, permit2, router_config],
    )
    ok(f"BrokerRouter: {broker_router}")

    router = w3.eth.contract(address=broker_router, abi=BROKER_ROUTER_ABI)
    if checksum(router.functions.brokerFactory().call()).lower() != broker_factory.lower():
        die("BrokerRouter brokerFactory immutable mismatch")
    if router.functions.marketId().call().hex().lower() != market_id[2:].lower():
        die("BrokerRouter marketId immutable mismatch")
    if checksum(router.functions.collateralToken().call()).lower() != collateral_token.lower():
        die("BrokerRouter collateralToken immutable mismatch")
    if checksum(router.functions.positionToken().call()).lower() != position_token.lower():
        die("BrokerRouter positionToken immutable mismatch")
    if checksum(router.functions.depositAdapter().call()).lower() != deposit_adapter.lower():
        die("BrokerRouter depositAdapter immutable mismatch")
    ok("BrokerRouter immutables verified")

    operators = read_default_operators(factory)
    if broker_router.lower() not in [op.lower() for op in operators]:
        operators.append(broker_router)
        send_contract_tx(
            w3,
            private_key,
            factory.functions.setDefaultOperators(operators),
            "PrimeBrokerFactory.setDefaultOperators",
            gas_cap=500_000,
        )
    else:
        ok("BrokerRouter already present in default operators")

    step("Update deployment config")
    markets[args.market]["broker_router"] = broker_router
    markets[args.market]["deposit_adapter"] = deposit_adapter
    deploy["markets"] = markets
    if args.market == "cds":
        deploy["cds_broker_router"] = broker_router
        deploy["cds_deposit_adapter"] = deposit_adapter
    write_json(deployment_path, deploy)
    ok(f"Wrote {deployment_path}")

    print("\n=== CDS Router Deployment Complete ===")
    print(json.dumps({
        "market": args.market,
        "market_id": market_id,
        "broker_factory": broker_factory,
        "broker_router": broker_router,
        "deposit_adapter": deposit_adapter,
    }, indent=2))


if __name__ == "__main__":
    main()
