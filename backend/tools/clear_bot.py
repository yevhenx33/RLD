#!/usr/bin/env python3
"""
JTM Clear Auction Arbitrage Bot

Monitors accrued ghost balances in the JTM TWAMM hook and calls clear()
to buy them at a time-decaying discount (Layer 3 of the waterfall).

The bot:
  1. Polls getStreamState() every POLL_INTERVAL seconds
  2. When accrued tokens exceed MIN_CLEAR_USD, attempts to clear
  3. Pays at TWAP price minus discount (discount increases over time)
  4. Profits from the discount spread

Environment:
    CLEAR_BOT_KEY     - Private key (default: Anvil account 5)
    RPC_URL           - RPC endpoint (default: http://localhost:8545)
    INDEXER_URL       - Indexer URL for market-info (default: http://localhost:8080)
"""

import os
import sys
import time
import json
import logging
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# ── Logging ─────────────────────────────────────────────────────────

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'WARNING': '\033[93m', 'INFO': '\033[96m', 'ERROR': '\033[91m',
        'RESET': '\033[0m', 'GREEN': '\033[92m',
    }
    def format(self, record):
        msg = super().format(record)
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        return f"{color}{msg}{self.COLORS['RESET']}"

file_handler = logging.FileHandler('/tmp/clear_bot.log')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))
console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter('%(asctime)s - %(message)s', '%H:%M:%S'))

logger = logging.getLogger("clear_bot")
logger.addHandler(file_handler)
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

# ── Config ──────────────────────────────────────────────────────────

load_dotenv("/home/ubuntu/RLD/.env")

RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
INDEXER_URL = os.getenv("INDEXER_URL", "http://localhost:8080")
# Default: Anvil account 5
PRIVATE_KEY = os.getenv(
    "CLEAR_BOT_KEY",
    "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"
)

POLL_INTERVAL = 2        # seconds between checks (aggressive)
MIN_CLEAR_USD = 0.001    # minimum $ value to bother clearing
MIN_DISCOUNT_BPS = 1     # minimum discount to accept (0.01%)
MAX_GAS = 500_000        # gas limit for clear()
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# ── ABIs ────────────────────────────────────────────────────────────

JTM_ABI = json.loads("""[
  {
    "inputs": [
      {"components": [{"name":"currency0","type":"address"},{"name":"currency1","type":"address"},{"name":"fee","type":"uint24"},{"name":"tickSpacing","type":"int24"},{"name":"hooks","type":"address"}], "name":"key", "type":"tuple"},
      {"name":"zeroForOne","type":"bool"},
      {"name":"maxAmount","type":"uint256"},
      {"name":"minDiscountBps","type":"uint256"}
    ],
    "name": "clear",
    "outputs": [],
    "stateMutability": "nonpayable",
    "type": "function"
  },
  {
    "inputs": [
      {"components": [{"name":"currency0","type":"address"},{"name":"currency1","type":"address"},{"name":"fee","type":"uint24"},{"name":"tickSpacing","type":"int24"},{"name":"hooks","type":"address"}], "name":"key", "type":"tuple"}
    ],
    "name": "getStreamState",
    "outputs": [
      {"name":"accrued0","type":"uint256"},
      {"name":"accrued1","type":"uint256"},
      {"name":"currentDiscount","type":"uint256"},
      {"name":"timeSinceLastClear","type":"uint256"}
    ],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "inputs": [
      {"components": [{"name":"currency0","type":"address"},{"name":"currency1","type":"address"},{"name":"fee","type":"uint24"},{"name":"tickSpacing","type":"int24"},{"name":"hooks","type":"address"}], "name":"key", "type":"tuple"},
      {"name":"zeroForOne","type":"bool"}
    ],
    "name": "getStreamPool",
    "outputs": [
      {"name":"sellRateCurrent","type":"uint256"},
      {"name":"earningsFactorCurrent","type":"uint256"}
    ],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "inputs": [],
    "name": "discountRateScaled",
    "outputs": [{"name":"","type":"uint256"}],
    "stateMutability": "view",
    "type": "function"
  },
  {
    "inputs": [],
    "name": "maxDiscountBps",
    "outputs": [{"name":"","type":"uint256"}],
    "stateMutability": "view",
    "type": "function"
  }
]""")

