"""
verify_indexer.py — Verification script for the indexer's normalized broker state.

Snapshots Anvil on-chain state and compares against the indexed DB values.
Uses web3.py for RPC calls and asyncpg for DB reads.

Usage:
    cd backend/indexers
    python verify_indexer.py [--rpc-url URL] [--db-dsn DSN]
"""
import asyncio
import argparse
import sys
import os

import asyncpg
from web3 import Web3

# ── ABI fragments for on-chain reads ──────────────────────────────────────

ERC20_BALANCE_OF = [{
    "name": "balanceOf",
    "type": "function",
    "inputs": [{"name": "account", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
    "stateMutability": "view",
}]

BROKER_FROZEN_ABI = [{
    "name": "frozen",
    "type": "function",
    "inputs": [],
    "outputs": [{"name": "", "type": "bool"}],
    "stateMutability": "view",
}]

BROKER_OPERATORS_ABI = [{
    "name": "operators",
    "type": "function",
    "inputs": [{"name": "", "type": "address"}],
    "outputs": [{"name": "", "type": "bool"}],
    "stateMutability": "view",
}]

POSITION_MANAGER_OWNER_OF = [{
    "name": "ownerOf",
    "type": "function",
    "inputs": [{"name": "tokenId", "type": "uint256"}],
    "outputs": [{"name": "", "type": "address"}],
    "stateMutability": "view",
}]

POSITION_MANAGER_INFO = [{
    "name": "getPoolAndPositionInfo",
    "type": "function",
    "inputs": [{"name": "tokenId", "type": "uint256"}],
    "outputs": [
        {"name": "poolKey", "type": "tuple", "components": [
            {"name": "currency0", "type": "address"},
            {"name": "currency1", "type": "address"},
            {"name": "fee", "type": "uint24"},
            {"name": "tickSpacing", "type": "int24"},
            {"name": "hooks", "type": "address"},
        ]},
        {"name": "info", "type": "uint256"},  # packed PositionInfo
    ],
    "stateMutability": "view",
}]

# RLDCore position info
RLDCORE_POSITIONS = [{
    "name": "positions",
    "type": "function",
    "inputs": [
        {"name": "marketId", "type": "bytes32"},
        {"name": "user", "type": "address"},
    ],
    "outputs": [
        {"name": "debtPrincipal", "type": "uint128"},
        {"name": "lastNormFactor", "type": "uint128"},
    ],
    "stateMutability": "view",
}]


class Colors:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def ok(label: str, count: int, total: int, detail: str = ""):
    status = f"{Colors.GREEN}✓{Colors.RESET}" if count == total else f"{Colors.RED}✗{Colors.RESET}"
    d = f"  ({detail})" if detail else ""
    print(f"  {status} {label}: {count}/{total} match{d}")
    return count == total


async def verify(rpc_url: str, db_dsn: str):
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"{Colors.RED}ERROR: Cannot connect to RPC at {rpc_url}{Colors.RESET}")
        sys.exit(1)

    conn = await asyncpg.connect(db_dsn)

    # ── Load market config ──────────────────────────────────────────────
    market = await conn.fetchrow("SELECT * FROM markets LIMIT 1")
    if not market:
        print(f"{Colors.RED}ERROR: No markets in DB{Colors.RESET}")
        sys.exit(1)

    market_id = market["market_id"]
    wausdc_addr = Web3.to_checksum_address(market["wausdc"])
    wrlp_addr = Web3.to_checksum_address(market["wrlp"])
    posm_addr = market.get("v4_position_manager")

    wausdc = w3.eth.contract(address=wausdc_addr, abi=ERC20_BALANCE_OF)
    wrlp = w3.eth.contract(address=wrlp_addr, abi=ERC20_BALANCE_OF)

    latest_block = w3.eth.block_number
    print(f"\n{Colors.BOLD}══ RLD Indexer Verification ══{Colors.RESET}")
    print(f"  Block: {latest_block}")
    print(f"  Market: {market_id[:20]}...")
    print(f"  waUSDC: {wausdc_addr}")
    print(f"  wRLP: {wrlp_addr}")
    print()

    all_passed = True

    # ── 1. Verify Broker Balances ───────────────────────────────────────
    brokers = await conn.fetch("SELECT * FROM brokers WHERE market_id = $1", market_id)
    print(f"{Colors.BOLD}Brokers ({len(brokers)}):{Colors.RESET}")

    broker_match = 0
    for b in brokers:
        addr = Web3.to_checksum_address(b["address"])
        db_wausdc = int(b["wausdc_balance"] or "0")
        db_wrlp = int(b["wrlp_balance"] or "0")
        db_debt = int(b["debt_principal"] or "0")
        db_frozen = bool(b["is_frozen"])

        # On-chain reads
        chain_wausdc = wausdc.functions.balanceOf(addr).call()
        chain_wrlp = wrlp.functions.balanceOf(addr).call()

        broker_contract = w3.eth.contract(address=addr, abi=BROKER_FROZEN_ABI)
        try:
            chain_frozen = broker_contract.functions.frozen().call()
        except Exception:
            chain_frozen = False  # Contract may not have frozen()

        mismatches = []
        if db_wausdc != chain_wausdc:
            mismatches.append(f"wausdc: db={db_wausdc} chain={chain_wausdc}")
        if db_wrlp != chain_wrlp:
            mismatches.append(f"wrlp: db={db_wrlp} chain={chain_wrlp}")
        if db_frozen != chain_frozen:
            mismatches.append(f"frozen: db={db_frozen} chain={chain_frozen}")

        if mismatches:
            print(f"  {Colors.RED}✗{Colors.RESET} {b['address'][:18]}... {', '.join(mismatches)}")
        else:
            broker_match += 1
            print(f"  {Colors.GREEN}✓{Colors.RESET} {b['address'][:18]}... wausdc={db_wausdc} wrlp={db_wrlp} debt={db_debt} frozen={db_frozen}")

    all_passed &= ok("Brokers", broker_match, len(brokers), "wausdc, wrlp, frozen")
    print()

    # ── 2. Verify Operators ─────────────────────────────────────────────
    operators = await conn.fetch("SELECT * FROM broker_operators")
    print(f"{Colors.BOLD}Operators ({len(operators)}):{Colors.RESET}")

    op_match = 0
    for op in operators:
        broker_addr = Web3.to_checksum_address(op["broker_address"])
        operator_addr = Web3.to_checksum_address(op["operator"])
        broker_contract = w3.eth.contract(address=broker_addr, abi=BROKER_OPERATORS_ABI)
        try:
            chain_active = broker_contract.functions.operators(operator_addr).call()
        except Exception:
            chain_active = None

        if chain_active is True:
            op_match += 1
            print(f"  {Colors.GREEN}✓{Colors.RESET} {op['broker_address'][:18]}... → {op['operator'][:18]}...")
        else:
            print(f"  {Colors.RED}✗{Colors.RESET} {op['broker_address'][:18]}... → {op['operator'][:18]}... chain={chain_active}")

    if len(operators) == 0:
        print(f"  {Colors.YELLOW}(no operators in DB){Colors.RESET}")
    else:
        all_passed &= ok("Operators", op_match, len(operators))
    print()

    # ── 3. Verify LP Positions ──────────────────────────────────────────
    lps = await conn.fetch("SELECT * FROM lp_positions")
    print(f"{Colors.BOLD}LP Positions ({len(lps)}):{Colors.RESET}")

    lp_match = 0
    if posm_addr:
        posm = w3.eth.contract(
            address=Web3.to_checksum_address(posm_addr),
            abi=POSITION_MANAGER_OWNER_OF + POSITION_MANAGER_INFO
        )
        for lp in lps:
            token_id = int(lp["token_id"])
            db_owner = lp["owner"].lower()

            try:
                chain_owner = posm.functions.ownerOf(token_id).call().lower()
            except Exception:
                chain_owner = None

            mismatches = []
            if chain_owner and db_owner != chain_owner:
                mismatches.append(f"owner: db={db_owner[:18]} chain={chain_owner[:18]}")

            if mismatches:
                print(f"  {Colors.RED}✗{Colors.RESET} tokenId={token_id} {', '.join(mismatches)}")
            else:
                lp_match += 1
                tl = lp["tick_lower"] if lp["tick_lower"] is not None else "?"
                tu = lp["tick_upper"] if lp["tick_upper"] is not None else "?"
                print(f"  {Colors.GREEN}✓{Colors.RESET} tokenId={token_id} owner={db_owner[:18]}... ticks=[{tl},{tu}]")
    else:
        print(f"  {Colors.YELLOW}(v4_position_manager not configured in DB){Colors.RESET}")

    if len(lps) == 0:
        print(f"  {Colors.YELLOW}(no LP positions in DB){Colors.RESET}")
    else:
        all_passed &= ok("LP Positions", lp_match, len(lps), "owner, ticks")
    print()

    # ── 4. Verify TWAMM Orders ──────────────────────────────────────────
    orders = await conn.fetch("SELECT * FROM twamm_orders")
    print(f"{Colors.BOLD}TWAMM Orders ({len(orders)}):{Colors.RESET}")

    for o in orders:
        status_icon = {
            "active": Colors.GREEN + "●" + Colors.RESET,
            "cancelled": Colors.YELLOW + "○" + Colors.RESET,
            "claimed": Colors.GREEN + "◉" + Colors.RESET,
        }.get(o["status"], "?")
        print(f"  {status_icon} orderId={o['order_id'][:18]}... owner={o['owner'][:18]}... "
              f"status={o['status']} zfo={o['zero_for_one']} amtIn={o['amount_in']}")

    if len(orders) == 0:
        print(f"  {Colors.YELLOW}(no TWAMM orders in DB){Colors.RESET}")
    else:
        # Count orders from events for cross-check
        event_count = await conn.fetchval(
            "SELECT COUNT(*) FROM events WHERE event_name IN ('SubmitOrder', 'TwammOrderSubmitted')"
        )
        all_passed &= ok("TWAMM Orders", len(orders), len(orders),
                         f"db={len(orders)} submit_events={event_count}")
    print()

    # ── Summary ─────────────────────────────────────────────────────────
    if all_passed:
        print(f"{Colors.BOLD}{Colors.GREEN}━━━ ALL CHECKS PASSED ━━━{Colors.RESET}")
    else:
        print(f"{Colors.BOLD}{Colors.RED}━━━ SOME CHECKS FAILED ━━━{Colors.RESET}")
        sys.exit(1)

    await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Verify indexer state against Anvil on-chain state")
    parser.add_argument("--rpc-url", default=os.getenv("RPC_URL", "http://localhost:8545"))
    parser.add_argument("--db-dsn", default=os.getenv("DATABASE_URL", "postgresql://rld:rld@localhost:5432/rld"))
    args = parser.parse_args()

    asyncio.run(verify(args.rpc_url, args.db_dsn))


if __name__ == "__main__":
    main()
