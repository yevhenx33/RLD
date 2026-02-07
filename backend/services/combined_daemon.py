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
import subprocess
import logging
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

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
CONTRACTS_DIR = "/home/ubuntu/RLD/contracts"

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
    """Fetch latest USDC borrow rate from API."""
    try:
        headers = {"X-API-Key": API_KEY} if API_KEY else {}
        response = requests.get(f"{API_URL}/rates?limit=1&symbol=USDC", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data and len(data) > 0:
            return data[0].get("apy", 0)
        return None
    except Exception as e:
        logger.error(f"API error: {e}")
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
        """Get mark price from V4 pool."""
        try:
            env = os.environ.copy()
            env.update({
                "TOKEN0": Web3.to_checksum_address(self.token0),
                "TOKEN1": Web3.to_checksum_address(self.token1),
                "TWAMM_HOOK": TWAMM_HOOK,
                "WAUSDC": WAUSDC  # Needed to determine price inversion
            })
            
            result = subprocess.run(
                ["forge", "script", "script/GetMarkPrice.s.sol", "--tc", "GetMarkPrice",
                 "--rpc-url", RPC_URL, "-v"],
                cwd=CONTRACTS_DIR, env=env, capture_output=True, text=True
            )
            
            for line in result.stdout.split('\n'):
                if "MARK_PRICE_X18:" in line:
                    return int(line.split(":")[-1].strip()) / 1e18
            return None
        except:
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
        """Execute swap to correct price."""
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
        
        result = subprocess.run(
            ["forge", "script", "script/LifecycleSwap.s.sol", "--tc", "LifecycleSwap",
             "--rpc-url", RPC_URL, "--broadcast", "-v"],
            cwd=CONTRACTS_DIR, env=env, capture_output=True, text=True
        )
        
        if result.returncode != 0:
            # Log error for debugging
            logger.error(f"   ❌ Swap failed! Check stderr:")
            for line in result.stderr.split('\n')[-5:]:
                if line.strip():
                    logger.error(f"      {line}")
            return False
        
        return True
    
    def calculate_swap_amount(self, target_price: float) -> tuple:
        """Calculate exact swap amount to reach target price using V4 math."""
        try:
            target_price_wad = int(target_price * 1e18)
            
            env = os.environ.copy()
            env.update({
                "TOKEN0": Web3.to_checksum_address(self.token0),
                "TOKEN1": Web3.to_checksum_address(self.token1),
                "TWAMM_HOOK": TWAMM_HOOK,
                "WAUSDC": WAUSDC,
                "TARGET_PRICE_WAD": str(target_price_wad)
            })
            
            result = subprocess.run(
                ["forge", "script", "script/CalculateSwapAmount.s.sol", "--tc", "CalculateSwapAmount",
                 "--rpc-url", RPC_URL, "-v"],
                cwd=CONTRACTS_DIR, env=env, capture_output=True, text=True
            )
            
            amount_in = 0
            zero_for_one = True
            direction = "BUY_WRLP"
            
            for line in result.stdout.split('\n'):
                if "AMOUNT_IN:" in line:
                    amount_in = int(line.split(":")[-1].strip())
                elif "ZERO_FOR_ONE:" in line:
                    zero_for_one = int(line.split(":")[-1].strip()) == 1
                elif "DIRECTION:" in line:
                    direction = line.split(":")[-1].strip()
            
            return (amount_in, zero_for_one, direction)
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
