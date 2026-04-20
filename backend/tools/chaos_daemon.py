#!/usr/bin/env python3
"""
Chaos Trader Daemon

Executes random trades to simulate market activity and test system resilience.
Logs all operations and balances to /tmp/chaos_trader.log

Environment:
    CHAOS_KEY         - Private key for Chaos Trader
    CHAOS_BROKER      - Chaos Trader's broker address
    RPC_URL           - RPC endpoint
    WAUSDC            - waUSDC token address
    POSITION_TOKEN    - wRLP token address
    TWAMM_HOOK        - TWAMM hook address
"""

import os
import sys
import time
import random
import logging
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.v4_swap import V4SwapExecutor, GhostRouterSwapExecutor

# Configure logging with colors
class ColoredFormatter(logging.Formatter):
    COLORS = {
        'WARNING': '\033[93m',
        'INFO': '\033[92m',
        'ERROR': '\033[91m',
        'RESET': '\033[0m',
        'CYAN': '\033[96m',
        'MAGENTA': '\033[95m',
        'YELLOW': '\033[93m',
    }

    def format(self, record):
        msg = super().format(record)
        if record.levelname in self.COLORS:
            msg = f"{self.COLORS['CYAN']}{msg}{self.COLORS['RESET']}"
        return msg

# Setup file + console logging
file_handler = logging.FileHandler('/tmp/chaos_trader.log')
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', '%H:%M:%S'))

console_handler = logging.StreamHandler()
console_handler.setFormatter(ColoredFormatter('%(asctime)s - %(message)s', '%H:%M:%S'))

logger = logging.getLogger(__name__)
logger.addHandler(file_handler)
logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

# Load environment
load_dotenv()

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
PRIVATE_KEY = os.getenv("CHAOS_KEY") or os.getenv("PRIVATE_KEY")
INDEXER_URL = os.getenv("INDEXER_URL", "http://indexer:8080")
FAUCET_URL = os.getenv("FAUCET_URL", "http://faucet:8088")
AUTO_FUND_ENABLED = os.getenv("CHAOS_AUTO_FUND", "true").lower() in ("1", "true", "yes", "on")
AUTO_FUND_COOLDOWN_SEC = int(os.getenv("CHAOS_AUTO_FUND_COOLDOWN_SEC", "90"))

# Trade sizing/eligibility (human amounts)
TRADE_INTERVAL_MIN = int(os.getenv("CHAOS_TRADE_INTERVAL_MIN", "10"))
TRADE_INTERVAL_MAX = int(os.getenv("CHAOS_TRADE_INTERVAL_MAX", "15"))
TRADE_PCT_MIN = float(os.getenv("CHAOS_TRADE_PCT_MIN", "0.01"))
TRADE_PCT_MAX = float(os.getenv("CHAOS_TRADE_PCT_MAX", "0.10"))
MIN_WAUSDC_FOR_BUY = float(os.getenv("CHAOS_MIN_WAUSDC_FOR_BUY", "100.0"))
MIN_WRLP_FOR_SELL = float(os.getenv("CHAOS_MIN_WRLP_FOR_SELL", "1.0"))
MIN_BUY_AMOUNT_WAUSDC = float(os.getenv("CHAOS_MIN_BUY_AMOUNT_WAUSDC", "50.0"))
MIN_SELL_AMOUNT_WRLP = float(os.getenv("CHAOS_MIN_SELL_AMOUNT_WRLP", "1.0"))

# These will be set by load_config_from_indexer()
BROKER = None
WAUSDC = None
POSITION_TOKEN = None
TWAMM_HOOK = None
SWAP_ROUTER = None
GHOST_ROUTER = None
MARKET_ID = None
GHOST_MARKET_ID = None
TOKEN0 = None
TOKEN1 = None
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _is_zero_address(addr: str | None) -> bool:
    if not addr:
        return True
    normalized = addr.lower()
    return normalized in ("0x", "0x0", ZERO_ADDRESS.lower())


