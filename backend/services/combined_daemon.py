#!/usr/bin/env python3
"""
Combined Market Daemon — Rate Sync + Market Making + Clear Auctions + Timestamp Sync

Runs four sub-systems at two speeds:

Fast loop (every 2s):
  1. Timestamp Sync: Keeps Anvil EVM TIMESTAMP opcode in sync with block headers
  2. Clear Auctions: Buys accrued ghost tokens from TWAMM at discount

Slow loop (every 12s):
  3. Rate Sync: Updates MockRLDAaveOracle from live Aave rates
  4. Market Maker: Arbitrages V4 mark price toward index

Usage:
    python3 combined_daemon.py

Environment:
    MOCK_ORACLE_ADDR  - MockRLDAaveOracle address
    WAUSDC            - waUSDC token address
    POSITION_TOKEN    - wRLP token address
    TWAMM_HOOK        - TWAMM/V4 hook address
    PRIVATE_KEY       - Operator private key
    RPC_URL           - Anvil RPC
    API_URL           - Envio/data-pipeline API base URL
"""

import json
import os
import sys
import time
import logging
import urllib.request
import urllib.error
import requests
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Add backend to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.v4_pool import V4PoolReader
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

# Configuration — keys from env, contract addresses from indexer API
RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
API_URL = os.getenv("API_URL", "http://127.0.0.1:5000")
INDEXER_URL = os.getenv("INDEXER_URL", "http://indexer:8080")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # MM user key for swaps
ORACLE_ADMIN_KEY = os.getenv("ORACLE_ADMIN_KEY", PRIVATE_KEY)  # Deployer key for oracle updates
INDEXER_TIMEOUT = float(os.getenv("INDEXER_TIMEOUT", "3"))

# These will be set by load_config_from_indexer()
MOCK_ORACLE_ADDR = None
WAUSDC = None
POSITION_TOKEN = None
TWAMM_HOOK = None
RLD_CORE = None
MARKET_ID = None
GHOST_MARKET_ID = None
SWAP_ROUTER = None
GHOST_ROUTER = None
AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"       # Mainnet Aave V3 Pool (fork constant)
UNDERLYING_TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # Mainnet USDC (fork constant)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def _is_zero_address(addr: str | None) -> bool:
    if not addr:
        return True
    normalized = addr.lower()
    return normalized in ("0x", "0x0", ZERO_ADDRESS.lower())


def load_config_from_indexer():
    """Poll GET /config on the indexer until deployer has seeded the market."""
    global MOCK_ORACLE_ADDR, WAUSDC, POSITION_TOKEN, TWAMM_HOOK
    global RLD_CORE, MARKET_ID, GHOST_MARKET_ID, SWAP_ROUTER, GHOST_ROUTER, AAVE_POOL, UNDERLYING_TOKEN

    logger.info("⏳ Waiting for deployment config from indexer at %s/config ...", INDEXER_URL)
    while True:
        try:
            resp = requests.get(f"{INDEXER_URL}/config", timeout=5)
            if resp.status_code == 200:
                cfg = resp.json()
                MOCK_ORACLE_ADDR = cfg.get("mock_oracle")
                WAUSDC = cfg.get("wausdc")
                POSITION_TOKEN = cfg.get("position_token") or cfg.get("wrlp")
                raw_hook = cfg.get("twamm_hook") or cfg.get("twammHook")
                if _is_zero_address(raw_hook):
                    TWAMM_HOOK = ZERO_ADDRESS
                else:
                    TWAMM_HOOK = Web3.to_checksum_address(raw_hook)
                RLD_CORE = cfg.get("rld_core")
                MARKET_ID = cfg.get("market_id") or cfg.get("marketId")
                # GhostRouter market ID is PoolId (pool_id), not RLD market_id.
                GHOST_MARKET_ID = cfg.get("pool_id") or cfg.get("poolId") or MARKET_ID
                SWAP_ROUTER = cfg.get("swap_router")
                GHOST_ROUTER = cfg.get("ghost_router") or cfg.get("ghostRouter")
                ext = cfg.get("external_contracts", {})
                AAVE_POOL = ext.get("aave_pool")
                UNDERLYING_TOKEN = ext.get("usdc")
                logger.info("✅ Config loaded from indexer:")
                logger.info("   MARKET_ID=%s", MARKET_ID)
                logger.info("   GHOST_MARKET_ID=%s", GHOST_MARKET_ID)
                logger.info("   MOCK_ORACLE=%s", MOCK_ORACLE_ADDR)
                logger.info("   WAUSDC=%s  POSITION_TOKEN=%s", WAUSDC, POSITION_TOKEN)
                logger.info(
                    "   TWAMM_HOOK=%s (clear=%s)  GHOST_ROUTER=%s  SWAP_ROUTER=%s",
                    TWAMM_HOOK,
                    "enabled" if not _is_zero_address(TWAMM_HOOK) else "disabled",
                    GHOST_ROUTER,
                    SWAP_ROUTER,
                )
                return cfg
            else:
                logger.info("   Indexer returned %d — deployer not done yet, retrying...", resp.status_code)
        except Exception as e:
            logger.info("   Indexer not reachable (%s), retrying in 5s...", e)
        time.sleep(5)

