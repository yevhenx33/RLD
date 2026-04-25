#!/usr/bin/env python3
"""
setup_cds_simulation.py
=======================

Separate runtime setup for a deployed CDS market.

This does not touch the existing perp market setup, mm-daemon, or chaos trader.
It expects `docker/deployment.json` to contain `markets.cds`, then:

  1. Funds an underwriter and protection buyer with raw USDC + ETH.
  2. Creates a CDS underwriter broker.
  3. Deposits raw USDC into the broker.
  4. Mints bounded wCDSUSDC debt against that collateral.
  5. Withdraws minted wCDSUSDC to the underwriter wallet for later LP/sale.

It intentionally does not seed V4 liquidity yet; that should be a follow-up once
the desired LP range and market depth are selected.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import math
from pathlib import Path
from typing import Any

from eth_account import Account
from eth_abi import encode as abi_encode
from web3 import Web3


SCRIPT_DIR = Path(__file__).resolve().parent
DOCKER_DIR = SCRIPT_DIR.parent
ENV_FILE = DOCKER_DIR / ".env"
DEPLOY_JSON = DOCKER_DIR / "deployment.json"
RPC_URL = os.environ.get("RPC_URL", "http://localhost:8545")

USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEFAULT_WHALE_KEY = "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"
BROKER_CREATED_TOPIC = "c418c83b1622e1e32aac5d6d2848134a7e89eb8e96c8514afd1757d25ee5ef71"


ERC20_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "balanceOf",
        "stateMutability": "view",
        "inputs": [{"name": "owner", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "transfer",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
]

BROKER_FACTORY_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "createBroker",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "salt", "type": "bytes32"}],
        "outputs": [{"name": "broker", "type": "address"}],
    }
]

BROKER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "modifyPosition",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "rawMarketId", "type": "bytes32"},
            {"name": "deltaCollateral", "type": "int256"},
            {"name": "deltaDebt", "type": "int256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "withdrawToken",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "recipient", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
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
        "outputs": [{"name": "", "type": "uint256"}],
    }
]

GHOST_ROUTER_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "getSpotPrice",
        "stateMutability": "view",
        "inputs": [{"name": "marketId", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "uint256"}],
    }
]

PERMIT2_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "token", "type": "address"},
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint160"},
            {"name": "expiration", "type": "uint48"},
        ],
        "outputs": [],
    }
]

POSM_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "modifyLiquidities",
        "stateMutability": "payable",
        "inputs": [
            {"name": "unlockData", "type": "bytes"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "type": "function",
        "name": "nextTokenId",
        "stateMutability": "view",
        "inputs": [],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "type": "function",
        "name": "ownerOf",
        "stateMutability": "view",
        "inputs": [{"name": "tokenId", "type": "uint256"}],
        "outputs": [{"name": "", "type": "address"}],
    },
]


def fail(message: str) -> None:
    print(f"[ERR] {message}", file=sys.stderr)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"[OK] {message}")


def info(message: str) -> None:
    print(f"[..] {message}")


def read_env_key(name: str) -> str | None:
    if name in os.environ:
        return os.environ[name].strip()
    if not ENV_FILE.exists():
        return None
    for raw_line in ENV_FILE.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return None


def normalize_key(value: str | None, label: str) -> str:
    if not value:
        fail(f"{label} is required")
    key = value if value.startswith("0x") else f"0x{value}"
    if len(key) != 66:
        fail(f"{label} must be a 32-byte private key")
    return key


def checksum(addr: str) -> str:
    return Web3.to_checksum_address(addr)


def has_code(w3: Web3, addr: str) -> bool:
    return len(w3.eth.get_code(checksum(addr))) > 0


def raw_usdc(amount: float) -> int:
    return int(round(amount * 1_000_000))


def human_from_raw(amount: int) -> float:
    return amount / 1_000_000


def send_tx(
    w3: Web3,
    private_key: str,
    to: str,
    data: bytes | str = b"",
    *,
    value: int = 0,
    gas: int = 1_000_000,
    label: str,
):
    account = Account.from_key(private_key)
    nonce = w3.eth.get_transaction_count(account.address, "pending")
    gas_price = max(w3.eth.gas_price or 1_000_000_000, Web3.to_wei(2, "gwei"))
    tx = {
        "to": checksum(to),
        "data": data,
        "value": value,
        "gas": gas,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
    }
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    if receipt.status != 1:
        fail(f"{label} reverted: tx={tx_hash.hex()}")
    ok(f"{label}: tx={tx_hash.hex()} gas={receipt.gasUsed:,}")
    time.sleep(float(os.getenv("TX_DELAY_SECONDS", "1")))
    return receipt


def parse_broker_from_receipt(receipt: Any) -> str:
    for log in receipt.logs:
        topics = [topic.hex() for topic in log.topics]
        if topics and topics[0] == BROKER_CREATED_TOPIC:
            # Broker is indexed topic1.
            return checksum("0x" + topics[1][-40:])
    fail("BrokerCreated event not found in receipt")
    return ZERO_ADDRESS


def price_to_aligned_tick(price: float, spacing: int, *, up: bool) -> int:
    if price <= 0:
        fail(f"invalid price for tick conversion: {price}")
    raw = math.log(price) / math.log(1.0001)
    fn = math.ceil if up else math.floor
    return int(fn(raw / spacing) * spacing)


def calculate_lp_amounts(
    *,
    collateral: str,
    position: str,
    minted_position_raw: int,
    semantic_price: float,
    semantic_min: float,
    semantic_max: float,
    tick_spacing: int,
) -> dict[str, Any]:
    """Calculate balanced V4 LP amounts for a semantic price range.

    Semantic price is collateral per position token. V4 raw price is token1/token0.
    """
    if semantic_min <= 0 or semantic_max <= semantic_min:
        fail("invalid semantic price range")

    token0, token1 = sorted([checksum(collateral), checksum(position)], key=lambda a: int(a, 16))
    collateral_is_token0 = token0.lower() == checksum(collateral).lower()
    position_is_token0 = token0.lower() == checksum(position).lower()

    if collateral_is_token0:
        raw_low = 1.0 / semantic_max
        raw_high = 1.0 / semantic_min
        raw_current = 1.0 / semantic_price
    else:
        raw_low = semantic_min
        raw_high = semantic_max
        raw_current = semantic_price

    if not (raw_low < raw_current < raw_high):
        fail(
            "current price is outside LP range: "
            f"raw_low={raw_low}, raw_current={raw_current}, raw_high={raw_high}"
        )

    tick_lower = price_to_aligned_tick(raw_low, tick_spacing, up=False)
    tick_upper = price_to_aligned_tick(raw_high, tick_spacing, up=True)

    sqrt_p = math.sqrt(raw_current)
    sqrt_l = math.sqrt(math.pow(1.0001, tick_lower))
    sqrt_u = math.sqrt(math.pow(1.0001, tick_upper))
    position_amount = human_from_raw(minted_position_raw)

    if position_is_token0:
        denom = (1.0 / sqrt_p) - (1.0 / sqrt_u)
        liquidity = position_amount / denom
        amount0 = position_amount
        amount1 = liquidity * (sqrt_p - sqrt_l)
    else:
        denom = sqrt_p - sqrt_l
        liquidity = position_amount / denom
        amount0 = liquidity * ((1.0 / sqrt_p) - (1.0 / sqrt_u))
        amount1 = position_amount

    amount0_raw = raw_usdc(amount0)
    amount1_raw = raw_usdc(amount1)
    if amount0_raw <= 0 or amount1_raw <= 0:
        fail("calculated LP amount is zero")

    return {
        "token0": token0,
        "token1": token1,
        "amount0_raw": amount0_raw,
        "amount1_raw": amount1_raw,
        "liquidity": int(liquidity * 1_000_000),
        "tick_lower": tick_lower,
        "tick_upper": tick_upper,
        "raw_current": raw_current,
        "semantic_price": semantic_price,
    }


def ensure_eth(w3: Web3, whale_key: str, recipient: str, min_eth: float) -> None:
    balance = w3.eth.get_balance(checksum(recipient))
    target = Web3.to_wei(min_eth, "ether")
    if balance >= target:
        ok(f"{recipient} ETH balance sufficient: {w3.from_wei(balance, 'ether')} ETH")
        return
    missing = target - balance
    send_tx(
        w3,
        whale_key,
        recipient,
        value=missing,
        gas=21_000,
        label=f"fund ETH {recipient}",
    )


def ensure_usdc(w3: Web3, whale_key: str, token, recipient: str, target_amount: int) -> None:
    current = int(token.functions.balanceOf(checksum(recipient)).call())
    if current >= target_amount:
        ok(f"{recipient} USDC balance sufficient: ${current / 1e6:,.2f}")
        return
    missing = target_amount - current
    calldata = token.functions.transfer(checksum(recipient), missing).build_transaction({"from": Account.from_key(whale_key).address})["data"]
    send_tx(
        w3,
        whale_key,
        token.address,
        data=calldata,
        gas=120_000,
        label=f"fund USDC {recipient}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up actors for a deployed CDS market.")
    parser.add_argument("--deployment-json", default=str(DEPLOY_JSON))
    parser.add_argument("--rpc-url", default=RPC_URL)
    parser.add_argument("--underwriter-key", default=None)
    parser.add_argument("--buyer-key", default=None)
    parser.add_argument("--whale-key", default=None)
    parser.add_argument("--underwriter-fund", type=float, default=60_000_000.0)
    parser.add_argument("--underwriter-collateral", type=float, default=50_000_000.0)
    parser.add_argument("--mint-notional", type=float, default=500_000.0)
    parser.add_argument("--buyer-capital", type=float, default=10_000_000.0)
    parser.add_argument("--range-min", type=float, default=2.0)
    parser.add_argument("--range-max", type=float, default=20.0)
    parser.add_argument("--lp-only", action="store_true", help="Skip funding/broker/mint and only add LP from wallet balances.")
    parser.add_argument("--gas-eth", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--out", default=str(SCRIPT_DIR / "cds-simulation-setup-report.json"))
    args = parser.parse_args()

    deploy_path = Path(args.deployment_json)
    if not deploy_path.exists():
        fail(f"deployment.json not found: {deploy_path}")
    deploy = json.loads(deploy_path.read_text())
    cds = (deploy.get("markets") or {}).get("cds")
    if not isinstance(cds, dict):
        fail("deployment.json missing markets.cds; deploy CDS market first")

    underwriter_key = normalize_key(
        args.underwriter_key or read_env_key("CDS_UNDERWRITER_KEY") or read_env_key("USER_A_KEY"),
        "underwriter key",
    )
    buyer_key = normalize_key(
        args.buyer_key or read_env_key("CDS_BUYER_KEY") or read_env_key("USER_B_KEY"),
        "buyer key",
    )
    whale_key = normalize_key(args.whale_key or read_env_key("WHALE_KEY") or DEFAULT_WHALE_KEY, "whale key")

    underwriter = Account.from_key(underwriter_key).address
    buyer = Account.from_key(buyer_key).address
    whale = Account.from_key(whale_key).address

    w3 = Web3(Web3.HTTPProvider(args.rpc_url))
    if not w3.is_connected():
        fail(f"cannot connect to RPC: {args.rpc_url}")

    collateral = checksum(cds["collateral_token"])
    position = checksum(cds["position_token"])
    broker_factory_addr = checksum(cds["broker_factory"])
    market_id = cds["market_id"]
    rate_oracle = checksum(cds["rate_oracle"])
    ghost_router = checksum(deploy["ghost_router"])
    pool_id = cds["pool_id"]
    pool_fee = int(cds.get("pool_fee", 500))
    tick_spacing = int(cds.get("tick_spacing", 5))
    underwriter_fund_raw = raw_usdc(args.underwriter_fund)
    underwriter_collateral_raw = raw_usdc(args.underwriter_collateral)
    buyer_capital_raw = raw_usdc(args.buyer_capital)

    info(f"chainId={w3.eth.chain_id} block={w3.eth.block_number}")
    info(f"underwriter={underwriter}")
    info(f"buyer={buyer}")
    info(f"whale={whale}")
    info(f"CDS market={market_id}")
    oracle = w3.eth.contract(address=rate_oracle, abi=ORACLE_ABI)
    index_price = int(
        oracle.functions.getIndexPrice(
            checksum(cds["underlying_pool"]),
            checksum(cds["underlying_token"]),
        ).call()
    ) / 1e18
    if index_price <= 0:
        fail("oracle index price is non-positive")

    mint_amount = raw_usdc(args.mint_notional / index_price)
    lp_plan = calculate_lp_amounts(
        collateral=collateral,
        position=position,
        minted_position_raw=mint_amount,
        semantic_price=index_price,
        semantic_min=args.range_min,
        semantic_max=args.range_max,
        tick_spacing=tick_spacing,
    )

    required_collateral_wallet = (
        lp_plan["amount0_raw"]
        if lp_plan["token0"].lower() == collateral.lower()
        else lp_plan["amount1_raw"]
    )
    if required_collateral_wallet > underwriter_fund_raw - underwriter_collateral_raw:
        fail(
            "LP collateral requirement exceeds wallet remainder after broker collateral deposit: "
            f"required=${required_collateral_wallet/1e6:,.2f}, "
            f"available=${(underwriter_fund_raw-underwriter_collateral_raw)/1e6:,.2f}"
        )

    info(
        f"underwriter fund=${args.underwriter_fund:,.2f}, "
        f"broker collateral=${args.underwriter_collateral:,.2f}, "
        f"mint notional=${args.mint_notional:,.2f}"
    )
    info(f"index price=${index_price:.6f}, mint={mint_amount / 1e6:,.6f} {cds.get('position_symbol', 'wCDS')}")
    info(
        "LP plan: "
        f"token0={lp_plan['token0']} amount0={lp_plan['amount0_raw']/1e6:,.6f}, "
        f"token1={lp_plan['token1']} amount1={lp_plan['amount1_raw']/1e6:,.6f}, "
        f"ticks=[{lp_plan['tick_lower']},{lp_plan['tick_upper']}], "
        f"liquidity={lp_plan['liquidity']}"
    )

    if args.dry_run:
        ok("dry run complete; no transactions sent")
        return

    for label, addr in {
        "collateral token": collateral,
        "position token": position,
        "broker factory": broker_factory_addr,
    }.items():
        if not has_code(w3, addr):
            fail(f"{label} has no code: {addr}")

    usdc = w3.eth.contract(address=collateral, abi=ERC20_ABI)
    factory = w3.eth.contract(address=broker_factory_addr, abi=BROKER_FACTORY_ABI)
    broker_token = w3.eth.contract(address=position, abi=ERC20_ABI)

    broker = None
    broker_usdc = 0
    ensure_eth(w3, whale_key, underwriter, args.gas_eth)
    if not args.lp_only:
        ensure_eth(w3, whale_key, buyer, args.gas_eth)
        ensure_usdc(w3, whale_key, usdc, underwriter, underwriter_fund_raw)
        ensure_usdc(w3, whale_key, usdc, buyer, buyer_capital_raw)

        salt = "0x" + secrets.token_hex(32)
        create_data = factory.functions.createBroker(salt).build_transaction({"from": underwriter})["data"]
        receipt = send_tx(
            w3,
            underwriter_key,
            broker_factory_addr,
            data=create_data,
            gas=1_500_000,
            label="create CDS underwriter broker",
        )
        broker = parse_broker_from_receipt(receipt)
        ok(f"underwriter broker: {broker}")

        transfer_data = usdc.functions.transfer(broker, underwriter_collateral_raw).build_transaction({"from": underwriter})["data"]
        send_tx(
            w3,
            underwriter_key,
            collateral,
            data=transfer_data,
            gas=120_000,
            label="deposit raw USDC to underwriter broker",
        )

        broker_contract = w3.eth.contract(address=broker, abi=BROKER_ABI)
        mint_data = broker_contract.functions.modifyPosition(
            market_id,
            0,
            mint_amount,
        ).build_transaction({"from": underwriter})["data"]
        send_tx(
            w3,
            underwriter_key,
            broker,
            data=mint_data,
            gas=3_000_000,
            label="mint bounded wCDSUSDC",
        )

        withdraw_data = broker_contract.functions.withdrawToken(
            position,
            underwriter,
            mint_amount,
        ).build_transaction({"from": underwriter})["data"]
        send_tx(
            w3,
            underwriter_key,
            broker,
            data=withdraw_data,
            gas=500_000,
            label="withdraw minted wCDSUSDC",
        )

    broker_usdc = int(usdc.functions.balanceOf(broker).call()) if broker else 0
    if not args.lp_only and broker_usdc < underwriter_collateral_raw:
        fail(f"broker USDC below expected: {broker_usdc} < {underwriter_collateral_raw}")

    underwriter_wcds = int(broker_token.functions.balanceOf(underwriter).call())
    underwriter_usdc = int(usdc.functions.balanceOf(underwriter).call())
    if underwriter_wcds < mint_amount:
        fail(f"underwriter wCDS below expected: {underwriter_wcds} < {mint_amount}")
    if underwriter_usdc < required_collateral_wallet:
        fail(f"underwriter USDC below LP requirement: {underwriter_usdc} < {required_collateral_wallet}")

    permit2_addr = checksum(deploy["permit2"])
    posm_addr = checksum(deploy["v4_position_manager"])
    permit2 = w3.eth.contract(address=permit2_addr, abi=PERMIT2_ABI)
    posm = w3.eth.contract(address=posm_addr, abi=POSM_ABI)

    max_u256 = 2**256 - 1
    max_u160 = 2**160 - 1
    max_u48 = 2**48 - 1
    deadline = 2**256 - 1

    for token_addr in [lp_plan["token0"], lp_plan["token1"]]:
        token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
        approve_data = token.functions.approve(permit2_addr, max_u256).build_transaction({"from": underwriter})["data"]
        send_tx(
            w3,
            underwriter_key,
            token_addr,
            data=approve_data,
            gas=120_000,
            label=f"approve Permit2 {token_addr}",
        )
        permit2_data = permit2.functions.approve(
            token_addr,
            posm_addr,
            max_u160,
            max_u48,
        ).build_transaction({"from": underwriter})["data"]
        send_tx(
            w3,
            underwriter_key,
            permit2_addr,
            data=permit2_data,
            gas=120_000,
            label=f"Permit2 approve POSM {token_addr}",
        )

    next_token_id = int(posm.functions.nextTokenId().call())
    pool_key = (lp_plan["token0"], lp_plan["token1"], pool_fee, tick_spacing, ZERO_ADDRESS)
    actions = bytes([0x0B, 0x0B, 0x05, 0x11])
    params = [
        abi_encode(["address", "uint256", "bool"], [lp_plan["token0"], lp_plan["amount0_raw"], True]),
        abi_encode(["address", "uint256", "bool"], [lp_plan["token1"], lp_plan["amount1_raw"], True]),
        abi_encode(
            [
                "(address,address,uint24,int24,address)",
                "int24",
                "int24",
                "uint128",
                "uint128",
                "address",
                "bytes",
            ],
            [
                pool_key,
                lp_plan["tick_lower"],
                lp_plan["tick_upper"],
                max(lp_plan["amount0_raw"] * 2, 1),
                max(lp_plan["amount1_raw"] * 2, 1),
                underwriter,
                b"",
            ],
        ),
        abi_encode(["address", "address", "address"], [lp_plan["token0"], lp_plan["token1"], underwriter]),
    ]
    unlock_data = abi_encode(["bytes", "bytes[]"], [actions, params])
    modify_data = posm.functions.modifyLiquidities(
        "0x" + unlock_data.hex(),
        deadline,
    ).build_transaction({"from": underwriter})["data"]
    send_tx(
        w3,
        underwriter_key,
        posm_addr,
        data=modify_data,
        gas=2_500_000,
        label="mint CDS V4 LP position",
    )
    lp_owner = checksum(posm.functions.ownerOf(next_token_id).call())
    if lp_owner.lower() != underwriter.lower():
        fail(f"LP NFT owner mismatch: {lp_owner} != {underwriter}")

    final_underwriter_wcds = int(broker_token.functions.balanceOf(underwriter).call())
    final_underwriter_usdc = int(usdc.functions.balanceOf(underwriter).call())
    report = {
        "market_id": market_id,
        "underwriter": underwriter,
        "buyer": buyer,
        "broker": broker,
        "collateral_token": collateral,
        "position_token": position,
        "funded_underwriter_raw": str(underwriter_fund_raw),
        "locked_collateral_raw": str(broker_usdc),
        "minted_position_raw": str(mint_amount),
        "underwriter_usdc_balance_raw": str(final_underwriter_usdc),
        "underwriter_position_balance_raw": str(final_underwriter_wcds),
        "buyer_usdc_balance_raw": str(int(usdc.functions.balanceOf(buyer).call())),
        "lp_token_id": str(next_token_id),
        "lp_plan": {
            "pool_id": pool_id,
            "token0": lp_plan["token0"],
            "token1": lp_plan["token1"],
            "amount0_raw": str(lp_plan["amount0_raw"]),
            "amount1_raw": str(lp_plan["amount1_raw"]),
            "liquidity": str(lp_plan["liquidity"]),
            "tick_lower": lp_plan["tick_lower"],
            "tick_upper": lp_plan["tick_upper"],
            "semantic_price": lp_plan["semantic_price"],
        },
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n")
    ok(f"CDS simulation setup report written: {out_path}")


if __name__ == "__main__":
    main()
