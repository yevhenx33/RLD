#!/usr/bin/env python3
"""
Combined Rate Sync + Market Making Daemon

Runs both:
1. Rate Sync: Updates MockRLDAaveOracle from live Aave rates (every 12s)
2. Market Maker: Arbitrages V4 mark price toward index (every 12s)

Displays combined log showing Index, Mark, and Spread.

Usage:
    python3 combined_daemon.py

Environment:
    MOCK_ORACLE_ADDR  - MockRLDAaveOracle address
    WAUSDC            - waUSDC token address
    POSITION_TOKEN    - wRLP token address
    TWAMM_HOOK        - TWAMM/V4 hook address
    PRIVATE_KEY       - Operator private key
    RPC_URL           - Anvil RPC
    API_URL           - Rate API URL
    API_KEY           - API key
"""

import os
import sys
import time
import logging
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.v4_pool import V4PoolReader
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
    }
    
    def format(self, record):
        msg = super().format(record)
        if 'Index' in msg or '📡' in msg:
            return f"{self.COLORS['CYAN']}{msg}{self.COLORS['RESET']}"
        elif 'Mark' in msg or '📊' in msg:
            return f"{self.COLORS['MAGENTA']}{msg}{self.COLORS['RESET']}"
        return msg

handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter('%(asctime)s - %(message)s', '%H:%M:%S'))
logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Load environment
load_dotenv("../contracts/.env")
load_dotenv("../.env")

# Configuration
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
API_URL = os.getenv("API_URL", "https://rate-dashboard.onrender.com")
API_KEY = os.getenv("API_KEY")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # MM user key for swaps
ORACLE_ADMIN_KEY = os.getenv("ORACLE_ADMIN_KEY", PRIVATE_KEY)  # Deployer key for oracle updates
MOCK_ORACLE_ADDR = os.getenv("MOCK_ORACLE_ADDR")
WAUSDC = os.getenv("WAUSDC")
POSITION_TOKEN = os.getenv("POSITION_TOKEN")
TWAMM_HOOK = os.getenv("TWAMM_HOOK")
RLD_CORE = os.getenv("RLD_CORE")
MARKET_ID = os.getenv("MARKET_ID")

SYNC_INTERVAL = 12  # seconds
MM_THRESHOLD = 0.01  # 1% = 100 basis points
SWAP_ROUTER = os.getenv("SWAP_ROUTER")

# ABIs
ORACLE_ABI = [
    {"inputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}],
     "name": "getIndexPrice", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "newRateRay", "type": "uint256"}],
     "name": "setRate", "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [], "name": "mockRateRay",
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"}
]

# RLDCore ABI for reading market state
# getMarketState returns MarketState(normalizationFactor, totalDebt, lastUpdateTimestamp)
RLD_CORE_ABI = [
    {"inputs": [{"name": "id", "type": "bytes32"}],
     "name": "getMarketState", 
     "outputs": [
         {"components": [
             {"name": "normalizationFactor", "type": "uint128"},
             {"name": "totalDebt", "type": "uint128"},
             {"name": "lastUpdateTimestamp", "type": "uint48"}
         ], "name": "", "type": "tuple"}
     ],
     "stateMutability": "view", "type": "function"}
]