FAST_INTERVAL = 2    # seconds — timestamp sync + clear auctions
SLOW_INTERVAL = 12   # seconds — rate sync + MM arb
MM_THRESHOLD = 0.01  # 1% = 100 basis points

# Clear auction config
MIN_CLEAR_USD = 0.001  # minimum $ value to clear
MIN_DISCOUNT_BPS = 1   # minimum discount (0.01%) — aggressive clearing
MAX_CLEAR_GAS = 500_000

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
     "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
]

# JTM Hook ABI — clear auctions
JTM_HOOK_ABI = [
    {"inputs": [{"components": [
        {"name": "currency0", "type": "address"}, {"name": "currency1", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "tickSpacing", "type": "int24"},
        {"name": "hooks", "type": "address"}
    ], "name": "key", "type": "tuple"}],
     "name": "getStreamState",
     "outputs": [
         {"name": "accrued0", "type": "uint256"},
         {"name": "accrued1", "type": "uint256"},
         {"name": "currentDiscount", "type": "uint256"},
         {"name": "timeSinceLastClear", "type": "uint256"},
     ],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"components": [
        {"name": "currency0", "type": "address"}, {"name": "currency1", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "tickSpacing", "type": "int24"},
        {"name": "hooks", "type": "address"}
    ], "name": "key", "type": "tuple"},
     {"name": "zeroForOne", "type": "bool"}],
     "name": "getStreamPool",
     "outputs": [
         {"name": "sellRateCurrent", "type": "uint256"},
         {"name": "earningsFactorCurrent", "type": "uint256"},
     ],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"components": [
        {"name": "currency0", "type": "address"}, {"name": "currency1", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "tickSpacing", "type": "int24"},
        {"name": "hooks", "type": "address"}
    ], "name": "key", "type": "tuple"},
     {"name": "zeroForOne", "type": "bool"},
     {"name": "maxAmount", "type": "uint256"},
     {"name": "minDiscountBps", "type": "uint256"}],
     "name": "clear",
     "outputs": [],
     "stateMutability": "nonpayable", "type": "function"},
]


def fetch_latest_rate():
    """Fetch latest USDC borrow rate fraction strictly from the unified REST endpoint."""
    endpoints = [
        "http://rld_graphql_api:5000/api/v1/oracle/usdc-borrow-apy",  # Docker internal mesh
        f"{API_URL}/api/v1/oracle/usdc-borrow-apy"                    # Local fallback via API_URL
    ]
    
    for endpoint in endpoints:
        try:
            resp = requests.get(endpoint, timeout=2)
            resp.raise_for_status()
            rate_fraction = float(resp.json()["borrow_apy"])
            logger.info(
                "📡 Live USDC rate from REST API: r=%.6f (~%.4f%%)",
                rate_fraction,
                rate_fraction * 100,
            )
            return rate_fraction
        except Exception:
            continue
            
    logger.warning("⚠️ Oracle fetch failed across all endpoints. Maintaining previous rate.")
    return None


