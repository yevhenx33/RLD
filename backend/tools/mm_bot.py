#!/usr/bin/env python3
"""
Market Making Bot - Aligns Uniswap V4 mark price to oracle index price.

Runs continuously, checking price deviation every 12 seconds.
When spread exceeds threshold, executes swap to bring mark toward index.

Usage:
    python3 mm_bot.py

Environment:
    MOCK_ORACLE_ADDR  - MockRLDAaveOracle address
    BROKER_FACTORY    - BrokerFactory for creating MM broker
    MARKET_ID         - RLD Market ID
    WAUSDC            - waUSDC token address
    POSITION_TOKEN    - wRLP token address  
    TWAMM_HOOK        - TWAMM/V4 hook address
    PRIVATE_KEY       - MM operator private key
    RPC_URL           - Anvil RPC (default: http://localhost:8545)
"""

import os
import sys
import time
import json
import subprocess
import logging
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
from decimal import Decimal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment
load_dotenv("../contracts/.env")
load_dotenv("../.env")

# Configuration
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
MOCK_ORACLE_ADDR = os.getenv("MOCK_ORACLE_ADDR")
BROKER_FACTORY = os.getenv("BROKER_FACTORY")
MARKET_ID = os.getenv("MARKET_ID")
WAUSDC = os.getenv("WAUSDC")
POSITION_TOKEN = os.getenv("POSITION_TOKEN")
TWAMM_HOOK = os.getenv("TWAMM_HOOK")

# MM Parameters
THRESHOLD = 0.01     # 1% = 100 basis points
SYNC_INTERVAL = 12   # seconds
CONTRACTS_DIR = "/home/ubuntu/RLD/contracts"

# V4 Pool Manager
POOL_MANAGER = "0x000000000004444c5dc75cB358380D2e3dE08A90"