ERC20_ABI = [
    {"inputs": [{"name": "", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"}
]


def fetch_latest_rate():
    """Fetch latest USDC borrow rate — tries API first, falls back to on-chain Aave."""
    # Try API first (production)
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        response = requests.get(f"{API_URL}/rates?limit=1&symbol=USDC", headers=headers, timeout=5)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            apy = data[0].get("apy", 0)
            if apy:
                return apy
    except Exception:
        pass  # Fall through to on-chain

    # Fallback: read rate directly from Aave V3 on-chain
    try:
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
        USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        # getReserveData returns a tuple; index 4 is currentVariableBorrowRate (RAY)
        POOL_ABI = [{"inputs": [{"name": "asset", "type": "address"}],
                     "name": "getReserveData",
                     "outputs": [{"components": [
                         {"name": "configuration", "type": "uint256"},
                         {"name": "liquidityIndex", "type": "uint128"},
                         {"name": "currentLiquidityRate", "type": "uint128"},
                         {"name": "variableBorrowIndex", "type": "uint128"},
                         {"name": "currentVariableBorrowRate", "type": "uint128"},
                         {"name": "currentStableBorrowRate", "type": "uint128"},
                         {"name": "lastUpdateTimestamp", "type": "uint40"},
                         {"name": "id", "type": "uint16"},
                         {"name": "aTokenAddress", "type": "address"},
                         {"name": "stableDebtTokenAddress", "type": "address"},
                         {"name": "variableDebtTokenAddress", "type": "address"},
                         {"name": "interestRateStrategyAddress", "type": "address"},
                         {"name": "accruedToTreasury", "type": "uint128"},
                         {"name": "unbacked", "type": "uint128"},
                         {"name": "isolationModeTotalDebt", "type": "uint128"},
                     ], "name": "", "type": "tuple"}],
                     "stateMutability": "view", "type": "function"}]
        pool = w3.eth.contract(address=Web3.to_checksum_address(AAVE_POOL), abi=POOL_ABI)
        reserve_data = pool.functions.getReserveData(Web3.to_checksum_address(USDC_ADDR)).call()
        variable_borrow_rate_ray = reserve_data[4]  # currentVariableBorrowRate in RAY
        apy_percent = variable_borrow_rate_ray / 1e25  # RAY to percent
        logger.info(f"📡 On-chain Aave USDC rate: {apy_percent:.4f}%")
        return apy_percent
    except Exception as e:
        logger.error(f"On-chain rate fetch failed: {e}")
        return None


def apy_to_ray(apy_percent):
    return int(apy_percent / 100 * 1e27)


class CombinedDaemon:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(PRIVATE_KEY)  # MM user for swaps
        self.oracle_admin_account = Account.from_key(ORACLE_ADMIN_KEY)  # Deployer for oracle
        
        # Oracle contract
        self.oracle = self.w3.eth.contract(
            address=Web3.to_checksum_address(MOCK_ORACLE_ADDR),
            abi=ORACLE_ABI
        )
        
        # Token contracts for balance checks
        self.waUSDC = self.w3.eth.contract(
            address=Web3.to_checksum_address(WAUSDC),
            abi=ERC20_ABI
        )
        self.wRLP = self.w3.eth.contract(
            address=Web3.to_checksum_address(POSITION_TOKEN),
            abi=ERC20_ABI
        )
        
        # RLDCore for reading market state
        if RLD_CORE:
            self.rld_core = self.w3.eth.contract(
                address=Web3.to_checksum_address(RLD_CORE),
                abi=RLD_CORE_ABI
            )
        else:
            self.rld_core = None
        
        # Token order for V4
        self.token0 = min(WAUSDC.lower(), POSITION_TOKEN.lower())
        self.token1 = max(WAUSDC.lower(), POSITION_TOKEN.lower())
        self.wausdc_is_token0 = WAUSDC.lower() < POSITION_TOKEN.lower()
        
        # V4 pool reader (replaces forge script GetMarkPrice + CalculateSwapAmount)
        self.pool_reader = V4PoolReader(
            self.w3, self.token0, self.token1,
            os.getenv("TWAMM_HOOK"), WAUSDC
        )
        
        # V4 swap executor (replaces forge script LifecycleSwap)
        if SWAP_ROUTER:
            self.swap_executor = V4SwapExecutor(
                self.w3, self.token0, self.token1,
                os.getenv("TWAMM_HOOK"), SWAP_ROUTER
            )
        else:
            self.swap_executor = None
            logger.warning("⚠️  SWAP_ROUTER not set — swaps will be skipped")
        
        self.running = True
        self.trades = 0
    
    def get_index_price(self) -> float:
        """Get index price from oracle (WAD format)."""
        try:
            price = self.oracle.functions.getIndexPrice(
                Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),
                Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
            ).call()
            return price / 1e18
        except:
            return None
    
    def get_mark_price(self) -> float:
        """Get mark price from V4 pool via direct extsload."""
        try:
            return self.pool_reader.get_mark_price()
        except Exception as e:
            logger.error(f"Mark price read failed: {e}")
            return None
    
    def update_oracle(self, rate_ray: int) -> bool:
        """Update oracle rate using oracle admin account."""
        try:
            nonce = self.w3.eth.get_transaction_count(self.oracle_admin_account.address)
            tx = self.oracle.functions.setRate(rate_ray).build_transaction({
                'from': self.oracle_admin_account.address,
                'nonce': nonce,
                'gas': 100000,
                'maxFeePerGas': self.w3.to_wei('2', 'gwei'),
                'maxPriorityFeePerGas': self.w3.to_wei('1', 'gwei'),
            })
            signed = self.w3.eth.account.sign_transaction(tx, ORACLE_ADMIN_KEY)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            return receipt.status == 1
        except Exception as e:
            logger.error(f"Oracle update failed: {e}")
            return False
    
    def execute_swap(self, buy_wrlp: bool, amount: int) -> bool:
        """Execute swap via pre-deployed router."""
        if not self.swap_executor:
            logger.error("   ❌ No swap router available")
            return False
        
        if self.wausdc_is_token0:
            zero_for_one = buy_wrlp
        else:
            zero_for_one = not buy_wrlp
        
        return self.swap_executor.execute_swap(PRIVATE_KEY, zero_for_one, amount)
    
    def calculate_swap_amount(self, target_price: float) -> tuple:
        """Calculate exact swap amount using V4 math in Python."""
        try:
            return self.pool_reader.calculate_swap_amount(target_price)
        except Exception as e:
            logger.error(f"Calculate swap failed: {e}")
            return (0, True, "UNKNOWN")
    
    def cycle(self):
        """Run one combined cycle."""
        # 1. Fetch and update rate from API
        apy = fetch_latest_rate()
        if apy:
            rate_ray = apy_to_ray(apy)
            self.update_oracle(rate_ray)
        
        # 2. Get prices
        index_price = self.get_index_price()
        mark_price = self.get_mark_price()
        
        if index_price is None or mark_price is None:
            logger.warning("⚠️  Could not fetch prices")
            return
        
        # Get normalization factor from RLDCore market state
        try:
            if self.rld_core and MARKET_ID:
                market_id_bytes = bytes.fromhex(MARKET_ID[2:]) if MARKET_ID.startswith('0x') else bytes.fromhex(MARKET_ID)
                market_state = self.rld_core.functions.getMarketState(market_id_bytes).call()
                # market_state[0] is normalizationFactor (uint128)
                norm_factor = market_state[0]
                norm_factor_display = norm_factor / 1e18  # WAD format
            else:
                norm_factor_display = 1.0
        except Exception as e:
            norm_factor_display = 1.0
        
        # 3. Calculate spread
        spread = (mark_price - index_price) / index_price
        spread_bps = spread * 10000
        
        # 4. Log status
        status = "✅" if abs(spread) < MM_THRESHOLD else ("📈" if spread < 0 else "📉")
        logger.info(f"📡 Index=${index_price:.4f} | 📊 Mark=${mark_price:.4f} | NF={norm_factor_display:.10f} | Spread={spread_bps:+.2f}bps {status}")

        
        # 5. Execute arb if needed - use precise calculation
        if abs(spread) >= MM_THRESHOLD:
            # Get balances for debugging
            try:
                wausdc_bal = self.waUSDC.functions.balanceOf(self.account.address).call()
                wrlp_bal = self.wRLP.functions.balanceOf(self.account.address).call()
                logger.info(f"   💰 Balances: waUSDC={wausdc_bal/1e6:.0f} | wRLP={wrlp_bal/1e6:.0f}")
            except Exception as e:
                logger.warning(f"   ⚠️  Could not fetch balances: {e}")
            
            # Calculate exact swap amount to reach index price
            amount_in, zero_for_one, direction = self.calculate_swap_amount(index_price)
            
            if amount_in == 0:
                logger.warning("   ⚠️  Could not calculate swap amount")
                return
            
            # Cap at available balance (safety)
            amount_in = min(amount_in, 500000 * 1_000_000)  # Max $500k per trade
            
            buy_wrlp = (direction == "BUY_WRLP")
            logger.info(f"   🎯 {direction}: {amount_in/1e6:.0f} tokens to reach ${index_price:.4f}")
            
            if self.execute_swap(buy_wrlp=buy_wrlp, amount=amount_in):
                self.trades += 1
    
    def run(self):
        """Run daemon continuously."""
        print("\n" + "=" * 60)
        print("🤖 COMBINED DAEMON: Rate Sync + Market Maker")
        print("=" * 60)
        print(f"   Oracle:    {MOCK_ORACLE_ADDR}")
        print(f"   Threshold: {MM_THRESHOLD*100:.4f}%")
        print(f"   Interval:  {SYNC_INTERVAL}s")
        print("=" * 60 + "\n")
        
        while self.running:
            try:
                self.cycle()
                time.sleep(SYNC_INTERVAL)
            except KeyboardInterrupt:
                print("\n🛑 Stopping daemon...")
                self.running = False
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                time.sleep(SYNC_INTERVAL)
        
        print(f"\n📊 Total trades: {self.trades}")


def main():
    required = ["MOCK_ORACLE_ADDR", "WAUSDC", "POSITION_TOKEN", "TWAMM_HOOK", "PRIVATE_KEY"]
    missing = [v for v in required if not os.getenv(v)]
    
    if missing:
        print(f"❌ Missing: {missing}")
        sys.exit(1)
    
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"❌ Cannot connect to {RPC_URL}")
        sys.exit(1)
    
    print(f"✅ Connected to {RPC_URL}")
    
    daemon = CombinedDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
