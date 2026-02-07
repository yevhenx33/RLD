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
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.v4_swap import V4SwapExecutor

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
load_dotenv("/home/ubuntu/RLD/.env")

RPC_URL = os.getenv("RPC_URL", "http://127.0.0.1:8545")
PRIVATE_KEY = os.getenv("CHAOS_KEY")
BROKER = os.getenv("CHAOS_BROKER")
WAUSDC = os.getenv("WAUSDC")
POSITION_TOKEN = os.getenv("POSITION_TOKEN")
TWAMM_HOOK = os.getenv("TWAMM_HOOK")
SWAP_ROUTER = os.getenv("SWAP_ROUTER")

TOKEN0 = min(WAUSDC.lower(), POSITION_TOKEN.lower()) if WAUSDC and POSITION_TOKEN else None
TOKEN1 = max(WAUSDC.lower(), POSITION_TOKEN.lower()) if WAUSDC and POSITION_TOKEN else None
TRADE_INTERVAL_MIN = 10  # seconds
TRADE_INTERVAL_MAX = 15  # seconds

ERC20_ABI = [
    {"inputs": [{"name": "", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}
]


class ChaosTrader:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(PRIVATE_KEY)
        
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
        
        # V4 swap executor
        if SWAP_ROUTER:
            self.swap_executor = V4SwapExecutor(
                self.w3, TOKEN0, TOKEN1, TWAMM_HOOK, SWAP_ROUTER
            )
        else:
            self.swap_executor = None
            logger.warning("⚠️  SWAP_ROUTER not set — swaps disabled")
        
    def get_balances(self):
        """Get current balances."""
        try:
            wausdc_bal = self.waUSDC.functions.balanceOf(self.account.address).call()
            wrlp_bal = self.wRLP.functions.balanceOf(self.account.address).call()
            return wausdc_bal / 1e6, wrlp_bal / 1e6
        except Exception as e:
            logger.error(f"Failed to get balances: {e}")
            return 0, 0
    
    def execute_random_trade(self):
        """Execute a random trade."""
        if not self.swap_executor:
            logger.warning("   ⚠️  No swap executor available")
            return False
        
        wausdc_bal, wrlp_bal = self.get_balances()
        
        # Randomly decide direction
        buy_wrlp = random.choice([True, False])
        
        # Random trade size (1% to 10% of balance)
        trade_pct = random.uniform(0.01, 0.10)
        
        if buy_wrlp:
            # Buy wRLP with waUSDC
            if wausdc_bal < 1000:
                logger.warning(f"   ⚠️  Insufficient waUSDC ({wausdc_bal:.0f}) - skipping buy")
                return False
            amount = int(wausdc_bal * trade_pct * 1e6)
            direction = "BUY_WRLP"
            zero_for_one = True if WAUSDC.lower() < POSITION_TOKEN.lower() else False
        else:
            # Sell wRLP for waUSDC
            if wrlp_bal < 1000:
                logger.warning(f"   ⚠️  Insufficient wRLP ({wrlp_bal:.0f}) - skipping sell")
                return False
            amount = int(wrlp_bal * trade_pct * 1e6)
            direction = "SELL_WRLP"
            zero_for_one = False if WAUSDC.lower() < POSITION_TOKEN.lower() else True
        
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
        print("ERROR: CHAOS_KEY not set in .env")
        sys.exit(1)
    if not WAUSDC or not POSITION_TOKEN:
        print("ERROR: Token addresses not set in .env")
        sys.exit(1)
    
    trader = ChaosTrader()
    trader.run()


if __name__ == "__main__":
    main()