# ABIs (minimal)
ORACLE_ABI = [
    {"inputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}],
     "name": "getIndexPrice", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"}
]

ERC20_ABI = [
    {"inputs": [{"name": "", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "", "type": "address"}, {"name": "", "type": "uint256"}],
     "name": "transfer", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "", "type": "address"}, {"name": "", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"}
]


def run_cast(args: list) -> str:
    """Run cast command and return output."""
    cmd = ["cast"] + args + ["--rpc-url", RPC_URL]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"cast error: {result.stderr}")
    return result.stdout.strip()


def run_forge_script(script: str, env: dict) -> bool:
    """Run a forge script with environment variables."""
    full_env = os.environ.copy()
    full_env.update(env)
    
    cmd = [
        "forge", "script", script,
        "--rpc-url", RPC_URL,
        "--private-key", PRIVATE_KEY,
        "--broadcast", "-v"
    ]
    
    result = subprocess.run(cmd, cwd=CONTRACTS_DIR, env=full_env, 
                           capture_output=True, text=True)
    return result.returncode == 0


class MarketMaker:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(PRIVATE_KEY)
        self.mm_address = self.account.address
        
        # Contracts
        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(MOCK_ORACLE_ADDR),
            abi=ORACLE_ABI
        )
        self.waUSDC = self.w3.eth.contract(
            address=Web3.to_checksum_address(WAUSDC),
            abi=ERC20_ABI
        )
        self.wRLP = self.w3.eth.contract(
            address=Web3.to_checksum_address(POSITION_TOKEN),
            abi=ERC20_ABI
        )
        
        # Determine token order (currency0 < currency1)
        self.token0 = min(WAUSDC.lower(), POSITION_TOKEN.lower())
        self.token1 = max(WAUSDC.lower(), POSITION_TOKEN.lower())
        self.wausdc_is_token0 = WAUSDC.lower() < POSITION_TOKEN.lower()
        
        # Stats
        self.trades_executed = 0
        self.total_volume = 0
        self.running = True
    
    def get_index_price(self) -> float:
        """Get index price from oracle in WAD format."""
        try:
            # Oracle ignores these addresses for mock
            price_wad = self.oracle.functions.getIndexPrice(
                Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),
                Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
            ).call()
            return price_wad / 1e18
        except Exception as e:
            logger.error(f"Failed to get index price: {e}")
            return None
    
    def get_mark_price(self) -> float:
        """Get mark price from V4 pool via GetMarkPrice forge script."""
        try:
            env = os.environ.copy()
            env.update({
                "TOKEN0": Web3.to_checksum_address(self.token0),
                "TOKEN1": Web3.to_checksum_address(self.token1),
                "TWAMM_HOOK": TWAMM_HOOK
            })
            
            result = subprocess.run(
                ["forge", "script", "script/GetMarkPrice.s.sol", "--tc", "GetMarkPrice",
                 "--rpc-url", RPC_URL, "-v"],
                cwd=CONTRACTS_DIR,
                env=env,
                capture_output=True,
                text=True
            )
            
            # Parse MARK_PRICE_X18 from output
            for line in result.stdout.split('\n'):
                if "MARK_PRICE_X18:" in line:
                    price_x18 = int(line.split(":")[-1].strip())
                    return price_x18 / 1e18
            
            logger.warning("Could not parse mark price from output")
            return None
            
        except Exception as e:
            logger.error(f"Failed to get mark price: {e}")
            return None
    
    def get_mm_balances(self) -> tuple:
        """Get MM wallet balances."""
        wausdc_bal = self.waUSDC.functions.balanceOf(self.mm_address).call()
        wrlp_bal = self.wRLP.functions.balanceOf(self.mm_address).call()
        return wausdc_bal / 1e6, wrlp_bal / 1e6
    
    def execute_swap(self, buy_wrlp: bool, amount: int) -> bool:
        """Execute swap via LifecycleSwap forge script."""
        
        # Determine swap direction
        # If waUSDC is token0:
        #   buy wRLP = zeroForOne=true (sell token0/waUSDC, get token1/wRLP)
        #   sell wRLP = zeroForOne=false
        # If waUSDC is token1 (wRLP is token0):
        #   buy wRLP = zeroForOne=false (sell token1/waUSDC, get token0/wRLP)
        #   sell wRLP = zeroForOne=true
        
        if self.wausdc_is_token0:
            zero_for_one = buy_wrlp  # true to buy wRLP
        else:
            zero_for_one = not buy_wrlp  # false to buy wRLP
        
        env = {
            "TOKEN0": Web3.to_checksum_address(self.token0),
            "TOKEN1": Web3.to_checksum_address(self.token1),
            "TWAMM_HOOK": TWAMM_HOOK,
            "SWAP_AMOUNT": str(amount),
            "ZERO_FOR_ONE": str(zero_for_one).lower(),
            "SWAP_USER_KEY": PRIVATE_KEY
        }
        
        logger.info(f"🔄 Executing swap: {'BUY' if buy_wrlp else 'SELL'} wRLP, amount={amount/1e6:.2f}")
        
        success = run_forge_script("script/LifecycleSwap.s.sol --tc LifecycleSwap", env)
        
        if success:
            self.trades_executed += 1
            self.total_volume += amount / 1e6
            
        return success
    
    def calculate_trade_size(self, spread: float, index_price: float) -> int:
        """Calculate trade size based on spread magnitude."""
        # Larger spread = larger trade to correct faster
        # Base: trade enough to move price by ~spread/2
        
        # For now: trade proportional to spread
        # At 0.1% spread, trade 10k; at 1% spread, trade 100k
        
        base_size = 10000  # $10k base
        size_multiplier = abs(spread) / 0.001  # relative to 0.1%
        
        size_usd = base_size * max(1, size_multiplier)
        
        # Convert to token units (6 decimals)
        return int(size_usd * 1e6)
    
    def arb_cycle(self):
        """Perform one arbitrage cycle."""
        index_price = self.get_index_price()
        mark_price = self.get_mark_price()
        
        if index_price is None or mark_price is None:
            logger.warning("⚠️  Could not fetch prices")
            return
        
        # Calculate spread
        spread = (mark_price - index_price) / index_price
        spread_bps = spread * 10000
        
        wausdc_bal, wrlp_bal = self.get_mm_balances()
        
        logger.info(f"📊 Index=${index_price:.4f} | Mark=${mark_price:.4f} | Spread={spread_bps:.2f}bps")
        logger.info(f"   MM Wallet: {wausdc_bal:.2f} waUSDC | {wrlp_bal:.2f} wRLP")
        
        # Check if spread exceeds threshold
        if abs(spread) < THRESHOLD:
            logger.info(f"   ✅ Spread within threshold ({THRESHOLD*100:.4f}%)")
            return
        
        # Calculate trade size
        trade_size = self.calculate_trade_size(spread, index_price)
        
        if spread > THRESHOLD:
            # Mark too high - SELL wRLP to push price down
            logger.info(f"   📉 Mark too HIGH - selling wRLP to push down")
            
            if wrlp_bal * 1e6 < trade_size:
                logger.warning(f"   ⚠️  Insufficient wRLP balance")
                return
            
            self.execute_swap(buy_wrlp=False, amount=trade_size)
            
        else:
            # Mark too low - BUY wRLP to push price up
            logger.info(f"   📈 Mark too LOW - buying wRLP to push up")
            
            if wausdc_bal * 1e6 < trade_size:
                logger.warning(f"   ⚠️  Insufficient waUSDC balance")
                return
            
            self.execute_swap(buy_wrlp=True, amount=trade_size)
    
    def run(self):
        """Run the MM bot continuously."""
        logger.info("=" * 60)
        logger.info("🤖 Market Making Bot Started")
        logger.info(f"   Threshold: {THRESHOLD*100:.4f}%")
        logger.info(f"   Interval:  {SYNC_INTERVAL}s")
        logger.info(f"   MM Wallet: {self.mm_address}")
        logger.info(f"   Oracle:    {MOCK_ORACLE_ADDR}")
        logger.info("=" * 60)
        
        while self.running:
            try:
                self.arb_cycle()
                time.sleep(SYNC_INTERVAL)
            except KeyboardInterrupt:
                logger.info("🛑 Stopping MM bot...")
                self.running = False
            except Exception as e:
                logger.error(f"Error in arb cycle: {e}")
                time.sleep(SYNC_INTERVAL)
        
        # Final stats
        logger.info("=" * 60)
        logger.info("📊 Final Statistics")
        logger.info(f"   Trades: {self.trades_executed}")
        logger.info(f"   Volume: ${self.total_volume:,.2f}")
        logger.info("=" * 60)


def main():
    # Validate configuration
    required = ["MOCK_ORACLE_ADDR", "WAUSDC", "POSITION_TOKEN", "TWAMM_HOOK", "PRIVATE_KEY"]
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
    
    # Run bot
    bot = MarketMaker()
    bot.run()


if __name__ == "__main__":
    main()
