#!/usr/bin/env python3
"""
test_twap_order_flow.py
=======================

Runtime TWAP validation on a deployed GhostRouter mock market.

Flow:
  1. Load `ghost-router-mock-market.json`
  2. Deploy TwapEngine (forge create)
  3. Register engine on GhostRouter
  4. Mint/approve mock tokens for maker + taker
  5. Place overlapping TWAP orders
  6. Verify order progression + sell-rate math
  7. Execute taker swap through GhostRouter (engine intercept)
  8. Verify per-order claims and optional cancel broadcast

Designed for local dev chains (e.g. anvil/reth-dev at chainId 31337).
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
from web3.logs import DISCARD


SCRIPT_DIR = Path(__file__).resolve().parent
RLD_ROOT = SCRIPT_DIR.parent.parent
CONTRACTS_DIR = RLD_ROOT / "contracts"
DOCKER_ENV = SCRIPT_DIR.parent / ".env"

DEFAULT_RPC_URL = "http://localhost:8545"
DEFAULT_REPORT = SCRIPT_DIR / "ghost-router-mock-market.json"

# Standard anvil defaults (#1 and #2)
DEFAULT_MAKER_KEY = "0x59c6995e998f97a5a0044966f0945387dc9e86dae88c7a8412f4603b6b78690d"
DEFAULT_TAKER_KEY = "0x5de4111afa1a4b94908f83103c07f6f4a5f9f4d7f7f2f6d8fa5f2ff3817f4a45"


ROUTER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "registerEngine",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "engine", "type": "address"}],
        "outputs": [],
    },
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
    {
        "type": "function",
        "name": "swap",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "zeroForOne", "type": "bool"},
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMinimum", "type": "uint256"},
        ],
        "outputs": [{"name": "amountOut", "type": "uint256"}],
    },
    {
        "type": "event",
        "name": "SwapExecuted",
        "anonymous": False,
        "inputs": [
            {"name": "marketId", "type": "bytes32", "indexed": True},
            {"name": "sender", "type": "address", "indexed": True},
            {"name": "zeroForOne", "type": "bool", "indexed": False},
            {"name": "amountIn", "type": "uint256", "indexed": False},
            {"name": "amountOut", "type": "uint256", "indexed": False},
            {"name": "amountOutMinimum", "type": "uint256", "indexed": False},
        ],
    },
]


TWAP_ENGINE_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "submitStream",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "zeroForOne", "type": "bool"},
            {"name": "duration", "type": "uint256"},
            {"name": "amountIn", "type": "uint256"},
        ],
        "outputs": [{"name": "orderId", "type": "bytes32"}],
    },
    {
        "type": "function",
        "name": "claimTokens",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "orderId", "type": "bytes32"},
        ],
        "outputs": [{"name": "earningsOut", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "cancelOrder",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "orderId", "type": "bytes32"},
        ],
        "outputs": [
            {"name": "refund", "type": "uint256"},
            {"name": "earningsOut", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "getCancelOrderStateExact",
        "stateMutability": "view",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "orderId", "type": "bytes32"},
        ],
        "outputs": [
            {"name": "buyTokensOwed", "type": "uint256"},
            {"name": "sellTokensRefund", "type": "uint256"},
        ],
    },
    {
        "type": "function",
        "name": "streamOrders",
        "stateMutability": "view",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "orderId", "type": "bytes32"},
        ],
        "outputs": [
            {"name": "owner", "type": "address"},
            {"name": "sellRate", "type": "uint256"},
            {"name": "earningsFactorLast", "type": "uint256"},
            {"name": "startEpoch", "type": "uint256"},
            {"name": "expiration", "type": "uint256"},
            {"name": "zeroForOne", "type": "bool"},
        ],
    },
    {
        "type": "function",
        "name": "streamPools",
        "stateMutability": "view",
        "inputs": [
            {"name": "marketId", "type": "bytes32"},
            {"name": "zeroForOne", "type": "bool"},
        ],
        "outputs": [
            {"name": "sellRateCurrent", "type": "uint256"},
            {"name": "earningsFactorCurrent", "type": "uint256"},
        ],
    },
    {
        "type": "event",
        "name": "StreamSubmitted",
        "anonymous": False,
        "inputs": [
            {"name": "marketId", "type": "bytes32", "indexed": True},
            {"name": "orderId", "type": "bytes32", "indexed": True},
            {"name": "owner", "type": "address", "indexed": True},
            {"name": "zeroForOne", "type": "bool", "indexed": False},
            {"name": "amountIn", "type": "uint256", "indexed": False},
            {"name": "startEpoch", "type": "uint256", "indexed": False},
            {"name": "expiration", "type": "uint256", "indexed": False},
            {"name": "sellRate", "type": "uint256", "indexed": False},
        ],
    },
    {
        "type": "event",
        "name": "GhostTaken",
        "anonymous": False,
        "inputs": [
            {"name": "marketId", "type": "bytes32", "indexed": True},
            {"name": "zeroForOne", "type": "bool", "indexed": True},
            {"name": "amountIn", "type": "uint256", "indexed": False},
            {"name": "filledOut", "type": "uint256", "indexed": False},
            {"name": "inputConsumed", "type": "uint256", "indexed": False},
            {"name": "spotPrice", "type": "uint256", "indexed": False},
        ],
    },
    {
        "type": "event",
        "name": "TokensClaimed",
        "anonymous": False,
        "inputs": [
            {"name": "marketId", "type": "bytes32", "indexed": True},
            {"name": "orderId", "type": "bytes32", "indexed": True},
            {"name": "owner", "type": "address", "indexed": True},
            {"name": "earningsOut", "type": "uint256", "indexed": False},
        ],
    },
    {
        "type": "event",
        "name": "OrderCancelled",
        "anonymous": False,
        "inputs": [
            {"name": "marketId", "type": "bytes32", "indexed": True},
            {"name": "orderId", "type": "bytes32", "indexed": True},
            {"name": "owner", "type": "address", "indexed": True},
            {"name": "refund", "type": "uint256", "indexed": False},
            {"name": "earnings", "type": "uint256", "indexed": False},
            {"name": "orderStarted", "type": "bool", "indexed": False},
            {"name": "orderExpired", "type": "bool", "indexed": False},
        ],
    },
]


ERC20_ABI: list[dict[str, Any]] = [
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
        "inputs": [{"name": "account", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
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


def normalize_private_key(value: str | None, label: str) -> str:
    if not value:
        die(f"{label} is required.")
    key = value.strip()
    if not key.startswith("0x"):
        key = f"0x{key}"
    if len(key) != 66:
        die(f"{label} must be 32 bytes (64 hex chars).")
    return key


def run_cmd(cmd: Sequence[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if proc.returncode != 0:
        joined = " ".join(cmd)
        tail = (proc.stderr or proc.stdout)[-1200:]
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


def send_contract_tx(
    w3: Web3,
    sender_key: str,
    function_call: Any,
    label: str,
    gas_cap: int = 2_500_000,
) -> Any:
    account = Account.from_key(sender_key)
    sender = account.address
    nonce = w3.eth.get_transaction_count(sender, "pending")
    gas_price = max(w3.eth.gas_price, Web3.to_wei(2, "gwei"))

    try:
        estimated = function_call.estimate_gas({"from": sender})
        # Keep generous headroom: some stateful paths (TWAP accrual/crossing)
        # can exceed naive estimates between estimate and execution.
        headroom = max(estimated // 2, 250_000)
        gas_limit = min(gas_cap, max(estimated + headroom, 200_000))
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


def send_eth_transfer(w3: Web3, sender_key: str, to_addr: str, wei_amount: int, label: str) -> None:
    account = Account.from_key(sender_key)
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    gas_price = max(w3.eth.gas_price, Web3.to_wei(2, "gwei"))
    tx = {
        "from": account.address,
        "to": Web3.to_checksum_address(to_addr),
        "value": wei_amount,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gas": 21_000,
        "gasPrice": gas_price,
    }
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        die(f"{label} reverted (tx={tx_hash.hex()})")
    ok(f"{label}: tx={tx_hash.hex()} value={wei_amount}")


def advance_time_or_blocks(w3: Web3, seconds: int, fallback_key: str, fallback_steps: int = 1) -> None:
    # Try JSON-RPC time travel first (anvil/hardhat).
    increased = w3.provider.make_request("evm_increaseTime", [seconds])
    mined = w3.provider.make_request("evm_mine", [])
    if "error" not in increased and "error" not in mined:
        ok(f"Advanced chain time by {seconds}s via evm_increaseTime + evm_mine.")
        return

    info("evm_increaseTime not available; advancing with fallback self-transfers.")
    sender = Account.from_key(fallback_key).address
    for i in range(fallback_steps):
        send_eth_transfer(w3, fallback_key, sender, 0, f"mine-fallback-{i + 1}")


def ensure_address_report(report: dict[str, Any], key: str) -> str:
    value = report.get(key)
    if not value or not isinstance(value, str):
        die(f"Missing '{key}' in report.")
    return Web3.to_checksum_address(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy TwapEngine and verify TWAP order placement on GhostRouter market.")
    parser.add_argument("--rpc-url", default=os.environ.get("RPC_URL", DEFAULT_RPC_URL))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--deployer-key", default=None)
    parser.add_argument("--maker-key", default=os.environ.get("MAKER_KEY", DEFAULT_MAKER_KEY))
    parser.add_argument("--taker-key", default=os.environ.get("TAKER_KEY", DEFAULT_TAKER_KEY))
    parser.add_argument("--interval", type=int, default=1, help="TwapEngine epoch interval in seconds.")
    parser.add_argument("--duration", type=int, default=30, help="Primary TWAP order duration in seconds.")
    parser.add_argument("--overlap-duration", type=int, default=60, help="Overlapping TWAP order duration in seconds.")
    parser.add_argument("--order-amount", default="1200", help="Primary maker sell amount in whole tokens.")
    parser.add_argument("--overlap-order-amount", default="600", help="Overlapping maker sell amount in whole tokens.")
    parser.add_argument("--swap-amount-in", default="20", help="Taker input amount in whole tokens.")
    parser.add_argument("--max-discount-bps", type=int, default=500)
    parser.add_argument("--discount-rate-scaled", type=int, default=0)
    parser.add_argument(
        "--broadcast-cancel",
        action="store_true",
        help="Broadcast cancel tx for the primary order after claim checks.",
    )
    args = parser.parse_args()

    report_path = Path(args.report).expanduser()
    if not report_path.exists():
        die(f"Report file not found: {report_path}")
    report = json.loads(report_path.read_text())

    deployer_key = normalize_private_key(
        args.deployer_key or os.environ.get("DEPLOYER_KEY") or read_key_from_env_file(DOCKER_ENV, "DEPLOYER_KEY"),
        "deployer key",
    )
    maker_key = normalize_private_key(args.maker_key, "maker key")
    taker_key = normalize_private_key(args.taker_key, "taker key")

    deployer = Account.from_key(deployer_key).address
    maker = Account.from_key(maker_key).address
    taker = Account.from_key(taker_key).address

    step("Step 0: Preflight")
    info(f"RPC: {args.rpc_url}")
    info(f"Report: {report_path}")
    info(f"Deployer: {deployer}")
    info(f"Maker: {maker}")
    info(f"Taker: {taker}")

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        die(f"Cannot connect to RPC endpoint: {args.rpc_url}")
    ok(f"Connected. chainId={w3.eth.chain_id}, latestBlock={w3.eth.block_number}")

    router_addr = ensure_address_report(report, "ghost_router")
    token0_addr = ensure_address_report(report, "token0")
    token1_addr = ensure_address_report(report, "token1")
    market_id_hex = report.get("market_id")
    if not isinstance(market_id_hex, str):
        die("Missing 'market_id' in report.")
    market_id = bytes.fromhex(market_id_hex.removeprefix("0x"))
    if len(market_id) != 32:
        die("market_id must be bytes32 hex.")

    for name, addr in [("GhostRouter", router_addr), ("token0", token0_addr), ("token1", token1_addr)]:
        if not has_code(w3, addr):
            die(f"No deployed code at {name} address {addr}")
        ok(f"{name} code verified at {addr}")

    router = w3.eth.contract(address=router_addr, abi=ROUTER_ABI)
    engine_abi = TWAP_ENGINE_ABI
    token0 = w3.eth.contract(address=token0_addr, abi=ERC20_ABI)
    token1 = w3.eth.contract(address=token1_addr, abi=ERC20_ABI)

    spot_price = int(router.functions.getSpotPrice(market_id).call())
    if spot_price == 0:
        die("Router spot price is zero; market is not initialized correctly.")
    ok(f"Router spot price: {spot_price}")

    step("Step 1: Ensure maker/taker have gas")
    min_eth = Web3.to_wei(0.05, "ether")
    for who, addr in [("maker", maker), ("taker", taker)]:
        bal = w3.eth.get_balance(addr)
        if bal < min_eth:
            top_up = min_eth - bal
            send_eth_transfer(w3, deployer_key, addr, top_up, f"topup-{who}")
        ok(f"{who} ETH balance: {w3.from_wei(w3.eth.get_balance(addr), 'ether')} ETH")

    step("Step 2: Deploy and register TwapEngine")
    if args.interval <= 0:
        die("--interval must be > 0.")
    if args.duration <= 0:
        die("--duration must be > 0.")
    if args.overlap_duration <= 0:
        die("--overlap-duration must be > 0.")
    if args.max_discount_bps < 0 or args.max_discount_bps > 10_000:
        die("--max-discount-bps must be in [0, 10000].")

    twap_engine_addr = deploy_contract_with_forge(
        "src/dex/TwapEngine.sol:TwapEngine",
        deployer_key,
        args.rpc_url,
        [router_addr, args.interval, args.max_discount_bps, args.discount_rate_scaled],
    )
    if not has_code(w3, twap_engine_addr):
        die("TwapEngine deployment failed (no runtime code).")
    ok(f"TwapEngine deployed: {twap_engine_addr}")

    send_contract_tx(w3, deployer_key, router.functions.registerEngine(twap_engine_addr), "registerEngine")
    is_engine = bool(router.functions.isEngine(twap_engine_addr).call())
    if not is_engine:
        die("Engine registration failed: router.isEngine(engine) is false.")
    ok("Engine registration verified.")

    engine = w3.eth.contract(address=twap_engine_addr, abi=engine_abi)

    step("Step 3: Mint and approve test balances")
    order_amount = int(args.order_amount) * 10**18
    overlap_order_amount = int(args.overlap_order_amount) * 10**18
    swap_amount_in = int(args.swap_amount_in) * 10**18
    if order_amount <= 0 or overlap_order_amount <= 0 or swap_amount_in <= 0:
        die("order/swap amounts must be > 0.")

    total_maker_sell = order_amount + overlap_order_amount
    # Maker sells token1 in this scenario (zeroForOne = false).
    send_contract_tx(w3, deployer_key, token1.functions.mint(maker, total_maker_sell), "mint maker token1")
    send_contract_tx(w3, deployer_key, token0.functions.mint(taker, swap_amount_in * 10), "mint taker token0")

    maker_t1_before = int(token1.functions.balanceOf(maker).call())
    taker_t0_before = int(token0.functions.balanceOf(taker).call())
    taker_t1_before = int(token1.functions.balanceOf(taker).call())
    router_t1_before = int(token1.functions.balanceOf(router_addr).call())

    send_contract_tx(w3, maker_key, token1.functions.approve(router_addr, 2**256 - 1), "maker approve token1")
    send_contract_tx(w3, taker_key, token0.functions.approve(router_addr, 2**256 - 1), "taker approve token0")

    step("Step 4: Submit overlapping TWAP orders")
    submit_primary_receipt = send_contract_tx(
        w3,
        maker_key,
        engine.functions.submitStream(market_id, False, args.duration, order_amount),
        "submitStream-primary",
        gas_cap=4_000_000,
    )
    primary_events = engine.events.StreamSubmitted().process_receipt(submit_primary_receipt, errors=DISCARD)
    if len(primary_events) != 1:
        die(f"Expected 1 primary StreamSubmitted event, got {len(primary_events)}")
    primary = primary_events[0]["args"]

    submit_overlap_receipt = send_contract_tx(
        w3,
        maker_key,
        engine.functions.submitStream(market_id, False, args.overlap_duration, overlap_order_amount),
        "submitStream-overlap",
        gas_cap=4_000_000,
    )
    overlap_events = engine.events.StreamSubmitted().process_receipt(submit_overlap_receipt, errors=DISCARD)
    if len(overlap_events) != 1:
        die(f"Expected 1 overlap StreamSubmitted event, got {len(overlap_events)}")
    overlap = overlap_events[0]["args"]

    order_id_1 = bytes(primary["orderId"])
    order_id_2 = bytes(overlap["orderId"])
    start_1 = int(primary["startEpoch"])
    exp_1 = int(primary["expiration"])
    start_2 = int(overlap["startEpoch"])
    exp_2 = int(overlap["expiration"])
    sell_rate_1 = int(primary["sellRate"])
    sell_rate_2 = int(overlap["sellRate"])
    expected_sell_rate_1 = (order_amount * 10**18) // args.duration
    expected_sell_rate_2 = (overlap_order_amount * 10**18) // args.overlap_duration

    if primary["owner"].lower() != maker.lower() or overlap["owner"].lower() != maker.lower():
        die("StreamSubmitted owner mismatch for overlapping orders.")
    if bool(primary["zeroForOne"]) or bool(overlap["zeroForOne"]):
        die("Expected both orders to be zeroForOne=false.")
    if sell_rate_1 != expected_sell_rate_1 or sell_rate_2 != expected_sell_rate_2:
        die(
            "Sell-rate mismatch: "
            f"primary expected={expected_sell_rate_1} got={sell_rate_1}, "
            f"overlap expected={expected_sell_rate_2} got={sell_rate_2}"
        )

    overlap_start = max(start_1, start_2)
    overlap_end = min(exp_1, exp_2)
    if overlap_start >= overlap_end:
        die(
            "Orders do not overlap. "
            f"primary=[{start_1},{exp_1}) overlap=[{start_2},{exp_2})"
        )

    ok(
        "Primary order: "
        f"id=0x{order_id_1.hex()} start={start_1} exp={exp_1} sellRate={sell_rate_1}"
    )
    ok(
        "Overlap order: "
        f"id=0x{order_id_2.hex()} start={start_2} exp={exp_2} sellRate={sell_rate_2}"
    )
    ok(f"Overlap window verified: [{overlap_start}, {overlap_end})")

    maker_t1_after_submit = int(token1.functions.balanceOf(maker).call())
    router_t1_after_submit = int(token1.functions.balanceOf(router_addr).call())
    if maker_t1_before - maker_t1_after_submit != total_maker_sell:
        die("Maker token1 debit mismatch after order submissions.")
    if router_t1_after_submit - router_t1_before != total_maker_sell:
        die("Router token1 credit mismatch after order submissions.")
    ok("Submit transfer verification passed (maker debited, router credited).")

    order1_view = engine.functions.streamOrders(market_id, order_id_1).call()
    order2_view = engine.functions.streamOrders(market_id, order_id_2).call()
    if Web3.to_checksum_address(order1_view[0]).lower() != maker.lower() or int(order1_view[1]) != sell_rate_1:
        die("Primary order storage mismatch.")
    if Web3.to_checksum_address(order2_view[0]).lower() != maker.lower() or int(order2_view[1]) != sell_rate_2:
        die("Overlap order storage mismatch.")
    ok("Order storage verified for both overlapping orders.")

    def expected_refund(order_sell_rate: int, order_start: int, order_exp: int, as_of_time: int) -> int:
        if as_of_time >= order_exp:
            return 0
        effective_time = as_of_time if as_of_time > order_start else order_start
        remaining = order_exp - effective_time
        return (order_sell_rate * remaining) // (10**18)

    step("Step 5: Verify progression and sell-rate correctness")
    if overlap_end - overlap_start < 6:
        die("Overlap window too small for progression checks; increase durations.")
    target_ts_a = overlap_start + 2
    target_ts_b = min(overlap_end - 1, target_ts_a + 3)
    if target_ts_b <= target_ts_a:
        die("Cannot build two progression checkpoints inside overlap window.")

    latest_ts = w3.eth.get_block("latest").timestamp
    while latest_ts < target_ts_a:
        advance_by = target_ts_a - latest_ts
        advance_time_or_blocks(w3, int(min(advance_by, 2)), deployer_key, fallback_steps=1)
        latest_ts = w3.eth.get_block("latest").timestamp
    ts_a = latest_ts

    owed1_a, refund1_a = engine.functions.getCancelOrderStateExact(market_id, order_id_1).call()
    owed2_a, refund2_a = engine.functions.getCancelOrderStateExact(market_id, order_id_2).call()
    exp_refund1_a = expected_refund(sell_rate_1, start_1, exp_1, ts_a)
    exp_refund2_a = expected_refund(sell_rate_2, start_2, exp_2, ts_a)
    if int(refund1_a) != exp_refund1_a or int(refund2_a) != exp_refund2_a:
        die(
            "Refund checkpoint A mismatch: "
            f"primary expected={exp_refund1_a} got={int(refund1_a)}, "
            f"overlap expected={exp_refund2_a} got={int(refund2_a)}"
        )
    ok(
        f"Checkpoint A @ts={ts_a}: "
        f"primary refund={int(refund1_a)} overlap refund={int(refund2_a)}"
    )

    latest_ts = w3.eth.get_block("latest").timestamp
    while latest_ts < target_ts_b:
        advance_by = target_ts_b - latest_ts
        advance_time_or_blocks(w3, int(min(advance_by, 2)), deployer_key, fallback_steps=1)
        latest_ts = w3.eth.get_block("latest").timestamp
    ts_b = latest_ts

    owed1_b, refund1_b = engine.functions.getCancelOrderStateExact(market_id, order_id_1).call()
    owed2_b, refund2_b = engine.functions.getCancelOrderStateExact(market_id, order_id_2).call()
    exp_refund1_b = expected_refund(sell_rate_1, start_1, exp_1, ts_b)
    exp_refund2_b = expected_refund(sell_rate_2, start_2, exp_2, ts_b)
    if int(refund1_b) != exp_refund1_b or int(refund2_b) != exp_refund2_b:
        die(
            "Refund checkpoint B mismatch: "
            f"primary expected={exp_refund1_b} got={int(refund1_b)}, "
            f"overlap expected={exp_refund2_b} got={int(refund2_b)}"
        )
    expected_drop_1 = exp_refund1_a - exp_refund1_b
    expected_drop_2 = exp_refund2_a - exp_refund2_b
    actual_drop_1 = int(refund1_a) - int(refund1_b)
    actual_drop_2 = int(refund2_a) - int(refund2_b)
    if actual_drop_1 != expected_drop_1 or actual_drop_2 != expected_drop_2:
        die(
            "Refund drop mismatch across checkpoints: "
            f"primary expectedDrop={expected_drop_1} got={actual_drop_1}, "
            f"overlap expectedDrop={expected_drop_2} got={actual_drop_2}"
        )
    ok(
        f"Checkpoint B @ts={ts_b}: "
        f"primary refund={int(refund1_b)} overlap refund={int(refund2_b)}"
    )
    ok(f"Sell-rate progression verified over Δt={ts_b - ts_a}s.")

    step("Step 6: Execute taker swap during overlap and verify aggregate behavior")
    swap_receipt = send_contract_tx(
        w3,
        taker_key,
        router.functions.swap(market_id, True, swap_amount_in, 1),
        "router.swap",
        gas_cap=5_000_000,
    )
    swap_events = router.events.SwapExecuted().process_receipt(swap_receipt, errors=DISCARD)
    if len(swap_events) != 1:
        die(f"Expected 1 SwapExecuted event, got {len(swap_events)}")
    swap_args = swap_events[0]["args"]
    swap_amount_out = int(swap_args["amountOut"])
    if swap_amount_out <= 0:
        die("Swap produced zero output.")

    ghost_events = engine.events.GhostTaken().process_receipt(swap_receipt, errors=DISCARD)
    if len(ghost_events) == 0:
        die("Expected at least one GhostTaken event in swap tx.")
    ghost_args = ghost_events[0]["args"]
    input_consumed = int(ghost_args["inputConsumed"])
    if int(ghost_args["filledOut"]) <= 0 or input_consumed <= 0:
        die("GhostTaken event indicates no meaningful fill.")
    ok(
        f"Swap intercept verified: amountOut={swap_amount_out}, "
        f"ghostFilled={int(ghost_args['filledOut'])}, inputConsumed={input_consumed}"
    )

    taker_t0_after = int(token0.functions.balanceOf(taker).call())
    taker_t1_after = int(token1.functions.balanceOf(taker).call())
    if taker_t0_before - taker_t0_after != swap_amount_in:
        die("Taker token0 spent mismatch after swap.")
    if taker_t1_after - taker_t1_before != swap_amount_out:
        die("Taker token1 received mismatch after swap.")
    ok("Taker balance deltas verified.")

    stream_pool_false = engine.functions.streamPools(market_id, False).call()
    current_sell_rate = int(stream_pool_false[0])
    expected_agg_sell_rate = sell_rate_1 + sell_rate_2
    if current_sell_rate != expected_agg_sell_rate:
        die(
            f"Aggregate sellRate mismatch: expected {expected_agg_sell_rate}, got {current_sell_rate}"
        )
    ok(f"Aggregate overlapping sellRate verified: {current_sell_rate}")

    step("Step 7: Claim both orders and verify proportional proceeds")
    owed1_post, refund1_post = engine.functions.getCancelOrderStateExact(market_id, order_id_1).call()
    owed2_post, refund2_post = engine.functions.getCancelOrderStateExact(market_id, order_id_2).call()
    if int(owed1_post) <= 0 or int(owed2_post) <= 0:
        die(
            "Expected positive owed proceeds for both overlapping orders after swap: "
            f"primary={int(owed1_post)} overlap={int(owed2_post)}"
        )
    ok(
        f"Post-swap preview: primary owed={int(owed1_post)} refund={int(refund1_post)}; "
        f"overlap owed={int(owed2_post)} refund={int(refund2_post)}"
    )

    maker_t0_before_claims = int(token0.functions.balanceOf(maker).call())

    claim1_before = int(token0.functions.balanceOf(maker).call())
    claim1_receipt = send_contract_tx(w3, maker_key, engine.functions.claimTokens(market_id, order_id_1), "claimTokens-primary")
    claim1_events = engine.events.TokensClaimed().process_receipt(claim1_receipt, errors=DISCARD)
    if len(claim1_events) == 0:
        die("Expected TokensClaimed event for primary order.")
    claim1_after = int(token0.functions.balanceOf(maker).call())
    claimed_1 = claim1_after - claim1_before

    claim2_before = int(token0.functions.balanceOf(maker).call())
    claim2_receipt = send_contract_tx(w3, maker_key, engine.functions.claimTokens(market_id, order_id_2), "claimTokens-overlap")
    claim2_events = engine.events.TokensClaimed().process_receipt(claim2_receipt, errors=DISCARD)
    if len(claim2_events) == 0:
        die("Expected TokensClaimed event for overlap order.")
    claim2_after = int(token0.functions.balanceOf(maker).call())
    claimed_2 = claim2_after - claim2_before

    total_claimed = claimed_1 + claimed_2
    maker_t0_after_claims = int(token0.functions.balanceOf(maker).call())
    if maker_t0_after_claims - maker_t0_before_claims != total_claimed:
        die("Maker token0 aggregate delta mismatch after claims.")
    if claimed_1 <= 0 or claimed_2 <= 0:
        die(f"Both overlapping orders should claim > 0. got primary={claimed_1}, overlap={claimed_2}")
    if total_claimed > input_consumed:
        die(f"Claimed more than taker input consumed. claimed={total_claimed}, consumed={input_consumed}")

    expected_claim_1 = (input_consumed * sell_rate_1) // (sell_rate_1 + sell_rate_2)
    expected_claim_2 = input_consumed - expected_claim_1
    ratio_tol = 10**15  # 0.001 token tolerance for integer rounding artifacts
    if abs(claimed_1 - expected_claim_1) > ratio_tol or abs(claimed_2 - expected_claim_2) > ratio_tol:
        die(
            "Overlapping claim split mismatch: "
            f"primary expected~{expected_claim_1} got={claimed_1}, "
            f"overlap expected~{expected_claim_2} got={claimed_2}"
        )
    ok(
        f"Claim split verified: primary={claimed_1}, overlap={claimed_2}, "
        f"total={total_claimed}, consumed={input_consumed}"
    )

    step("Step 8: Optional cancel broadcast and remainder checks")
    rem1_owed, rem1_refund = engine.functions.getCancelOrderStateExact(market_id, order_id_1).call()
    rem2_owed, rem2_refund = engine.functions.getCancelOrderStateExact(market_id, order_id_2).call()
    ok(
        f"Remaining previews: primary owed={int(rem1_owed)} refund={int(rem1_refund)}; "
        f"overlap owed={int(rem2_owed)} refund={int(rem2_refund)}"
    )

    if args.broadcast_cancel:
        maker_t1_before_cancel = int(token1.functions.balanceOf(maker).call())
        cancel_receipt = send_contract_tx(
            w3,
            maker_key,
            engine.functions.cancelOrder(market_id, order_id_1),
            "cancelOrder-primary",
        )
        cancel_events = engine.events.OrderCancelled().process_receipt(cancel_receipt, errors=DISCARD)
        if len(cancel_events) != 1:
            die(f"Expected 1 OrderCancelled event for primary order, got {len(cancel_events)}")
        cancel_refund = int(cancel_events[0]["args"]["refund"])
        maker_t1_after_cancel = int(token1.functions.balanceOf(maker).call())
        if maker_t1_after_cancel - maker_t1_before_cancel != cancel_refund:
            die("Primary cancel refund mismatch on maker token1 balance.")
        primary_after_cancel = engine.functions.streamOrders(market_id, order_id_1).call()
        if Web3.to_checksum_address(primary_after_cancel[0]) != Web3.to_checksum_address("0x0000000000000000000000000000000000000000"):
            die("Primary order owner not cleared after cancel.")
        if int(primary_after_cancel[1]) != 0:
            die("Primary order sellRate not cleared after cancel.")
        ok(f"Primary cancel broadcast verified: refund={cancel_refund}")
    else:
        try:
            engine.functions.cancelOrder(market_id, order_id_1).call({"from": maker})
            ok("Primary cancel dry-run succeeds (use --broadcast-cancel to execute).")
        except Exception as exc:
            info(f"Primary cancel dry-run reverts in this setup: {exc}")

    try:
        engine.functions.cancelOrder(market_id, order_id_2).call({"from": maker})
        ok("Overlap cancel dry-run succeeds at current state.")
    except Exception as exc:
        info(f"Overlap cancel dry-run reverts at current state (expected for final-active/no-liquidity path): {exc}")

    final_spot = int(router.functions.getSpotPrice(market_id).call())
    if final_spot <= 0:
        die("Final spot price invalid.")
    ok(f"Final spot price still valid: {final_spot}")

    step("Done")
    ok("TWAP flow passed: overlapping orders, progression, sell-rate checks, swap intercept, and claim split.")


if __name__ == "__main__":
    main()