def rate_fraction_to_ray(rate_fraction):
    return int(rate_fraction * 1e27)


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
            TWAMM_HOOK, WAUSDC
        )
        
        # Swap executor (prefer GhostRouter for hookless deployments)
        if not _is_zero_address(GHOST_ROUTER) and GHOST_MARKET_ID:
            self.swap_executor = GhostRouterSwapExecutor(
                self.w3, self.token0, self.token1, GHOST_ROUTER, GHOST_MARKET_ID
            )
            logger.info("ℹ️  Swap path: GhostRouter (%s)", GHOST_ROUTER)
        elif SWAP_ROUTER:
            self.swap_executor = V4SwapExecutor(
                self.w3, self.token0, self.token1,
                TWAMM_HOOK, SWAP_ROUTER
            )
            logger.info("ℹ️  Swap path: LifecycleSwapRouter (%s)", SWAP_ROUTER)
        else:
            self.swap_executor = None
            logger.warning("⚠️  No swap path configured (GhostRouter/SwapRouter missing) — swaps will be skipped")
        
        # JTM Hook contract for clear auctions
        self.jtm_enabled = not _is_zero_address(TWAMM_HOOK)
        if self.jtm_enabled:
            self.jtm_hook = self.w3.eth.contract(
                address=Web3.to_checksum_address(TWAMM_HOOK),
                abi=JTM_HOOK_ABI
            )
            # Pool key tuple: (currency0, currency1, fee, tickSpacing, hooks)
            self.pool_key = (
                Web3.to_checksum_address(self.token0),
                Web3.to_checksum_address(self.token1),
                500, 5,  # fee=500, tickSpacing=5
                Web3.to_checksum_address(TWAMM_HOOK),
            )
            self._ensure_hook_approvals()
        else:
            self.jtm_hook = None
            self.pool_key = None
            logger.info("ℹ️  Hookless mode: JTM clear-auction loop disabled")
        
        self.running = True
        self.trades = 0
        self.clears = 0
        self.syncs = 0
        self.mark_price = 0.0
        self.price_source = "onchain"
    
    # ── Timestamp Sync ────────────────────────────────────────────

    def sync_timestamp(self):
        """Keep Anvil EVM TIMESTAMP opcode in sync with block headers."""
        try:
            payload = json.dumps({
                "jsonrpc": "2.0", "method": "eth_getBlockByNumber",
                "params": ["latest", False], "id": 1,
            }).encode()
            req = urllib.request.Request(
                RPC_URL, data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                result = json.loads(resp.read())
            
            latest_ts = int(result["result"]["timestamp"], 16)
            next_ts = latest_ts + 1
            
            set_payload = json.dumps({
                "jsonrpc": "2.0", "method": "evm_setNextBlockTimestamp",
                "params": [next_ts], "id": 2,
            }).encode()
            set_req = urllib.request.Request(
                RPC_URL, data=set_payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(set_req, timeout=3):
                self.syncs += 1
        except Exception:
            pass  # Non-critical, retry next cycle

    # ── Clear Auctions ────────────────────────────────────────────

    def _ensure_hook_approvals(self):
        """Ensure MM account has approved both tokens to the JTM hook."""
        if not self.jtm_hook:
            return
        hook_addr = Web3.to_checksum_address(TWAMM_HOOK)
        MAX_UINT = 2**256 - 1
        for token_contract, name in [(self.waUSDC, "waUSDC"), (self.wRLP, "wRLP")]:
            try:
                allowance = token_contract.functions.allowance(
                    self.account.address, hook_addr
                ).call()
                if allowance < 10**24:  # Re-approve if low
                    nonce = self.w3.eth.get_transaction_count(self.account.address, 'pending')
                    base_fee = self.w3.eth.gas_price or 1_000_000_000
                    tx = token_contract.functions.approve(
                        hook_addr, MAX_UINT
                    ).build_transaction({
                        'from': self.account.address, 'nonce': nonce,
                        'gas': 60000,
                        'maxFeePerGas': max(base_fee * 10, self.w3.to_wei('10', 'gwei')),
                        'maxPriorityFeePerGas': self.w3.to_wei('2', 'gwei'),
                    })
                    signed = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
                    tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                    self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
                    logger.info(f"   🔑 Approved {name} for JTM hook")
            except Exception as e:
                logger.warning(f"   ⚠️  {name} approval failed: {e}")

    def _to_usd(self, amount_raw, is_token0):
        """Convert raw token amount to approximate USD value."""
        decimals = 1e6  # Both tokens are 6 decimals
        amount = amount_raw / decimals
        if is_token0:
            # token0 is the lower address
            if self.wausdc_is_token0:
                return amount  # waUSDC ≈ $1
            else:
                return amount * self.mark_price  # wRLP
        else:
            if self.wausdc_is_token0:
                return amount * self.mark_price  # wRLP
            else:
                return amount  # waUSDC ≈ $1

    def check_and_clear(self):
        """Check accrued ghost tokens and execute clear auctions."""
        if not self.jtm_hook or not self.pool_key:
            return

        try:
            state = self.jtm_hook.functions.getStreamState(self.pool_key).call()
            accrued0, accrued1, current_discount, time_since_clear = state
        except Exception as e:
            logger.debug(f"   getStreamState failed: {e}")
            return

        accrued0_usd = self._to_usd(accrued0, True)
        accrued1_usd = self._to_usd(accrued1, False)

        # Only log when there's something interesting
        if accrued0_usd >= MIN_CLEAR_USD or accrued1_usd >= MIN_CLEAR_USD:
            t0_label = "waUSDC" if self.wausdc_is_token0 else "wRLP"
            t1_label = "wRLP" if self.wausdc_is_token0 else "waUSDC"
            logger.info(
                f"🧹 Ghost: {t0_label}={accrued0/1e6:.4f} (${accrued0_usd:.2f}) | "
                f"{t1_label}={accrued1/1e6:.4f} (${accrued1_usd:.2f}) | "
                f"Disc={current_discount/100:.1f}% | {time_since_clear}s"
            )

        # Try clearing each direction
        for zfo, accrued, accrued_usd in [(True, accrued0, accrued0_usd), (False, accrued1, accrued1_usd)]:
            if accrued == 0 or accrued_usd < MIN_CLEAR_USD:
                continue

            # Check stream has active orders
            try:
                sr, _ = self.jtm_hook.functions.getStreamPool(self.pool_key, zfo).call()
                if sr == 0:
                    continue  # No active stream
            except Exception:
                continue

            buy_label = ("waUSDC" if self.wausdc_is_token0 else "wRLP") if zfo else ("wRLP" if self.wausdc_is_token0 else "waUSDC")
            try:
                tx = self.jtm_hook.functions.clear(
                    self.pool_key, zfo, accrued, MIN_DISCOUNT_BPS
                ).build_transaction({
                    'from': self.account.address,
                    'nonce': self.w3.eth.get_transaction_count(self.account.address, 'pending'),
                    'gas': MAX_CLEAR_GAS,
                    'maxFeePerGas': max((self.w3.eth.gas_price or 1_000_000_000) * 10, self.w3.to_wei('10', 'gwei')),
                    'maxPriorityFeePerGas': self.w3.to_wei('2', 'gwei'),
                })
                signed = self.w3.eth.account.sign_transaction(tx, PRIVATE_KEY)
                tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
                if receipt['status'] == 1:
                    self.clears += 1
                    logger.info(
                        f"   ✅ Clear #{self.clears}: bought {accrued/1e6:.4f} {buy_label} "
                        f"at {current_discount/100:.1f}% discount"
                    )
            except Exception as e:
                msg = str(e)
                if "InsufficientDiscount" in msg:
                    pass  # Expected — discount hasn't built up yet
                elif "NothingToClear" in msg:
                    pass  # Already cleared by someone else
                elif "NoActiveStream" in msg:
                    pass  # Stream expired
                else:
                    logger.debug(f"   Clear failed: {msg[:120]}")

    # ── Price Getters ─────────────────────────────────────────────

    def get_index_price(self) -> float:
        """Get index price from oracle (WAD format)."""
        try:
            pool = Web3.to_checksum_address(AAVE_POOL) if AAVE_POOL else Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
            underlying = Web3.to_checksum_address(UNDERLYING_TOKEN) if UNDERLYING_TOKEN else Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
            price = self.oracle.functions.getIndexPrice(pool, underlying).call()
            return price / 1e18
        except Exception as e:
            logger.warning(f"Index price fetch failed: {e}")
            return None

    def get_prices_from_indexer(self):
        """
        Get mark/index from the event indexer API.

        Returns:
            (index_price, mark_price, source_label)
        """
        # Primary path: /api/latest (snapshot + top-level mark/index mirrors)
        try:
            response = requests.get(f"{INDEXER_URL}/api/latest", timeout=INDEXER_TIMEOUT)
            if response.status_code == 200:
                payload = response.json()

                index_raw = payload.get("index_price")
                mark_raw = payload.get("mark_price")

                if index_raw is None:
                    market = payload.get("market")
                    if isinstance(market, dict):
                        index_raw = market.get("indexPrice", market.get("index_price"))

                if mark_raw is None:
                    pool = payload.get("pool")
                    if isinstance(pool, dict):
                        mark_raw = pool.get("markPrice", pool.get("mark_price"))

                index_price = None
                mark_price = None
                if index_raw not in (None, "", "0", 0):
                    try:
                        index_price = float(index_raw)
                    except (TypeError, ValueError):
                        index_price = None
                if mark_raw not in (None, "", "0", 0):
                    try:
                        mark_price = float(mark_raw)
                    except (TypeError, ValueError):
                        mark_price = None

                if index_price is not None or mark_price is not None:
                    return index_price, mark_price, "indexer:/api/latest"
        except Exception:
            pass

        # Secondary path: /api/status
        try:
            response = requests.get(f"{INDEXER_URL}/api/status", timeout=INDEXER_TIMEOUT)
            if response.status_code == 200:
                payload = response.json()
                if payload.get("status") == "ok":
                    index_raw = payload.get("index_price")
                    mark_raw = payload.get("mark_price")
                    index_price = float(index_raw) if index_raw not in (None, "", "0", 0) else None
                    mark_price = float(mark_raw) if mark_raw not in (None, "", "0", 0) else None
                    if index_price is not None or mark_price is not None:
                        return index_price, mark_price, "indexer:/api/status"
        except Exception:
            pass

        return None, None, "indexer:unavailable"
    
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
            nonce = self.w3.eth.get_transaction_count(self.oracle_admin_account.address, 'pending')
            tx = self.oracle.functions.setRate(rate_ray).build_transaction({
                'from': self.oracle_admin_account.address,
                'nonce': nonce,
                'gas': 100000,
                'maxFeePerGas': max((self.w3.eth.gas_price or 1_000_000_000) * 10, self.w3.to_wei('10', 'gwei')),
                'maxPriorityFeePerGas': self.w3.to_wei('2', 'gwei'),
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
        rate_fraction = fetch_latest_rate()
        if rate_fraction is not None:
            rate_ray = rate_fraction_to_ray(rate_fraction)
            self.update_oracle(rate_ray)
        
        # 2. Get prices (prefer event-indexer path, fallback to direct on-chain)
        idx_from_indexer, mark_from_indexer, idx_source = self.get_prices_from_indexer()
        index_price = idx_from_indexer if idx_from_indexer is not None else self.get_index_price()
        mark_price = mark_from_indexer if mark_from_indexer is not None else self.get_mark_price()
        self.price_source = idx_source if (idx_from_indexer is not None or mark_from_indexer is not None) else "onchain"
        self.mark_price = mark_price or 0.0
        
        if index_price is None or mark_price is None:
            logger.warning("⚠️  Could not fetch prices (index=%s, mark=%s)", index_price, mark_price)
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
        except Exception:
            norm_factor_display = 1.0
        
        # 3. Calculate spread
        spread = (mark_price - index_price) / index_price
        spread_bps = spread * 10000
        
        # 4. Log status
        status = "✅" if abs(spread) < MM_THRESHOLD else ("📈" if spread < 0 else "📉")
        logger.info(
            f"📡 Index=${index_price:.4f} | 📊 Mark=${mark_price:.4f} | "
            f"NF={norm_factor_display:.10f} | Spread={spread_bps:+.2f}bps {status} "
            f"[src={self.price_source}]"
        )

        
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
        """Run daemon with two-speed loop."""
        print("\n" + "═" * 64)
        print("🤖 COMBINED DAEMON: Rate + MM + Clear + Timestamp Sync")
        print("═" * 64)
        print(f"   Oracle:      {MOCK_ORACLE_ADDR}")
        print(f"   Hook:        {TWAMM_HOOK}")
        print(f"   Clear path:  {'enabled' if self.jtm_enabled else 'disabled (hookless)'}")
        print(f"   MM threshold: {MM_THRESHOLD*100:.2f}%")
        print(f"   Fast loop:   {FAST_INTERVAL}s (sync + clear)")
        print(f"   Slow loop:   {SLOW_INTERVAL}s (rate + MM arb)")
        print(f"   Min clear:   ${MIN_CLEAR_USD}")
        print(f"   Min discount: {MIN_DISCOUNT_BPS} bps")
        print("═" * 64 + "\n")
        
        slow_counter = 0
        slow_every = max(1, SLOW_INTERVAL // FAST_INTERVAL)  # 6
        
        while self.running:
            try:
                # Fast path (every 2s): timestamp sync + clear auctions
                self.sync_timestamp()
                self.check_and_clear()
                
                # Slow path (every 12s): rate sync + MM arbitrage
                slow_counter += 1
                if slow_counter >= slow_every:
                    slow_counter = 0
                    self.cycle()
                
                time.sleep(FAST_INTERVAL)
            except KeyboardInterrupt:
                print("\n🛑 Stopping daemon...")
                self.running = False
            except Exception as e:
                logger.error(f"Loop error: {e}")
                time.sleep(FAST_INTERVAL)
        
        print(f"\n📊 Totals: {self.trades} trades | {self.clears} clears | {self.syncs} syncs")


def main():
    if not PRIVATE_KEY:
        print("❌ PRIVATE_KEY not set")
        sys.exit(1)

    # Poll indexer for deployment config (blocks until deployer has run)
    load_config_from_indexer()

    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print(f"❌ Cannot connect to {RPC_URL}")
        sys.exit(1)

    print(f"✅ Connected to {RPC_URL}")

    daemon = CombinedDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