ERC20_ABI = json.loads("""[
  {"inputs":[{"name":"","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
  {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
  {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]""")

# ── Bot ─────────────────────────────────────────────────────────────

class ClearBot:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not self.w3.is_connected():
            raise Exception(f"Cannot connect to RPC: {RPC_URL}")

        self.account = Account.from_key(PRIVATE_KEY)
        self.address = self.account.address
        self.clears = 0
        self.total_cleared_usd = 0.0

        # Fetch market info from indexer
        self._load_market_info()

    def _load_market_info(self):
        """Fetch token addresses and pool key from the indexer."""
        try:
            resp = requests.get(f"{INDEXER_URL}/api/market-info", timeout=5)
            mi = resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch market-info: {e}")
            raise

        self.col_addr = Web3.to_checksum_address(mi["collateral"]["address"])
        self.pos_addr = Web3.to_checksum_address(mi["position_token"]["address"])
        infra = mi.get("infrastructure", {})
        raw_hook = infra.get("twamm_hook") or infra.get("twammHook")
        if not raw_hook or raw_hook.lower() in ("0x", "0x0", ZERO_ADDRESS.lower()):
            raise RuntimeError("Hookless deployment detected (twamm_hook=0x0); clear bot is disabled.")
        self.hook_addr = Web3.to_checksum_address(raw_hook)
        self.mark_price = float(mi.get("mark_price", mi.get("index_price", "3.38")))

        # Build pool key (token0 < token1)
        if self.col_addr.lower() < self.pos_addr.lower():
            self.token0 = self.col_addr
            self.token1 = self.pos_addr
            self.col_is_token0 = True
        else:
            self.token0 = self.pos_addr
            self.token1 = self.col_addr
            self.col_is_token0 = False

        fee = mi["infrastructure"].get("pool_fee", 500)
        tick_spacing = mi["infrastructure"].get("tick_spacing", 5)
        self.pool_key = (self.token0, self.token1, fee, tick_spacing, self.hook_addr)

        # Contracts
        self.jtm = self.w3.eth.contract(address=self.hook_addr, abi=JTM_ABI)
        self.tok0_contract = self.w3.eth.contract(address=self.token0, abi=ERC20_ABI)
        self.tok1_contract = self.w3.eth.contract(address=self.token1, abi=ERC20_ABI)

        # Fetch config
        self.discount_rate_scaled = self.jtm.functions.discountRateScaled().call()
        self.max_discount = self.jtm.functions.maxDiscountBps().call()

        logger.info(f"token0: {self.token0} ({'waUSDC' if self.col_is_token0 else 'wRLP'})")
        logger.info(f"token1: {self.token1} ({'wRLP' if self.col_is_token0 else 'waUSDC'})")
        logger.info(f"JTM hook: {self.hook_addr}")
        logger.info(f"Discount rate: {self.discount_rate_scaled / 10000:.2f} bps/s (scaled: {self.discount_rate_scaled}), max: {self.max_discount} bps")

    def _ensure_approval(self, token_contract, amount):
        """Ensure bot has approved enough tokens to JTM hook."""
        allowance = token_contract.functions.allowance(self.address, self.hook_addr).call()
        if allowance < amount:
            max_uint = 2**256 - 1
            logger.info(f"   🔑 Approving {token_contract.address[:10]}... to JTM hook")
            tx = token_contract.functions.approve(self.hook_addr, max_uint).build_transaction({
                'from': self.address,
                'nonce': self.w3.eth.get_transaction_count(self.address),
                'gas': 100_000,
                'maxFeePerGas': self.w3.to_wei('2', 'gwei'),
                'maxPriorityFeePerGas': self.w3.to_wei('1', 'gwei'),
            })
            signed = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            raw = getattr(signed, 'raw_transaction', getattr(signed, 'rawTransaction', None))
            tx_hash = self.w3.eth.send_raw_transaction(raw)
            self.w3.eth.wait_for_transaction_receipt(tx_hash)
            logger.info(f"   ✅ Approved")

    def _get_balances(self):
        """Get bot's token balances."""
        bal0 = self.tok0_contract.functions.balanceOf(self.address).call()
        bal1 = self.tok1_contract.functions.balanceOf(self.address).call()
        return bal0, bal1

    def _to_usd(self, amount_raw, is_token0):
        """Convert raw token amount to USD value."""
        tokens = amount_raw / 1e6
        if is_token0:
            # token0 is wRLP if col_is_token0=False, waUSDC if col_is_token0=True
            if self.col_is_token0:
                return tokens  # waUSDC = USD
            else:
                return tokens * self.mark_price  # wRLP × price
        else:
            if self.col_is_token0:
                return tokens * self.mark_price  # wRLP × price
            else:
                return tokens  # waUSDC = USD

    def _token_label(self, is_token0):
        if is_token0:
            return "waUSDC" if self.col_is_token0 else "wRLP"
        else:
            return "wRLP" if self.col_is_token0 else "waUSDC"

    def check_and_clear(self):
        """Check stream state and clear if profitable."""
        # Refresh price
        try:
            resp = requests.get(f"{INDEXER_URL}/api/market-info", timeout=3)
            mi = resp.json()
            self.mark_price = float(mi.get("mark_price", mi.get("index_price", str(self.mark_price))))
        except:
            pass  # keep last known price

        # Read stream state
        state = self.jtm.functions.getStreamState(self.pool_key).call()
        accrued0, accrued1, current_discount, time_since_clear = state

        accrued0_usd = self._to_usd(accrued0, True)
        accrued1_usd = self._to_usd(accrued1, False)
        discount_pct = current_discount / 100

        logger.info(
            f"📊 Accrued: {self._token_label(True)}={accrued0/1e6:.4f} (${accrued0_usd:.2f}) | "
            f"{self._token_label(False)}={accrued1/1e6:.4f} (${accrued1_usd:.2f}) | "
            f"Discount: {discount_pct:.1f}% | Since clear: {time_since_clear}s"
        )

        # Check each direction
        # zeroForOne=true: arb buys accrued token0 (pays token1)
        # zeroForOne=false: arb buys accrued token1 (pays token0)
        cleared = False

        if accrued0 > 0 and accrued0_usd >= MIN_CLEAR_USD:
            # Check stream has active orders (clear() requires sellRateCurrent > 0)
            sr0, _ = self.jtm.functions.getStreamPool(self.pool_key, True).call()
            if sr0 > 0:
                cleared = self._execute_clear(True, accrued0, accrued0_usd, current_discount)
            else:
                logger.info(f"   ⏳ Accrued {self._token_label(True)} but stream0For1 inactive (sellRate=0)")

        if accrued1 > 0 and accrued1_usd >= MIN_CLEAR_USD:
            sr1, _ = self.jtm.functions.getStreamPool(self.pool_key, False).call()
            if sr1 > 0:
                cleared = self._execute_clear(False, accrued1, accrued1_usd, current_discount) or cleared
            else:
                logger.info(f"   ⏳ Accrued {self._token_label(False)} but stream1For0 inactive (sellRate=0)")

        if not cleared and (accrued0_usd + accrued1_usd) < MIN_CLEAR_USD:
            logger.info("   💤 Nothing to clear")

    def _execute_clear(self, zero_for_one, accrued_amount, accrued_usd, current_discount):
        """Execute a clear() transaction."""
        direction = "token0" if zero_for_one else "token1"
        buy_label = self._token_label(zero_for_one)
        pay_label = self._token_label(not zero_for_one)

        logger.info(
            f"   🎯 Clearing {accrued_amount/1e6:.4f} {buy_label} "
            f"(${accrued_usd:.2f}) at {current_discount/100:.1f}% discount"
        )

        # Check we have enough payment token
        # Payment is approximately: accrued_amount * price * (1 - discount)
        # The actual payment is calculated on-chain using TWAP price
        bal0, bal1 = self._get_balances()
        pay_balance = bal1 if zero_for_one else bal0
        pay_balance_usd = self._to_usd(pay_balance, not zero_for_one)

        # Rough estimate: payment ≈ accrued_usd * (1 - discount/10000)
        est_payment_usd = accrued_usd * (1 - current_discount / 10000)

        if pay_balance_usd < est_payment_usd:
            logger.warning(
                f"   ⚠️ Insufficient {pay_label}: have ${pay_balance_usd:.2f}, "
                f"need ~${est_payment_usd:.2f}"
            )
            return False

        # Ensure approval for payment token
        pay_token = self.tok1_contract if zero_for_one else self.tok0_contract
        self._ensure_approval(pay_token, int(est_payment_usd * 2 * 1e6))

        # Build and send clear() tx
        try:
            tx = self.jtm.functions.clear(
                self.pool_key,
                zero_for_one,
                accrued_amount,  # maxAmount = all of it
                MIN_DISCOUNT_BPS
            ).build_transaction({
                'from': self.address,
                'nonce': self.w3.eth.get_transaction_count(self.address),
                'gas': MAX_GAS,
                'maxFeePerGas': self.w3.to_wei('3', 'gwei'),
                'maxPriorityFeePerGas': self.w3.to_wei('1', 'gwei'),
            })

            signed = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
            raw = getattr(signed, 'raw_transaction', getattr(signed, 'rawTransaction', None))
            tx_hash = self.w3.eth.send_raw_transaction(raw)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt['status'] == 1:
                self.clears += 1
                self.total_cleared_usd += accrued_usd
                logger.info(
                    f"   ✅ Clear #{self.clears} succeeded! "
                    f"Gas: {receipt['gasUsed']} | "
                    f"Bought {accrued_amount/1e6:.4f} {buy_label} "
                    f"at {current_discount/100:.1f}% discount"
                )

                # Log new balances
                bal0, bal1 = self._get_balances()
                logger.info(
                    f"   💰 Balances: {self._token_label(True)}={bal0/1e6:.2f} | "
                    f"{self._token_label(False)}={bal1/1e6:.2f}"
                )
                return True
            else:
                logger.error(f"   ❌ Clear tx reverted: {receipt['transactionHash'].hex()}")
                return False

        except Exception as e:
            msg = str(e)
            if "NothingToClear" in msg:
                logger.info("   ℹ️ NothingToClear — already cleared by someone else")
            elif "NoActiveStream" in msg:
                logger.info("   ℹ️ NoActiveStream — stream expired, tokens will be donated to LPs")
            elif "InsufficientDiscount" in msg:
                logger.info("   ⏳ InsufficientDiscount — discount hasn't grown enough yet")
            else:
                logger.error(f"   ❌ Clear failed: {msg[:200]}")
            return False

    def run(self):
        """Main daemon loop."""
        logger.info("═" * 60)
        logger.info("🧹 JTM CLEAR AUCTION BOT STARTED")
        logger.info(f"   Address: {self.address}")
        logger.info(f"   JTM Hook: {self.hook_addr}")
        logger.info(f"   Min clear: ${MIN_CLEAR_USD}")
        logger.info(f"   Min discount: {MIN_DISCOUNT_BPS} bps")
        logger.info(f"   Poll interval: {POLL_INTERVAL}s")
        logger.info("═" * 60)

        # Log initial balances
        bal0, bal1 = self._get_balances()
        logger.info(
            f"   💰 Starting balances: {self._token_label(True)}={bal0/1e6:.2f} | "
            f"{self._token_label(False)}={bal1/1e6:.2f}"
        )

        while True:
            try:
                self.check_and_clear()
                time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                logger.info("🛑 Clear bot stopped")
                logger.info(f"   Stats: {self.clears} clears, ${self.total_cleared_usd:.2f} total")
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                time.sleep(10)


def main():
    bot = ClearBot()
    bot.run()


if __name__ == "__main__":
    main()
