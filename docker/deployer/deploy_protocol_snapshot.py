#!/usr/bin/env python3
"""
Deploy full RLD protocol (GhostRouter + TwapEngine) on an Anvil fork and write
/config/deployment.json for snapshot-based Reth bootstrap.

This script replaces the legacy phase scripts that depended on removed Foundry
script files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from eth_abi import encode as abi_encode
from eth_account import Account
from web3 import Web3


_DEFAULT_ROOT = Path("/workspace")
if _DEFAULT_ROOT.exists():
    RLD_ROOT = _DEFAULT_ROOT
else:
    # Local fallback when running directly from the repo checkout.
    RLD_ROOT = Path(__file__).resolve().parents[2]

CONTRACTS_DIR = RLD_ROOT / "contracts"

_default_deploy_path = Path("/config/deployment.json")
if not _default_deploy_path.parent.exists():
    _default_deploy_path = RLD_ROOT / "docker" / "deployment.json"
DEPLOYMENT_PATH = Path(os.getenv("DEPLOYMENT_JSON_OUT", str(_default_deploy_path)))

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Core external infra (mainnet canonical addresses)
POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"
V4_POSITION_MANAGER = "0xbD216513d74C8cf14cf4747E6AaA6420FF64ee9e"
V4_QUOTER = "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203"
V4_POSITION_DESCRIPTOR = "0xd1428ba554f4c8450b763a0b2040a4935c63f06c"
V4_STATE_VIEW = "0x7ffe42c4a5deea5b0fec41c94c136cf115597227"
UNIVERSAL_ROUTER = "0x66a9893cc07d91d95644aedd05d03f95e1dba8af"
PERMIT2 = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
AUSDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
SUSDE = "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"
USDC_WHALE = "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341"

FACTORY_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "initializeCore",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "_core", "type": "address"}],
        "outputs": [],
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

GHOST_ORACLE_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "transferOwnership",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "newOwner", "type": "address"}],
        "outputs": [],
    }
]

GHOST_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "registerEngine",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "engine", "type": "address"}],
        "outputs": [],
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
        "name": "transferOwnership",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "newOwner", "type": "address"}],
        "outputs": [],
    },
]

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
    }
]


def _fatal(msg: str) -> None:
    print(f"[ERR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def _step(msg: str) -> None:
    print(f"\n== {msg}")


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _info(msg: str) -> None:
    print(f"[..] {msg}")


def _checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name, default).strip()
    try:
        return Decimal(raw)
    except Exception:
        _info(f"Invalid {name}={raw!r}; defaulting to {default}")
        return Decimal(default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except Exception:
        _info(f"Invalid {name}={raw!r}; defaulting to {default}")
        return default


def _forge_create(contract_id: str, private_key: str, rpc_url: str, constructor_args: Sequence[Any]) -> str:
    cmd = [
        "forge",
        "create",
        contract_id,
        "--private-key",
        private_key,
        "--rpc-url",
        rpc_url,
        "--broadcast",
        "--legacy",
    ]
    if constructor_args:
        cmd.extend(["--constructor-args", *[str(a) for a in constructor_args]])

    last_output = ""
    for attempt in range(1, 6):
        proc = subprocess.run(
            cmd,
            cwd=str(CONTRACTS_DIR),
            capture_output=True,
            text=True,
        )
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        last_output = output
        if proc.returncode == 0:
            match = re.search(r"Deployed to:\s*(0x[a-fA-F0-9]{40})", output)
            if not match:
                _fatal(f"Could not parse deployment address for {contract_id}\n{output[-1200:]}")
            return _checksum(match.group(1))

        lower = output.lower()
        if (
            "replacement transaction underpriced" in lower
            or "nonce too low" in lower
            or "contract was not deployed" in lower
        ):
            _info(f"{contract_id} transient deploy failure (attempt {attempt}/5), retrying...")
            time.sleep(1)
            continue

        _fatal(f"forge create failed for {contract_id}\n{output[-1200:]}")

    _fatal(f"forge create failed after retries for {contract_id}\n{last_output[-1200:]}")
    return ZERO_ADDRESS


def _send_tx(
    w3: Web3,
    private_key: str,
    fn_call: Any,
    label: str,
    gas_cap: int = 8_000_000,
) -> Any:
    account = Account.from_key(private_key)
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    gas_price = max(w3.eth.gas_price, Web3.to_wei(2, "gwei"))

    try:
        estimated = fn_call.estimate_gas({"from": account.address})
        gas_limit = min(gas_cap, max(int(estimated * 1.2), 200_000))
    except Exception:
        gas_limit = gas_cap

    tx = fn_call.build_transaction(
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
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if receipt.status != 1:
        _fatal(f"{label} reverted: {tx_hash.hex()}")
    _ok(f"{label}: tx={tx_hash.hex()} gas={receipt.gasUsed:,}")
    return receipt


def _has_code(w3: Web3, address: str) -> bool:
    return len(w3.eth.get_code(_checksum(address))) > 0


def _must_have_code(w3: Web3, address: str, label: str) -> None:
    if not _has_code(w3, address):
        _fatal(f"{label} has no code: {address}")
    _ok(f"{label} code present: {address}")


def _normalize_rate_fraction(raw_rate: Decimal) -> Decimal | None:
    """Normalize feed value to APY rate fraction r (e.g., 14% => 0.14)."""
    if raw_rate < 0:
        return None
    # Support both percent-like and fraction-like payloads.
    if raw_rate > 1:
        return raw_rate / Decimal(100)
    return raw_rate


def _normalize_api_base(url: str | None) -> str:
    if not url:
        return ""
    return url.strip().rstrip("/")


def _candidate_rate_api_bases(api_url: str | None) -> list[str]:
    configured = [
        api_url,
        os.getenv("ENVIO_API_URL", ""),
        os.getenv("API_URL", ""),
        os.getenv("RATES_API_URL", ""),
        f"http://localhost:{os.getenv('ENVIO_API_PORT', '5000')}",
        "http://127.0.0.1:5000",
        "http://rld_graphql_api:5000",
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in configured:
        base = _normalize_api_base(candidate)
        if not base or base in seen:
            continue
        seen.add(base)
        ordered.append(base)
    return ordered


def _parse_rate_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            pass
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _fetch_live_rate_fraction(api_url: str | None) -> Decimal | None:
    """Fetch live USDC borrow rate fraction r strictly from the unified REST endpoint."""
    min_rate = _env_decimal("MIN_BORROW_APY", "0.0")
    max_rate = _env_decimal("MAX_BORROW_APY", "2.0")
    max_rate_age_seconds = _env_int("MAX_RATE_AGE_SECONDS", 900)
    require_rate_timestamp = os.getenv("REQUIRE_RATE_TIMESTAMP", "1").strip().lower() not in {"0", "false", "no"}

    for base in _candidate_rate_api_bases(api_url):
        endpoint = f"{base}/api/v1/oracle/usdc-borrow-apy"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                apy = data.get("borrow_apy")
                if apy is None:
                    _info(f"Rate API payload missing borrow_apy from {endpoint}")
                    continue

                rate_fraction = _normalize_rate_fraction(Decimal(str(apy)))
                if rate_fraction is None:
                    _info(f"Rate API returned invalid APY from {endpoint}: {apy}")
                    continue
                if rate_fraction < min_rate or rate_fraction > max_rate:
                    _info(
                        f"Ignoring out-of-range APY from {endpoint}: "
                        f"{rate_fraction} (expected {min_rate}..{max_rate})"
                    )
                    continue

                if max_rate_age_seconds > 0:
                    updated_at = _parse_rate_timestamp(
                        data.get("timestamp")
                        or data.get("updated_at")
                        or data.get("updatedAt")
                        or data.get("ts")
                    )
                    if updated_at is None:
                        if require_rate_timestamp:
                            _info(f"Ignoring rate payload without timestamp from {endpoint}")
                            continue
                    else:
                        age_seconds = max(0, int(time.time()) - int(updated_at))
                        if age_seconds > max_rate_age_seconds:
                            _info(
                                f"Ignoring stale APY from {endpoint}: age={age_seconds}s "
                                f"(max={max_rate_age_seconds}s)"
                            )
                            continue

                return rate_fraction
        except Exception:
            continue

    return None


def _rate_fraction_to_ray(rate_fraction: Decimal) -> int:
    return int(rate_fraction * Decimal(10**27))


def _poll_rpc_ready(rpc_url: str, max_tries: int = 60) -> None:
    for i in range(1, max_tries + 1):
        try:
            proc = subprocess.run(
                ["cast", "block-number", "--rpc-url", rpc_url],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            if i < max_tries:
                _info(f"RPC probe timeout ({i}/{max_tries})")
                continue
            _fatal(f"RPC did not become ready: timeout after {max_tries} probes")

        if proc.returncode == 0:
            out = (proc.stdout or "").strip()
            _ok(f"RPC ready at block {out.strip()}")
            return
        if i < max_tries:
            continue
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        _fatal(f"RPC did not become ready: {output[-400:]}")
    _fatal("RPC did not become ready")


def main() -> None:
    rpc_url = os.getenv("RPC_URL", "http://host.docker.internal:8545")
    deployer_key = os.getenv("DEPLOYER_KEY", "").strip()
    api_url = os.getenv("API_URL", "").strip() or None
    require_live_rate = os.getenv("REQUIRE_LIVE_RATE", "1").strip().lower() not in {"0", "false", "no"}
    fork_block = int(os.getenv("FORK_BLOCK", "0"))

    if not deployer_key:
        _fatal("DEPLOYER_KEY is required")
    if not deployer_key.startswith("0x"):
        deployer_key = "0x" + deployer_key
    if len(deployer_key) != 66:
        _fatal("DEPLOYER_KEY must be 32 bytes")

    deployer = Account.from_key(deployer_key).address

    _step("Preflight")
    _info(f"RPC URL: {rpc_url}")
    _info(f"Deployer: {deployer}")
    _poll_rpc_ready(rpc_url)

    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        _fatal(f"Cannot connect to RPC: {rpc_url}")

    for label, addr in [
        ("PoolManager", POOL_MANAGER),
        ("V4 PositionManager", V4_POSITION_MANAGER),
        ("V4 Quoter", V4_QUOTER),
        ("Permit2", PERMIT2),
        ("Aave Pool", AAVE_POOL),
        ("USDC", USDC),
        ("aUSDC", AUSDC),
    ]:
        _must_have_code(w3, addr, label)

    deployer_eth = w3.eth.get_balance(deployer)
    if deployer_eth == 0:
        _fatal("Deployer has 0 ETH")
    _ok(f"Deployer ETH: {w3.from_wei(deployer_eth, 'ether')} ETH")

    _step("Deploy protocol components")
    mock_oracle = _forge_create(
        "src/rld/modules/oracles/MockRLDAaveOracle.sol:MockRLDAaveOracle",
        deployer_key,
        rpc_url,
        [],
    )
    _ok(f"Mock oracle: {mock_oracle}")

    # Live APY sync from Envio/data-pipeline.
    live_rate_fraction = _fetch_live_rate_fraction(api_url)
    if live_rate_fraction is not None:
        rate_ray = _rate_fraction_to_ray(live_rate_fraction)
        oracle = w3.eth.contract(address=_checksum(mock_oracle), abi=MOCK_ORACLE_ABI)
        _send_tx(w3, deployer_key, oracle.functions.setRate(rate_ray), "MockOracle.setRate(live)")
        implied_index_price = live_rate_fraction * Decimal(100)
        _ok(
            f"Live APY synced: r={live_rate_fraction} -> P={implied_index_price} (K=100) -> ray {rate_ray}"
        )
    else:
        if require_live_rate:
            _fatal(
                "Rates API unavailable or returned no usable APY. "
                "Live index rate is required (set REQUIRE_LIVE_RATE=0 to allow fallback)."
            )
        _info("Rates API unavailable; using default mock rate")

    oracle_contract = w3.eth.contract(address=_checksum(mock_oracle), abi=MOCK_ORACLE_ABI)

    wausdc = _forge_create(
        "src/shared/wrappers/WrappedAToken.sol:WrappedAToken",
        deployer_key,
        rpc_url,
        [AUSDC, "Wrapped aUSDC", "waUSDC"],
    )
    _ok(f"waUSDC wrapper: {wausdc}")

    sim_funder = _forge_create(
        "src/periphery/SimFunder.sol:SimFunder",
        deployer_key,
        rpc_url,
        [USDC, AUSDC, wausdc, AAVE_POOL],
    )
    _ok(f"SimFunder: {sim_funder}")

    v4_module = _forge_create(
        "src/rld/modules/broker/UniswapV4BrokerModule.sol:UniswapV4BrokerModule",
        deployer_key,
        rpc_url,
        [],
    )
    twap_module = _forge_create(
        "src/rld/modules/broker/TwapEngineBrokerModule.sol:TWAPBrokerModule",
        deployer_key,
        rpc_url,
        [],
    )
    prime_broker_impl = _forge_create(
        "src/rld/broker/PrimeBroker.sol:PrimeBroker",
        deployer_key,
        rpc_url,
        [v4_module, twap_module, V4_POSITION_MANAGER],
    )
    position_token_impl = _forge_create(
        "src/rld/tokens/PositionToken.sol:PositionToken",
        deployer_key,
        rpc_url,
        ["PositionToken Impl", "pWRLP", 6, wausdc],
    )
    funding_model = _forge_create(
        "src/rld/modules/funding/StandardFundingModel.sol:StandardFundingModel",
        deployer_key,
        rpc_url,
        [],
    )
    liquidation_module = _forge_create(
        "src/rld/modules/liquidation/DutchLiquidationModule.sol:DutchLiquidationModule",
        deployer_key,
        rpc_url,
        [],
    )
    ghost_oracle = _forge_create(
        "src/rld/modules/oracles/GhostSingletonOracle.sol:GhostSingletonOracle",
        deployer_key,
        rpc_url,
        [],
    )
    broker_router = _forge_create(
        "src/periphery/BrokerRouter.sol:BrokerRouter",
        deployer_key,
        rpc_url,
        [POOL_MANAGER, PERMIT2],
    )
    ghost_router = _forge_create(
        "src/dex/GhostRouter.sol:GhostRouter",
        deployer_key,
        rpc_url,
        [POOL_MANAGER, deployer],
    )

    twap_interval = int(os.getenv("TWAP_INTERVAL", "3600"))
    twap_max_discount_bps = int(os.getenv("TWAP_MAX_DISCOUNT_BPS", "500"))
    twap_discount_rate_scaled = int(os.getenv("TWAP_DISCOUNT_RATE_SCALED", "10000000000"))

    twap_engine = _forge_create(
        "src/dex/TwapEngine.sol:TwapEngine",
        deployer_key,
        rpc_url,
        [ghost_router, twap_interval, twap_max_discount_bps, twap_discount_rate_scaled],
    )
    twap_engine_lens = _forge_create(
        "src/dex/TwapEngineLens.sol:TwapEngineLens",
        deployer_key,
        rpc_url,
        [twap_engine],
    )
    broker_executor = _forge_create(
        "src/periphery/BrokerExecutor.sol:BrokerExecutor",
        deployer_key,
        rpc_url,
        [],
    )

    funding_period = int(os.getenv("FUNDING_PERIOD", str(30 * 24 * 60 * 60)))
    factory = _forge_create(
        "src/rld/core/RLDMarketFactory.sol:RLDMarketFactory",
        deployer_key,
        rpc_url,
        [
            POOL_MANAGER,
            position_token_impl,
            prime_broker_impl,
            ghost_oracle,
            funding_model,
            ghost_router,
            deployer,  # metadata renderer placeholder (non-zero required)
            funding_period,
            broker_router,
        ],
    )
    core = _forge_create(
        "src/rld/core/RLDCore.sol:RLDCore",
        deployer_key,
        rpc_url,
        [factory, POOL_MANAGER],
    )

    _ok("Core stack deployed")
    _info(f"RLDCore={core}")
    _info(f"Factory={factory}")
    _info(f"GhostRouter={ghost_router}")
    _info(f"TwapEngine={twap_engine}")
    _info(f"BrokerExecutor={broker_executor}")

    _step("Wire core + market infrastructure")
    factory_contract = w3.eth.contract(address=_checksum(factory), abi=FACTORY_ABI)
    core_contract = w3.eth.contract(address=_checksum(core), abi=CORE_ABI)
    ghost_oracle_contract = w3.eth.contract(address=_checksum(ghost_oracle), abi=GHOST_ORACLE_ABI)
    ghost_router_contract = w3.eth.contract(address=_checksum(ghost_router), abi=GHOST_ROUTER_ABI)

    _send_tx(w3, deployer_key, factory_contract.functions.initializeCore(core), "Factory.initializeCore")
    _send_tx(
        w3,
        deployer_key,
        ghost_oracle_contract.functions.transferOwnership(factory),
        "GhostOracle.transferOwnership(factory)",
    )
    _send_tx(w3, deployer_key, ghost_router_contract.functions.registerEngine(twap_engine), "GhostRouter.registerEngine")
    _send_tx(
        w3,
        deployer_key,
        ghost_router_contract.functions.transferOwnership(factory),
        "GhostRouter.transferOwnership(factory)",
    )

    pool_fee = int(os.getenv("POOL_FEE", "500"))
    tick_spacing = int(os.getenv("TICK_SPACING", "5"))
    oracle_period = int(os.getenv("ORACLE_PERIOD", "3600"))

    # Packed Dutch liquidation params:
    # [0..15] baseDiscountBps | [16..31] maxDiscountBps | [32..47] slope
    liq_base_discount_bps = int(os.getenv("LIQ_BASE_DISCOUNT_BPS", "100"))
    liq_max_discount_bps = int(os.getenv("LIQ_MAX_DISCOUNT_BPS", "500"))
    liq_slope = int(os.getenv("LIQ_SLOPE", "200"))
    liquidation_params_int = (
        liq_base_discount_bps | (liq_max_discount_bps << 16) | (liq_slope << 32)
    )
    liquidation_params = Web3.to_hex(liquidation_params_int.to_bytes(32, byteorder="big"))

    deploy_params = (
        AAVE_POOL,                 # underlyingPool
        USDC,                      # underlyingToken
        wausdc,                    # collateralToken
        deployer,                  # curator
        "Wrapped RLP: aUSDC",      # positionTokenName
        "wRLP",                    # positionTokenSymbol
        1_200_000_000_000_000_000, # minColRatio
        1_090_000_000_000_000_000, # maintenanceMargin
        500_000_000_000_000_000,   # liquidationCloseFactor
        liquidation_module,        # liquidationModule
        ZERO_ADDRESS,              # fundingModel (use default)
        0,                         # fundingPeriod (use default)
        0,                         # decayRateWad
        ZERO_ADDRESS,              # settlementModule
        liquidation_params,        # liquidationParams
        mock_oracle,               # spotOracle (mock)
        mock_oracle,               # rateOracle (mock)
        oracle_period,             # oraclePeriod
        pool_fee,                  # poolFee
        tick_spacing,              # tickSpacing
    )

    preview_market_id, preview_broker_factory = factory_contract.functions.createMarket(deploy_params).call(
        {"from": deployer}
    )
    _ok(f"createMarket preview marketId={Web3.to_hex(preview_market_id)} brokerFactory={preview_broker_factory}")

    create_receipt = _send_tx(
        w3,
        deployer_key,
        factory_contract.functions.createMarket(deploy_params),
        "Factory.createMarket",
        gas_cap=10_000_000,
    )

    market_id_bytes = preview_market_id
    market_id = Web3.to_hex(market_id_bytes)
    broker_factory = _checksum(preview_broker_factory)

    if not core_contract.functions.isValidMarket(market_id_bytes).call():
        _fatal("Core did not register market")
    _ok("Core confirms market is valid")

    market_addresses = core_contract.functions.getMarketAddresses(market_id_bytes).call()
    collateral_token = _checksum(market_addresses[0])
    underlying_token = _checksum(market_addresses[1])
    underlying_pool = _checksum(market_addresses[2])
    position_token = _checksum(market_addresses[9])
    if collateral_token != _checksum(wausdc):
        _fatal(f"Unexpected collateral token in market: {collateral_token} != {wausdc}")
    _ok(f"Market addresses verified: collateral={collateral_token}, positionToken={position_token}")

    expected_core_market_id = Web3.to_hex(
        Web3.keccak(
            abi_encode(
                ["address", "address", "address"],
                [collateral_token, underlying_token, underlying_pool],
            )
        )
    )
    if expected_core_market_id.lower() != market_id.lower():
        _fatal(
            f"Core marketId mismatch: expected={expected_core_market_id}, actual={market_id}"
        )
    _ok(f"Core marketId verified: {market_id}")

    token0, token1 = sorted([collateral_token, position_token], key=lambda a: int(a, 16))
    pool_id_bytes = Web3.keccak(
        abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [token0, token1, pool_fee, tick_spacing, ZERO_ADDRESS],
        )
    )
    pool_id = Web3.to_hex(pool_id_bytes)
    _ok(f"V4 poolId verified: {pool_id}")

    spot_price = int(ghost_router_contract.functions.getSpotPrice(pool_id_bytes).call())
    if spot_price == 0:
        _fatal("GhostRouter spot price is zero")

    # E2E price wiring check:
    # factory uses rateOracle.getIndexPrice(underlyingPool, underlyingToken),
    # then inverts if positionToken is token1 in pool ordering.
    oracle_index_price = int(
        oracle_contract.functions.getIndexPrice(_checksum(AAVE_POOL), _checksum(USDC)).call()
    )
    if oracle_index_price <= 0:
        _fatal(f"Oracle index price is non-positive: {oracle_index_price}")
    expected_pool_price = oracle_index_price
    if _checksum(position_token).lower() == _checksum(token1).lower():
        expected_pool_price = (10**36) // expected_pool_price

    # Allow small rounding error from sqrt(price) quantization in Q64.96.
    tolerance = max(expected_pool_price // 1_000_000_000, 1_000)
    delta = abs(spot_price - expected_pool_price)
    if delta > tolerance:
        _fatal(
            "GhostRouter spot mismatch vs oracle-driven pool init "
            f"(expected={expected_pool_price}, spot={spot_price}, delta={delta}, tolerance={tolerance})"
        )
    _ok(
        "GhostRouter market verified; "
        f"spot={spot_price}, expected={expected_pool_price}, delta={delta}"
    )

    # Optional BondFactory deployment. BasisTradeFactory remains opt-in (heavy deps).
    bond_factory = ""
    if os.getenv("DEPLOY_BOND_FACTORY", "true").lower() in ("1", "true", "yes"):
        try:
            bond_factory = _forge_create(
                "src/periphery/BondFactory.sol:BondFactory",
                deployer_key,
                rpc_url,
                [broker_factory, broker_router, twap_engine, wausdc, POOL_MANAGER, V4_QUOTER],
            )
            _ok(f"BondFactory: {bond_factory}")
        except SystemExit:
            _info("BondFactory deployment failed; continuing without it")
            bond_factory = ""

    latest_block = w3.eth.get_block("latest")
    deploy_block = int(latest_block["number"])
    deploy_timestamp = int(latest_block["timestamp"])

    zero_for_one_long = _checksum(wausdc).lower() == _checksum(token0).lower()

    deployment = {
        "fork_block": fork_block,
        "deploy_block": deploy_block,
        "deploy_timestamp": deploy_timestamp,
        "rpc_url": rpc_url,
        "rld_core": _checksum(core),
        # Legacy key kept for backward compatibility. Hookless pool => zero address.
        "twamm_hook": _checksum(ZERO_ADDRESS),
        # New dex keys
        "ghost_router": _checksum(ghost_router),
        "twap_engine": _checksum(twap_engine),
        "twap_engine_lens": _checksum(twap_engine_lens),
        "ghost_oracle": _checksum(ghost_oracle),
        "market_id": market_id,
        "mock_oracle": _checksum(mock_oracle),
        "broker_router": _checksum(broker_router),
        "wausdc": _checksum(wausdc),
        "position_token": _checksum(position_token),
        "sim_funder": _checksum(sim_funder),
        "broker_factory": _checksum(broker_factory),
        # Keep empty to avoid incompatible legacy swap path usage.
        "swap_router": "",
        "bond_factory": _checksum(bond_factory) if bond_factory else "",
        "basis_trade_factory": "",
        "broker_executor": _checksum(broker_executor),
        "pool_manager": _checksum(POOL_MANAGER),
        "v4_pool_manager": _checksum(POOL_MANAGER),
        "pool_id": pool_id,
        "pool_fee": pool_fee,
        "tick_spacing": tick_spacing,
        "oracle_index_price_wad": str(oracle_index_price),
        "pool_spot_price_wad": str(spot_price),
        "pool_spot_expected_wad": str(expected_pool_price),
        "v4_quoter": _checksum(V4_QUOTER),
        "v4_position_manager": _checksum(V4_POSITION_MANAGER),
        "v4_position_descriptor": _checksum(V4_POSITION_DESCRIPTOR),
        "v4_state_view": _checksum(V4_STATE_VIEW),
        "universal_router": _checksum(UNIVERSAL_ROUTER),
        "permit2": _checksum(PERMIT2),
        "token0": _checksum(token0),
        "token1": _checksum(token1),
        "zero_for_one_long": bool(zero_for_one_long),
        "external_contracts": {
            "usdc": _checksum(USDC),
            "ausdc": _checksum(AUSDC),
            "aave_pool": _checksum(AAVE_POOL),
            "susde": _checksum(SUSDE),
            "usdc_whale": _checksum(USDC_WHALE),
        },
        # Indexer starts from the fork block while on Anvil.
        "session_start_block": fork_block,
    }

    DEPLOYMENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEPLOYMENT_PATH.write_text(json.dumps(deployment, indent=2) + "\n")
    _ok(f"Wrote deployment config: {DEPLOYMENT_PATH}")

    # Best-effort: reset indexer if running in legacy compose path.
    reset_url = os.getenv("INDEXER_RESET_URL", "http://indexer:8080/admin/reset")
    admin_token = os.getenv("INDEXER_ADMIN_TOKEN", "").strip()
    if os.getenv("SKIP_INDEXER_RESET", "false").lower() not in ("1", "true", "yes"):
        try:
            req = urllib.request.Request(reset_url, method="POST")
            if admin_token:
                req.add_header("X-Admin-Token", admin_token)
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
            _ok(f"Indexer reset response: {body[:200]}")
        except Exception as exc:
            _info(f"Indexer reset skipped/unavailable: {exc}")

    print("\n=== Deployment Complete ===")
    print(json.dumps(deployment, indent=2))


if __name__ == "__main__":
    main()

