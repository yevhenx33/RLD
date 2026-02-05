#!/usr/bin/env python3
"""
Chaotic Trader - Creates random market volatility.

Executes one random trade every 12 seconds to push the mark price
away from the index price. The MM bot will then correct the spread.

Usage:
    python3 scripts/chaotic_trader.py

Environment:
    WAUSDC            - waUSDC token address
    POSITION_TOKEN    - wRLP token address  
    TWAMM_HOOK        - TWAMM/V4 hook address
    USER_B_PRIVATE_KEY - Trader private key (different from MM)
    RPC_URL           - Anvil RPC (default: http://localhost:8545)
"""

import os
import sys
import time
import random
import subprocess
import logging
import signal
from datetime import datetime
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Configure logging with custom format
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv("../contracts/.env")
load_dotenv("../.env")

# Configuration
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
# Use CHAOS_USER_KEY (Mister Chaos) if available, otherwise fallbacks
PRIVATE_KEY = os.getenv("CHAOS_USER_KEY") or os.getenv("USER_B_PRIVATE_KEY") or os.getenv("PRIVATE_KEY")
# Anvil test account #4 (Chaos User - different from deployer and MM)
ANVIL_CHAOS_ACCOUNT = "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba"
if not PRIVATE_KEY:
    PRIVATE_KEY = ANVIL_CHAOS_ACCOUNT
WAUSDC = os.getenv("WAUSDC")
POSITION_TOKEN = os.getenv("POSITION_TOKEN")
TWAMM_HOOK = os.getenv("TWAMM_HOOK")

# Trade Parameters
TRADE_INTERVAL = 12  # seconds between trades
MIN_TRADE_SIZE = 500  # $500 minimum
MAX_TRADE_SIZE = 5000  # $5000 maximum
CONTRACTS_DIR = "/home/ubuntu/RLD/contracts"