def load_config_from_indexer():
    """Poll GET /config on the indexer until deployer has seeded the market."""
    global BROKER, WAUSDC, POSITION_TOKEN, TWAMM_HOOK, SWAP_ROUTER
    global GHOST_ROUTER, MARKET_ID, GHOST_MARKET_ID, TOKEN0, TOKEN1

    logger.info("⏳ Waiting for deployment config from indexer at %s/config ...", INDEXER_URL)
    while True:
        try:
            resp = requests.get(f"{INDEXER_URL}/config", timeout=5)
            if resp.status_code == 200:
                cfg = resp.json()
                WAUSDC = cfg.get("wausdc")
                POSITION_TOKEN = cfg.get("position_token") or cfg.get("wrlp")
                raw_hook = cfg.get("twamm_hook") or cfg.get("twammHook")
                TWAMM_HOOK = ZERO_ADDRESS if _is_zero_address(raw_hook) else raw_hook
                SWAP_ROUTER = cfg.get("swap_router")
                GHOST_ROUTER = cfg.get("ghost_router") or cfg.get("ghostRouter")
                MARKET_ID = cfg.get("market_id") or cfg.get("marketId")
                # GhostRouter market ID is PoolId (pool_id), not RLD market_id.
                GHOST_MARKET_ID = cfg.get("pool_id") or cfg.get("poolId") or MARKET_ID
                TOKEN0 = min(WAUSDC.lower(), POSITION_TOKEN.lower()) if WAUSDC and POSITION_TOKEN else None
                TOKEN1 = max(WAUSDC.lower(), POSITION_TOKEN.lower()) if WAUSDC and POSITION_TOKEN else None
                logger.info("✅ Config loaded from indexer:")
                logger.info("   WAUSDC=%s  POSITION_TOKEN=%s", WAUSDC, POSITION_TOKEN)
                logger.info("   MARKET_ID=%s", MARKET_ID)
                logger.info("   GHOST_MARKET_ID=%s", GHOST_MARKET_ID)
                logger.info("   GHOST_ROUTER=%s  SWAP_ROUTER=%s", GHOST_ROUTER, SWAP_ROUTER)
                return cfg
            else:
                logger.info("   Indexer returned %d — deployer not done yet...", resp.status_code)
        except Exception as e:
            logger.info("   Indexer not reachable (%s), retrying in 5s...", e)
        time.sleep(5)


