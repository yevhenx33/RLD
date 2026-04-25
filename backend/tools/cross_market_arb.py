#!/usr/bin/env python3
"""
Cross-market RLP/CDS arbitrage bot.

Simple bounded relative-value bot:
  - Reads RLP and CDS markets through the simulation GraphQL API.
  - Compares mark/index basis for each market.
  - If RLP is rich vs CDS, sells RLP and buys CDS.
  - If CDS is rich vs RLP, sells CDS and buys RLP.

The bot is intentionally conservative and defaults to dry-run. Set
ARB_DRY_RUN=false to send swaps.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from dataclasses import dataclass

from eth_account import Account
from web3 import Web3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.v4_swap import GhostRouterSwapExecutor


RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
INDEXER_URL = os.getenv("INDEXER_URL", "http://indexer:8080")
PRIVATE_KEY = os.getenv("ARB_KEY") or os.getenv("PRIVATE_KEY") or os.getenv("MM_KEY")
WHALE_KEY = os.getenv("WHALE_KEY")
ARB_INTERVAL_SECONDS = float(os.getenv("ARB_INTERVAL_SECONDS", "15"))
ARB_THRESHOLD_BPS = float(os.getenv("ARB_THRESHOLD_BPS", "75"))
ARB_TRADE_USD = float(os.getenv("ARB_TRADE_USD", "1000"))
ARB_DRY_RUN = os.getenv("ARB_DRY_RUN", "true").strip().lower() not in {"0", "false", "no"}
ARB_MIN_USDC = float(os.getenv("ARB_MIN_USDC", "10000"))
ARB_STATUS_FILE = Path(os.getenv("ARB_STATUS_FILE", "/tmp/cross_market_arb_status.json"))
ARB_MAX_CONSECUTIVE_FAILURES = int(os.getenv("ARB_MAX_CONSECUTIVE_FAILURES", "5"))
MAX_UINT256 = 2**256 - 1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("cross-market-arb")

STATS = {
    "cycles": 0,
    "opportunities": 0,
    "executed": 0,
    "failed": 0,
    "consecutive_failures": 0,
    "last_error": "",
}


MARKETS_QUERY = """
query CrossMarketArb {
  perpInfo: marketInfo(market: "perp")
  perpSnapshot: snapshot(market: "perp")
  cdsInfo: marketInfo(market: "cds")
  cdsSnapshot: snapshot(market: "cds")
}
"""

ERC20_ABI = [
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
        "inputs": [{"name": "to", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "type": "function",
        "name": "approve",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
        "outputs": [{"name": "", "type": "bool"}],
    },
]


@dataclass
class MarketView:
    key: str
    market_id: str
    pool_id: str
    collateral: str
    position: str
    token0: str
    token1: str
    ghost_router: str
    index: float
    mark: float
    tvl: float

    @property
    def basis(self) -> float:
        if self.index <= 0:
            return 0.0
        return (self.mark - self.index) / self.index

    def zero_for_buy_position(self) -> bool:
        return self.collateral.lower() == self.token0.lower()

    def zero_for_sell_position(self) -> bool:
        return self.position.lower() == self.token0.lower()


def _gql() -> dict:
    payload = json.dumps({"query": MARKETS_QUERY}).encode()
    req = urllib.request.Request(
        f"{INDEXER_URL.rstrip('/')}/graphql",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    return data["data"]


def _market(key: str, info: dict, snapshot: dict) -> MarketView:
    markets = info.get("markets") or {}
    entry = markets.get("cds" if key == "cds" else "perp") or {}
    token0 = entry.get("token0") or min(info["collateral"]["address"].lower(), info["position_token"]["address"].lower())
    token1 = entry.get("token1") or max(info["collateral"]["address"].lower(), info["position_token"]["address"].lower())
    return MarketView(
        key=key,
        market_id=info["marketId"],
        pool_id=info["poolId"],
        collateral=Web3.to_checksum_address(info["collateral"]["address"]),
        position=Web3.to_checksum_address(info["position_token"]["address"]),
        token0=Web3.to_checksum_address(token0),
        token1=Web3.to_checksum_address(token1),
        ghost_router=Web3.to_checksum_address(info["ghostRouter"]),
        index=float(snapshot["market"]["indexPrice"]),
        mark=float(snapshot["pool"]["markPrice"]),
        tvl=float(snapshot["pool"].get("tvlUsd") or 0),
    )


def load_markets() -> tuple[MarketView, MarketView]:
    data = _gql()
    return (
        _market("perp", data["perpInfo"], data["perpSnapshot"]),
        _market("cds", data["cdsInfo"], data["cdsSnapshot"]),
    )


def _raw(amount: float) -> int:
    return max(0, int(amount * 1_000_000))


def _balance(w3: Web3, token: str, account: str) -> int:
    return int(w3.eth.contract(address=Web3.to_checksum_address(token), abi=ERC20_ABI).functions.balanceOf(account).call())


def _write_status(status: str, **extra) -> None:
    payload = {
        "status": status,
        "updated_at": int(time.time()),
        "dry_run": ARB_DRY_RUN,
        "threshold_bps": ARB_THRESHOLD_BPS,
        "trade_usd": ARB_TRADE_USD,
        "max_consecutive_failures": ARB_MAX_CONSECUTIVE_FAILURES,
        **STATS,
        **extra,
    }
    tmp = ARB_STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(ARB_STATUS_FILE)


def _fund_usdc_if_needed(w3: Web3, account: str, usdc: str) -> None:
    if not WHALE_KEY:
        return
    target = int(ARB_MIN_USDC * 1e6)
    current = _balance(w3, usdc, account)
    if current >= target:
        return
    whale = Account.from_key(WHALE_KEY)
    token = w3.eth.contract(address=Web3.to_checksum_address(usdc), abi=ERC20_ABI)
    missing = target - current
    tx = token.functions.transfer(account, missing).build_transaction({
        "from": whale.address,
        "nonce": w3.eth.get_transaction_count(whale.address, "pending"),
        "gas": 120_000,
        "gasPrice": max(w3.eth.gas_price or 1_000_000_000, w3.to_wei("2", "gwei")),
        "chainId": w3.eth.chain_id,
    })
    signed = whale.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    if receipt.status == 1:
        log.info("Funded arb account with %.2f USDC", missing / 1e6)
    else:
        log.warning("USDC funding transaction reverted")


def _executor(w3: Web3, market: MarketView) -> GhostRouterSwapExecutor:
    return GhostRouterSwapExecutor(
        w3,
        market.token0,
        market.token1,
        market.ghost_router,
        market.pool_id,
        amount_out_minimum=1,
    )


def _quote_swap(executor: GhostRouterSwapExecutor, account: str, zero_for_one: bool, amount: int) -> int:
    return int(
        executor.router.functions.swap(
            executor.market_id,
            zero_for_one,
            amount,
            executor.amount_out_minimum,
        ).call({"from": account})
    )


def _execute_pair(w3: Web3, private_key: str, rich: MarketView, cheap: MarketView) -> bool:
    account = Account.from_key(private_key)
    trade_usd = min(ARB_TRADE_USD, max(ARB_TRADE_USD * 0.10, cheap.tvl * 0.001 if cheap.tvl else ARB_TRADE_USD))

    sell_position_raw = min(_raw(trade_usd / max(rich.mark, 1e-9)), _balance(w3, rich.position, account.address))
    buy_collateral_raw = min(_raw(trade_usd), _balance(w3, cheap.collateral, account.address))

    if sell_position_raw <= 0 or buy_collateral_raw <= 0:
        log.info(
            "Insufficient inventory for pair trade: sell_%s=%s buy_%s_collateral=%s",
            rich.key,
            sell_position_raw / 1e6,
            cheap.key,
            buy_collateral_raw / 1e6,
        )
        return False

    if ARB_DRY_RUN:
        log.info(
            "DRY RUN: sell %s %.6f position, buy %s %.2f collateral",
            rich.key,
            sell_position_raw / 1e6,
            cheap.key,
            buy_collateral_raw / 1e6,
        )
        return True

    sell_exec = _executor(w3, rich)
    buy_exec = _executor(w3, cheap)
    sell_zfo = rich.zero_for_sell_position()
    buy_zfo = cheap.zero_for_buy_position()

    # Preflight both legs before sending the first tx. Approvals are included
    # because GhostRouter.swap uses transferFrom during eth_call.
    if not sell_exec._ensure_approval(private_key, sell_exec._token_in(sell_zfo), sell_position_raw):
        log.error("Sell-leg approval failed")
        return False
    if not buy_exec._ensure_approval(private_key, buy_exec._token_in(buy_zfo), buy_collateral_raw):
        log.error("Buy-leg approval failed")
        return False

    try:
        sell_quote = _quote_swap(sell_exec, account.address, sell_zfo, sell_position_raw)
        buy_quote = _quote_swap(buy_exec, account.address, buy_zfo, buy_collateral_raw)
    except Exception as quote_error:
        log.error("Two-leg preflight failed: %s", quote_error)
        return False
    if sell_quote <= 0 or buy_quote <= 0:
        log.error("Two-leg preflight returned zero output sell=%s buy=%s", sell_quote, buy_quote)
        return False

    log.info(
        "Executing pair: sell %s %.6f -> quoted %.6f, buy %s %.2f -> quoted %.6f",
        rich.key,
        sell_position_raw / 1e6,
        sell_quote / 1e6,
        cheap.key,
        buy_collateral_raw / 1e6,
        buy_quote / 1e6,
    )

    sell_ok = sell_exec.execute_swap(private_key, sell_zfo, sell_position_raw)
    if not sell_ok:
        return False
    return buy_exec.execute_swap(private_key, buy_zfo, buy_collateral_raw)


def cycle(w3: Web3, private_key: str) -> None:
    STATS["cycles"] += 1
    perp, cds = load_markets()
    account = Account.from_key(private_key).address
    _fund_usdc_if_needed(w3, account, cds.collateral)
    spread = perp.basis - cds.basis
    spread_bps = spread * 10_000

    log.info(
        "RLP mark=%.6f index=%.6f basis=%.2fbps | CDS mark=%.6f index=%.6f basis=%.2fbps | spread=%.2fbps",
        perp.mark,
        perp.index,
        perp.basis * 10_000,
        cds.mark,
        cds.index,
        cds.basis * 10_000,
        spread_bps,
    )

    if abs(spread_bps) < ARB_THRESHOLD_BPS:
        log.info("No arb: |spread| %.2fbps < %.2fbps", abs(spread_bps), ARB_THRESHOLD_BPS)
        STATS["consecutive_failures"] = 0
        _write_status(
            "idle",
            spread_bps=spread_bps,
            perp_basis_bps=perp.basis * 10_000,
            cds_basis_bps=cds.basis * 10_000,
        )
        return

    STATS["opportunities"] += 1
    if spread > 0:
        log.info("RLP rich vs CDS: sell RLP, buy CDS")
        ok = _execute_pair(w3, private_key, rich=perp, cheap=cds)
        action = "sell_perp_buy_cds"
    else:
        log.info("CDS rich vs RLP: sell CDS, buy RLP")
        ok = _execute_pair(w3, private_key, rich=cds, cheap=perp)
        action = "sell_cds_buy_perp"

    if ok:
        STATS["executed"] += 1
        STATS["consecutive_failures"] = 0
        STATS["last_error"] = ""
        status = "executed" if not ARB_DRY_RUN else "dry_run"
    else:
        STATS["failed"] += 1
        STATS["consecutive_failures"] += 1
        status = "failed"
    _write_status(
        status,
        action=action,
        spread_bps=spread_bps,
        perp_basis_bps=perp.basis * 10_000,
        cds_basis_bps=cds.basis * 10_000,
        usdc_balance=_balance(w3, cds.collateral, account) / 1e6,
        wrlp_balance=_balance(w3, perp.position, account) / 1e6,
        wcds_balance=_balance(w3, cds.position, account) / 1e6,
    )

    if STATS["consecutive_failures"] >= ARB_MAX_CONSECUTIVE_FAILURES:
        raise RuntimeError(f"too many consecutive arb failures: {STATS['consecutive_failures']}")


def main() -> None:
    if not PRIVATE_KEY:
        raise SystemExit("ARB_KEY, PRIVATE_KEY, or MM_KEY must be set")

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise SystemExit(f"cannot connect to RPC {RPC_URL}")

    acct = Account.from_key(PRIVATE_KEY).address
    log.info("Cross-market arb started account=%s dry_run=%s threshold=%.2fbps trade_usd=%.2f", acct, ARB_DRY_RUN, ARB_THRESHOLD_BPS, ARB_TRADE_USD)
    _write_status("starting", account=acct)

    while True:
        try:
            cycle(w3, PRIVATE_KEY)
        except Exception as exc:
            STATS["failed"] += 1
            STATS["consecutive_failures"] += 1
            STATS["last_error"] = str(exc)
            _write_status("error", error=str(exc))
            log.exception("arb cycle failed: %s", exc)
            if STATS["consecutive_failures"] >= ARB_MAX_CONSECUTIVE_FAILURES:
                raise
        time.sleep(ARB_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