# ERC20 ABI (minimal)
ERC20_ABI = [
    {"inputs": [{"name": "", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}
]


class ChaoticTrader:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(PRIVATE_KEY)
        self.trader_address = self.account.address
        
        # Contracts
        self.waUSDC = self.w3.eth.contract(
            address=Web3.to_checksum_address(WAUSDC),
            abi=ERC20_ABI
        )
        self.wRLP = self.w3.eth.contract(
            address=Web3.to_checksum_address(POSITION_TOKEN),
            abi=ERC20_ABI
        )
        
        # Token ordering for swaps
        self.token0 = min(WAUSDC.lower(), POSITION_TOKEN.lower())
        self.token1 = max(WAUSDC.lower(), POSITION_TOKEN.lower())
        self.wausdc_is_token0 = WAUSDC.lower() < POSITION_TOKEN.lower()
        
        # Stats
        self.trades_executed = 0
        self.buys = 0
        self.sells = 0
        self.running = True
        
        # Signal handler
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        logger.info("\n🛑 Stopping chaotic trader...")
        self.running = False
    
    def get_balances(self) -> tuple:
        """Get trader wallet balances."""
        wausdc_bal = self.waUSDC.functions.balanceOf(self.trader_address).call()
        wrlp_bal = self.wRLP.functions.balanceOf(self.trader_address).call()
        return wausdc_bal / 1e6, wrlp_bal / 1e6
    
    def execute_swap(self, buy_wrlp: bool, amount: int) -> bool:
        """Execute swap via LifecycleSwap forge script."""
        
        # Determine swap direction
        if self.wausdc_is_token0:
            zero_for_one = buy_wrlp
        else:
            zero_for_one = not buy_wrlp
        
        env = os.environ.copy()
        env.update({
            "TOKEN0": Web3.to_checksum_address(self.token0),
            "TOKEN1": Web3.to_checksum_address(self.token1),
            "TWAMM_HOOK": TWAMM_HOOK,
            "SWAP_AMOUNT": str(amount),
            "ZERO_FOR_ONE": str(zero_for_one).lower(),
            "SWAP_USER_KEY": PRIVATE_KEY
        })
        
        cmd = [
            "forge", "script", "script/LifecycleSwap.s.sol",
            "--tc", "LifecycleSwap",
            "--rpc-url", RPC_URL,
            "--private-key", PRIVATE_KEY,
            "--broadcast", "-v"
        ]
        
        result = subprocess.run(cmd, cwd=CONTRACTS_DIR, env=env,
                               capture_output=True, text=True)
        
        return result.returncode == 0
    
    def random_trade(self):
        """Execute one random trade."""
        now = datetime.now().strftime("%H:%M:%S")
        
        # Random direction
        buy_wrlp = random.choice([True, False])
        
        # Random size ($500 - $5000)
        trade_usd = random.randint(MIN_TRADE_SIZE, MAX_TRADE_SIZE)
        
        # 20% chance of larger "momentum" trade (2x-3x)
        if random.random() < 0.2:
            multiplier = random.uniform(2.0, 3.0)
            trade_usd = int(trade_usd * multiplier)
        
        trade_amount = int(trade_usd * 1e6)  # Convert to 6 decimals
        
        # Check balance
        wausdc_bal, wrlp_bal = self.get_balances()
        
        if buy_wrlp and wausdc_bal * 1e6 < trade_amount:
            logger.info(f"{now} - ⚠️  Insufficient waUSDC for BUY (have ${wausdc_bal:.0f})")
            return
        if not buy_wrlp and wrlp_bal * 1e6 < trade_amount:
            logger.info(f"{now} - ⚠️  Insufficient wRLP for SELL (have {wrlp_bal:.0f})")
            return
        
        action = "BUY_WRLP" if buy_wrlp else "SELL_WRLP"
        emoji = "📈" if buy_wrlp else "📉"
        
        logger.info(f"{now} - {emoji} {action}: ${trade_usd:,} ({trade_amount/1e6:.0f} tokens)")
        
        success = self.execute_swap(buy_wrlp, trade_amount)
        
        if success:
            self.trades_executed += 1
            if buy_wrlp:
                self.buys += 1
            else:
                self.sells += 1
            logger.info(f"{now} -    ✅ Trade executed successfully")
        else:
            logger.info(f"{now} -    ❌ Trade failed")
    
    def run(self):
        """Run the chaotic trader continuously."""
        logger.info("=" * 60)
        logger.info("🎲 Chaotic Trader Started")
        logger.info(f"   Interval:    {TRADE_INTERVAL}s")
        logger.info(f"   Trade Size:  ${MIN_TRADE_SIZE} - ${MAX_TRADE_SIZE}")
        logger.info(f"   Trader:      {self.trader_address[:10]}...{self.trader_address[-6:]}")
        logger.info("=" * 60)
        logger.info("")
        
        while self.running:
            try:
                self.random_trade()
                time.sleep(TRADE_INTERVAL)
            except Exception as e:
                logger.error(f"Error in trade: {e}")
                time.sleep(TRADE_INTERVAL)
        
        # Final stats
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 Chaotic Trader Statistics")
        logger.info(f"   Total Trades: {self.trades_executed}")
        logger.info(f"   Buys:         {self.buys}")
        logger.info(f"   Sells:        {self.sells}")
        logger.info("=" * 60)


def main():
    # Validate configuration (PRIVATE_KEY has fallback logic)
    required = ["WAUSDC", "POSITION_TOKEN", "TWAMM_HOOK"]
    missing = [v for v in required if not os.getenv(v)]
    
    if missing:
        logger.error(f"❌ Missing environment variables: {missing}")
        sys.exit(1)
    
    # Check Web3 connection
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        logger.error(f"❌ Cannot connect to RPC: {RPC_URL}")
        sys.exit(1)
    
    logger.info(f"✅ Connected to RPC at {RPC_URL}")
    
    # Run trader
    trader = ChaoticTrader()
    trader.run()


if __name__ == "__main__":
    main()