ERC20_ABI = [
    {"inputs": [{"name": "", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}
]


class ChaosTrader:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(PRIVATE_KEY)
        self.last_faucet_request_at = 0.0
        
        self.waUSDC = self.w3.eth.contract(
            address=Web3.to_checksum_address(WAUSDC),
            abi=ERC20_ABI
        )
        self.wRLP = self.w3.eth.contract(
            address=Web3.to_checksum_address(POSITION_TOKEN),
            abi=ERC20_ABI
        )
        
        self.running = True
        self.trades = 0
        self.successful_trades = 0
        
        # Swap executor (prefer GhostRouter for hookless deployments)
        if not _is_zero_address(GHOST_ROUTER) and GHOST_MARKET_ID:
            self.swap_executor = GhostRouterSwapExecutor(
                self.w3, TOKEN0, TOKEN1, GHOST_ROUTER, GHOST_MARKET_ID
            )
            logger.info("ℹ️  Swap path: GhostRouter (%s)", GHOST_ROUTER)
        elif SWAP_ROUTER:
            self.swap_executor = V4SwapExecutor(
                self.w3, TOKEN0, TOKEN1, TWAMM_HOOK, SWAP_ROUTER
            )
            logger.info("ℹ️  Swap path: LifecycleSwapRouter (%s)", SWAP_ROUTER)
        else:
            self.swap_executor = None
            logger.warning("⚠️  No swap path configured (GhostRouter/SwapRouter missing) — swaps disabled")

        logger.info(
            "⚙️  Chaos config: interval=%ds..%ds tradePct=%.2f..%.2f "
            "minBuy=%.2f waUSDC minSell=%.2f wRLP autoFund=%s",
            TRADE_INTERVAL_MIN,
            TRADE_INTERVAL_MAX,
            TRADE_PCT_MIN,
            TRADE_PCT_MAX,
            MIN_WAUSDC_FOR_BUY,
            MIN_WRLP_FOR_SELL,
            AUTO_FUND_ENABLED,
        )
        
    def get_balances(self):
        """Get current balances."""
        try:
            wausdc_bal = self.waUSDC.functions.balanceOf(self.account.address).call()
            wrlp_bal = self.wRLP.functions.balanceOf(self.account.address).call()
            return wausdc_bal / 1e6, wrlp_bal / 1e6
        except Exception as e:
            logger.error(f"Failed to get balances: {e}")
            return 0, 0

    def _maybe_auto_fund(self, wausdc_bal: float) -> None:
        """Best-effort faucet top-up when waUSDC is depleted."""
        if not AUTO_FUND_ENABLED or wausdc_bal >= MIN_WAUSDC_FOR_BUY:
            return

        now = time.time()
        if now - self.last_faucet_request_at < AUTO_FUND_COOLDOWN_SEC:
            return

        self.last_faucet_request_at = now
        try:
            logger.info("🚰 Requesting faucet top-up for chaos wallet...")
            resp = requests.post(
                f"{FAUCET_URL}/faucet",
                json={"address": self.account.address},
                timeout=12,
            )
            if resp.status_code == 200:
                logger.info("   ✅ Faucet top-up accepted")
            else:
                logger.warning("   ⚠️  Faucet returned %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("   ⚠️  Faucet request failed: %s", e)
    
    def execute_random_trade(self):
        """Execute a random trade."""
        if not self.swap_executor:
            logger.warning("   ⚠️  No swap executor available")
            return False
        
        wausdc_bal, wrlp_bal = self.get_balances()

        self._maybe_auto_fund(wausdc_bal)

        can_buy = wausdc_bal >= MIN_WAUSDC_FOR_BUY
        can_sell = wrlp_bal >= MIN_WRLP_FOR_SELL

        if not can_buy and not can_sell:
            logger.warning(
                "   ⚠️  Inventory too low for trading (waUSDC=%.2f, wRLP=%.4f)",
                wausdc_bal,
                wrlp_bal,
            )
            return False

        # Choose only feasible directions to avoid long skip streaks.
        if can_buy and can_sell:
            buy_wrlp = random.choice([True, False])
        else:
            buy_wrlp = can_buy

        trade_pct = random.uniform(TRADE_PCT_MIN, TRADE_PCT_MAX)

        if buy_wrlp:
            amount_human = max(MIN_BUY_AMOUNT_WAUSDC, wausdc_bal * trade_pct)
            amount = int(min(amount_human, wausdc_bal) * 1e6)
            direction = "BUY_WRLP"
            zero_for_one = True if WAUSDC.lower() < POSITION_TOKEN.lower() else False
        else:
            amount_human = max(MIN_SELL_AMOUNT_WRLP, wrlp_bal * trade_pct)
            amount = int(min(amount_human, wrlp_bal) * 1e6)
            direction = "SELL_WRLP"
            zero_for_one = False if WAUSDC.lower() < POSITION_TOKEN.lower() else True

        if amount <= 0:
            logger.warning("   ⚠️  Computed zero amount for %s - skipping", direction)
            return False
        
        logger.info(f"🎲 {direction}: {amount/1e6:.0f} tokens ({trade_pct*100:.1f}% of balance)")
        
        try:
            success = self.swap_executor.execute_swap(PRIVATE_KEY, zero_for_one, amount)
            
            if success:
                self.successful_trades += 1
                logger.info(f"   ✅ Trade successful!")
                return True
            else:
                logger.error(f"   ❌ Trade failed")
                return False
                
        except Exception as e:
            logger.error(f"   ❌ Trade error: {e}")
            return False
    
    def log_status(self):
        """Log current status and balances."""
        wausdc_bal, wrlp_bal = self.get_balances()
        logger.info(f"📊 Status | Trades: {self.successful_trades}/{self.trades} | waUSDC: {wausdc_bal:.0f} | wRLP: {wrlp_bal:.0f}")
    
    def cycle(self):
        """Run one trading cycle."""
        self.trades += 1
        self.log_status()
        self.execute_random_trade()
    
    def run(self):
        """Run daemon continuously."""
        logger.info("═" * 60)
        logger.info("🌀 CHAOS TRADER DAEMON STARTED")
        logger.info(f"   Address: {self.account.address}")
        logger.info(f"   Broker:  {BROKER}")
        logger.info("═" * 60)
        
        while self.running:
            try:
                self.cycle()
                # Random interval between trades
                sleep_time = random.randint(TRADE_INTERVAL_MIN, TRADE_INTERVAL_MAX)
                logger.info(f"   💤 Sleeping {sleep_time}s until next trade...")
                time.sleep(sleep_time)
            except KeyboardInterrupt:
                logger.info("🛑 Chaos Trader stopped")
                break
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                time.sleep(10)


def main():
    if not PRIVATE_KEY:
        print("ERROR: CHAOS_KEY or PRIVATE_KEY not set")
        sys.exit(1)

    # Poll indexer for deployment config (blocks until deployer has run)
    load_config_from_indexer()

    trader = ChaosTrader()
    trader.run()


if __name__ == "__main__":
    main()
