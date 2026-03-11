"""
Comprehensive Block-Level Market State Indexer.
Tracks all market state changes at the block level including:
- Market state (NF, totalDebt)
- V4 pool state (price, liquidity, tick)
- Events (PositionModified, etc.)
- Broker positions
"""
import asyncio
import logging
import json
import os
from web3 import Web3
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv
import math

from db.comprehensive import (
    init_comprehensive_db,
    insert_block_state,
    insert_pool_state,
    insert_event,
    insert_broker_position,
    insert_lp_position,
    insert_transaction,
    get_last_indexed_block,
    update_last_indexed_block,
    get_block_summary,
    get_latest_summary,
    build_5m_candles,
    write_batch,
)

# Load environment
load_dotenv("../.env")
load_dotenv("../contracts/.env")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Contract ABIs (minimal for reading)
RLD_CORE_ABI = [
    {"inputs": [{"name": "id", "type": "bytes32"}], "name": "getMarketState", 
     "outputs": [{"components": [
         {"name": "normalizationFactor", "type": "uint128"},
         {"name": "totalDebt", "type": "uint128"},
         {"name": "lastUpdateTimestamp", "type": "uint48"}
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "id", "type": "bytes32"}], "name": "getMarketAddresses", 
     "outputs": [{"components": [
         {"name": "collateralToken", "type": "address"},
         {"name": "underlyingToken", "type": "address"},
         {"name": "underlyingPool", "type": "address"},
         {"name": "rateOracle", "type": "address"},
         {"name": "spotOracle", "type": "address"},
         {"name": "fundingModel", "type": "address"},
         {"name": "positionGetter", "type": "address"},
         {"name": "curator", "type": "address"},
         {"name": "liquidationModule", "type": "address"},
         {"name": "positionToken", "type": "address"}
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "id", "type": "bytes32"}, {"name": "user", "type": "address"}],
     "name": "getPosition",
     "outputs": [{"components": [
         {"name": "debtPrincipal", "type": "uint128"}
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "id", "type": "bytes32"},
        {"indexed": True, "name": "user", "type": "address"},
        {"indexed": False, "name": "deltaCollateral", "type": "int256"},
        {"indexed": False, "name": "deltaDebt", "type": "int256"}
    ], "name": "PositionModified", "type": "event"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "id", "type": "bytes32"},
        {"indexed": False, "name": "collateral", "type": "address"},
        {"indexed": False, "name": "underlying", "type": "address"},
        {"indexed": False, "name": "pool", "type": "address"}
    ], "name": "MarketCreated", "type": "event"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "id", "type": "bytes32"},
        {"indexed": False, "name": "fundingFee", "type": "int256"},
        {"indexed": False, "name": "newNormalizationFactor", "type": "uint256"}
    ], "name": "FundingApplied", "type": "event"}
]

POOL_MANAGER_ABI = [
    {"inputs": [{"name": "id", "type": "bytes32"}],
     "name": "getSlot0",
     "outputs": [
         {"name": "sqrtPriceX96", "type": "uint160"},
         {"name": "tick", "type": "int24"},
         {"name": "protocolFee", "type": "uint24"},
         {"name": "lpFee", "type": "uint24"}
     ],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "id", "type": "bytes32"}],
     "name": "getLiquidity",
     "outputs": [{"name": "", "type": "uint128"}],
     "stateMutability": "view", "type": "function"}
]

ORACLE_ABI = [
    {"inputs": [{"name": "", "type": "address"}, {"name": "", "type": "address"}],
     "name": "getIndexPrice", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"}
]

ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"}
]

PRIME_BROKER_ABI = [
    {"inputs": [], "name": "getFullState",
     "outputs": [{"components": [
         {"name": "collateralBalance", "type": "uint256"},
         {"name": "positionBalance", "type": "uint256"},
         {"name": "debtPrincipal", "type": "uint128"},
         {"name": "debtValue", "type": "uint256"},
         {"name": "twammSellOwed", "type": "uint256"},
         {"name": "twammBuyOwed", "type": "uint256"},
         {"name": "v4LPValue", "type": "uint256"},
         {"name": "netAccountValue", "type": "uint256"},
         {"name": "healthFactor", "type": "uint256"},
         {"name": "isSolvent", "type": "bool"}
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"}
]

# V4 Position Manager ABI (for LP position tracking)
POSM_ABI = [
    {"inputs": [{"name": "tokenId", "type": "uint256"}],
     "name": "getPositionLiquidity",
     "outputs": [{"name": "", "type": "uint128"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}],
     "name": "ownerOf",
     "outputs": [{"name": "", "type": "address"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "tokenId", "type": "uint256"}],
     "name": "positionInfo",
     "outputs": [{"name": "", "type": "bytes32"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [],
     "name": "nextTokenId",
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"anonymous": False, "inputs": [
        {"indexed": True, "name": "from", "type": "address"},
        {"indexed": True, "name": "to", "type": "address"},
        {"indexed": True, "name": "tokenId", "type": "uint256"}
    ], "name": "Transfer", "type": "event"}
]

STATE_VIEW_ABI = [
    {"inputs": [{"name": "poolId", "type": "bytes32"}],
     "name": "getSlot0",
     "outputs": [
         {"name": "sqrtPriceX96", "type": "uint160"},
         {"name": "tick", "type": "int24"},
         {"name": "protocolFee", "type": "uint24"},
         {"name": "lpFee", "type": "uint24"}
     ],
     "stateMutability": "view", "type": "function"},
    {"inputs": [{"name": "poolId", "type": "bytes32"}],
     "name": "getFeeGrowthGlobals",
     "outputs": [
         {"name": "feeGrowthGlobal0", "type": "uint256"},
         {"name": "feeGrowthGlobal1", "type": "uint256"}
     ],
     "stateMutability": "view", "type": "function"}
]


# V4 Quoter ABI — quoteExactInputSingle for mark price
V4_QUOTER_ABI = [
    {
        "name": "quoteExactInputSingle",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {
                "name": "params",
                "type": "tuple",
                "components": [
                    {
                        "name": "poolKey",
                        "type": "tuple",
                        "components": [
                            {"name": "currency0", "type": "address"},
                            {"name": "currency1", "type": "address"},
                            {"name": "fee", "type": "uint24"},
                            {"name": "tickSpacing", "type": "int24"},
                            {"name": "hooks", "type": "address"},
                        ],
                    },
                    {"name": "zeroForOne", "type": "bool"},
                    {"name": "exactAmount", "type": "uint128"},
                    {"name": "hookData", "type": "bytes"},
                ],
            },
        ],
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
    },
]

# Mainnet V4 Quoter (always available on mainnet fork)
V4_QUOTER_ADDRESS = os.getenv("V4_QUOTER", "0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203")


class ComprehensiveIndexer:
    """Block-level indexer for complete market state tracking."""
    
    def __init__(self, rpc_url: str, rld_core: str, pool_manager: str,
                 market_id: str, oracle_addr: str = None,
                 tracked_brokers: List[str] = None):
        """
        Initialize the indexer.
        
        Args:
            rpc_url: RPC URL for the chain
            rld_core: RLDCore contract address
            pool_manager: V4 PoolManager address
            market_id: Market ID to track (bytes32 hex)
            oracle_addr: Rate oracle address (optional)
            tracked_brokers: List of broker addresses to track positions
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.market_id = market_id
        self.market_id_bytes = bytes.fromhex(market_id.replace('0x', ''))
        self.tracked_brokers = tracked_brokers or []
        
        # Initialize contracts
        self.rld_core = self.w3.eth.contract(
            address=Web3.to_checksum_address(rld_core),
            abi=RLD_CORE_ABI
        )
        self.pool_manager = self.w3.eth.contract(
            address=Web3.to_checksum_address(pool_manager),
            abi=POOL_MANAGER_ABI
        )
        
        # Get market addresses for pool ID calculation
        try:
            self.addresses = self.rld_core.functions.getMarketAddresses(
                self.market_id_bytes
            ).call()
            self.collateral_token = self.addresses[0]
            self.position_token = self.addresses[9]
            
            # Determine token order for pool ID
            token0 = min(self.collateral_token.lower(), self.position_token.lower())
            token1 = max(self.collateral_token.lower(), self.position_token.lower())
            self.token0 = Web3.to_checksum_address(token0)
            self.token1 = Web3.to_checksum_address(token1)
            self.wausdc_is_token0 = self.collateral_token.lower() < self.position_token.lower()
            
            # Calculate pool ID (currency0, currency1, fee, tickSpacing, hooks)
            # We'll compute this from the pool key
            self.pool_id = self._compute_pool_id()
            
            # V4 Quoter for mark price (quotes 1 wRLP → waUSDC)
            try:
                twamm_hook = os.getenv("TWAMM_HOOK", "")
                if not twamm_hook:
                    try:
                        with open("/home/ubuntu/RLD/contracts/deployments.json") as f:
                            deployments = json.load(f)
                            twamm_hook = deployments.get("TWAMM") or deployments.get("TWAMMHook") or ""
                    except:
                        pass
                self._quoter = self.w3.eth.contract(
                    address=Web3.to_checksum_address(V4_QUOTER_ADDRESS),
                    abi=V4_QUOTER_ABI
                )
                # Pool key for quoter calls
                self._pool_key = (
                    Web3.to_checksum_address(self.token0),
                    Web3.to_checksum_address(self.token1),
                    500,   # fee
                    5,     # tickSpacing
                    Web3.to_checksum_address(twamm_hook) if twamm_hook else Web3.to_checksum_address("0x" + "0" * 40),
                )
                # Selling wRLP: if wRLP is token0 → zeroForOne=True, else False
                self._sell_wrlp_zfo = not self.wausdc_is_token0
                # Token decimals (both 6 for waUSDC/wRLP, but keep general)
                self._pos_decimals = 6  # wRLP decimals
                logger.info(f"   V4 Quoter: {V4_QUOTER_ADDRESS}")
            except Exception as e:
                logger.warning(f"Could not init V4 Quoter: {e}")
                self._quoter = None
            
        except Exception as e:
            logger.warning(f"Could not fetch market addresses: {e}")
            self.pool_id = None
        
        # Oracle (if provided)
        if oracle_addr:
            self.oracle = self.w3.eth.contract(
                address=Web3.to_checksum_address(oracle_addr),
                abi=ORACLE_ABI
            )
        else:
            self.oracle = None
        
        # Token contracts for balance checks
        self.collateral_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.collateral_token),
            abi=ERC20_ABI
        )
        self.position_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.position_token),
            abi=ERC20_ABI
        )
        
        # BondFactory for bond event tracking
        bond_factory_addr = os.getenv("BOND_FACTORY", "")
        if bond_factory_addr:
            self.bond_factory_addr = Web3.to_checksum_address(bond_factory_addr)
            logger.info(f"   BondFactory: {self.bond_factory_addr}")
        else:
            self.bond_factory_addr = None

        # BasisTradeFactory for basis trade event tracking
        basis_trade_factory_addr = os.getenv("BASIS_TRADE_FACTORY", "")
        if basis_trade_factory_addr:
            self.basis_trade_factory_addr = Web3.to_checksum_address(basis_trade_factory_addr)
            logger.info(f"   BasisTradeFactory: {self.basis_trade_factory_addr}")
        else:
            self.basis_trade_factory_addr = None

        # Initialize database
        init_comprehensive_db()
        
        self.running = False

        # V4 Position Manager for LP tracking
        # Try env var first, then fall back to deployment.json config
        posm_addr = os.getenv("V4_POSITION_MANAGER")
        state_view_addr = os.getenv("V4_STATE_VIEW")
        if not posm_addr:
            config_file = os.getenv("CONFIG_FILE", "/config/deployment.json")
            try:
                import json
                with open(config_file) as f:
                    cfg = json.load(f)
                posm_addr = cfg.get("v4_position_manager")
                state_view_addr = state_view_addr or cfg.get("v4_state_view")
            except Exception:
                pass
        if posm_addr:
            self.posm = self.w3.eth.contract(
                address=Web3.to_checksum_address(posm_addr),
                abi=POSM_ABI
            )
            logger.info(f"   POSM: {posm_addr}")
        else:
            self.posm = None
        if state_view_addr:
            self.state_view = self.w3.eth.contract(
                address=Web3.to_checksum_address(state_view_addr),
                abi=STATE_VIEW_ABI
            )
        else:
            self.state_view = None
        # Cache: token_id -> mint_block (so we only query entry price once)
        self._mint_block_cache = {}

        logger.info(f"✅ ComprehensiveIndexer initialized")
        logger.info(f"   Market: {market_id[:16]}...")
        logger.info(f"   Token0: {self.token0}")
        logger.info(f"   Token1: {self.token1}")
    
    def _compute_pool_id(self) -> bytes:
        """Get V4 pool ID — auto-detect from swap events or POOL_ID env var.
        
        The Solidity PoolIdLibrary.toId() hashes the PoolKey struct directly
        from memory, and reproducing this in Python can fail if the pool was
        deployed with different fee/tickSpacing than expected. To guarantee
        correctness, we:
          1. Check POOL_ID env var (explicit override)
          2. Read pool_id from existing Swap events in the DB (most reliable)
          3. Fall back to computing from PoolKey parameters
        """
        # 1. Explicit override via env var
        pool_id_env = os.getenv("POOL_ID")
        if pool_id_env:
            pool_id_bytes = bytes.fromhex(pool_id_env.replace('0x', ''))
            logger.info(f"   Pool ID (from env): {pool_id_bytes.hex()}")
            return pool_id_bytes
        
        # 2. Auto-detect from existing Swap events in the DB
        try:
            import sqlite3
            db_path = os.getenv("DB_PATH", "/data/market.db")
            db = sqlite3.connect(db_path)
            c = db.cursor()
            c.execute(
                "SELECT data FROM events WHERE event_name='Swap' ORDER BY block_number DESC LIMIT 1"
            )
            row = c.fetchone()
            db.close()
            if row:
                event_data = json.loads(row[0])
                event_pool_id = event_data.get('pool_id', '')
                if event_pool_id:
                    pool_id_bytes = bytes.fromhex(event_pool_id.replace('0x', ''))
                    logger.info(f"   Pool ID (from swap events): {pool_id_bytes.hex()}")
                    return pool_id_bytes
        except Exception as e:
            logger.debug(f"Could not read pool_id from events DB: {e}")
        
        # 3. Fallback: compute from PoolKey parameters
        try:
            twamm_hook = os.getenv("TWAMM_HOOK")
            if not twamm_hook:
                try:
                    with open("/home/ubuntu/RLD/contracts/deployments.json") as f:
                        deployments = json.load(f)
                        twamm_hook = deployments.get("TWAMM") or deployments.get("TWAMMHook")
                except:
                    pass
            
            if not twamm_hook:
                logger.warning("TWAMM hook address not found - pool state will be unavailable")
                return None
            
            logger.info(f"   TWAMM Hook: {twamm_hook}")
            
            from eth_abi import encode
            pool_key_encoded = encode(
                ['address', 'address', 'uint24', 'int24', 'address'],
                [self.token0, self.token1, 500, 5, Web3.to_checksum_address(twamm_hook)]
            )
            pool_id = Web3.keccak(pool_key_encoded)
            logger.info(f"   Pool ID (computed): {pool_id.hex()}")
            logger.warning("   ⚠️  Computed pool ID may not match the deployed pool. "
                          "Set POOL_ID env var to override.")
            return pool_id
        except Exception as e:
            logger.warning(f"Could not compute pool ID: {e}")
            return None
    
    def _get_mark_price_from_quoter(self) -> float:
        """Get mark price via V4 Quoter: quote selling 1 wRLP for waUSDC.
        
        Returns: price of 1 wRLP in waUSDC (e.g., 3.77).
        Token-ordering agnostic — works for any pair.
        """
        if not self._quoter:
            return None
        try:
            one_wrlp = 10 ** self._pos_decimals  # 1 wRLP in raw units
            params = (
                self._pool_key,       # poolKey
                self._sell_wrlp_zfo,  # zeroForOne (selling wRLP)
                one_wrlp,             # exactAmount
                b"",                  # hookData
            )
            result = self._quoter.functions.quoteExactInputSingle(params).call()
            amount_out = result[0]  # waUSDC received for 1 wRLP
            return amount_out / (10 ** self._pos_decimals)  # both 6 decimals
        except Exception as e:
            logger.debug(f"Quoter mark price failed: {e}")
            return None

    def _mark_price_from_sqrt(self, sqrt_price_x96: int) -> float:
        """Fallback: compute mark price from sqrtPriceX96.
        
        The pool was initialized by the RLDMarketFactory which already inverts
        the oracle price when needed, so the raw token1/token0 ratio directly
        gives the correct mark price (collateral per position token).
        """
        if sqrt_price_x96 == 0:
            return 0.0
        raw_price_x18 = (sqrt_price_x96 * sqrt_price_x96 * 10**18) // (2**192)
        if raw_price_x18 == 0:
            return 0.0
        # Raw price = token1/token0 = mark price (factory handles inversion at init)
        return raw_price_x18 / 10**18
    
    def get_market_state(self) -> Dict:
        """Fetch current market state from RLDCore."""
        try:
            state = self.rld_core.functions.getMarketState(self.market_id_bytes).call()
            return {
                'normalization_factor': state[0],
                'total_debt': state[1],
                'last_update_timestamp': state[2]
            }
        except Exception as e:
            logger.error(f"Failed to get market state: {e}")
            return {}
    
    def get_index_price(self) -> Optional[int]:
        """Get index price from oracle."""
        if not self.oracle:
            return None
        try:
            price = self.oracle.functions.getIndexPrice(
                Web3.to_checksum_address("0x0000000000000000000000000000000000000000"),
                Web3.to_checksum_address("0x0000000000000000000000000000000000000000")
            ).call()
            return price
        except Exception as e:
            logger.warning(f"Could not get index price: {e}")
            return None
    
    def get_pool_state(self) -> Dict:
        """Fetch current V4 pool state using extsload (storage reads)."""
        if not self.pool_id:
            return {}
        try:
            # V4 uses extsload for storage reads
            # Pool state slot = keccak256(poolId, POOLS_SLOT) where POOLS_SLOT = 6
            from eth_abi import encode
            
            # Compute pool state slot
            pools_slot = 6
            pool_id_bytes = self.pool_id if isinstance(self.pool_id, bytes) else bytes.fromhex(self.pool_id.replace('0x', ''))
            state_slot = Web3.keccak(
                pool_id_bytes + pools_slot.to_bytes(32, 'big')
            )
            
            # extsload ABI
            extsload_abi = [{
                "inputs": [{"name": "slot", "type": "bytes32"}],
                "name": "extsload",
                "outputs": [{"name": "", "type": "bytes32"}],
                "stateMutability": "view", 
                "type": "function"
            }]
            pool_manager_extsload = self.w3.eth.contract(
                address=self.pool_manager.address,
                abi=extsload_abi
            )
            
            # Read slot0 (at state_slot)
            slot0_data = pool_manager_extsload.functions.extsload(state_slot).call()
            
            # Read liquidity (at state_slot + 3)
            liquidity_slot = int.from_bytes(state_slot, 'big') + 3
            liquidity_data = pool_manager_extsload.functions.extsload(
                liquidity_slot.to_bytes(32, 'big')
            ).call()
            
            # Parse slot0: last 160 bits = sqrtPriceX96, next 24 bits = tick
            slot0_int = int.from_bytes(slot0_data, 'big')
            sqrt_price_x96 = slot0_int & ((1 << 160) - 1)
            tick_raw = (slot0_int >> 160) & ((1 << 24) - 1)
            # Sign extend tick from 24 bits
            if tick_raw >= (1 << 23):
                tick = tick_raw - (1 << 24)
            else:
                tick = tick_raw
            
            # Parse liquidity (uint128 in bottom 128 bits)
            liquidity = int.from_bytes(liquidity_data, 'big') & ((1 << 128) - 1)
            
            # Calculate mark price: prefer quoter, fallback to sqrt
            mark_price = self._get_mark_price_from_quoter()
            if mark_price is None:
                mark_price = self._mark_price_from_sqrt(sqrt_price_x96)
            
            # ── Fee growth globals ──────────────────────────────
            fee_growth0 = 0
            fee_growth1 = 0
            if self.state_view:
                try:
                    pool_id_b = self.pool_id if isinstance(self.pool_id, bytes) else bytes.fromhex(self.pool_id.replace('0x', ''))
                    fees = self.state_view.functions.getFeeGrowthGlobals(pool_id_b).call()
                    fee_growth0 = fees[0]
                    fee_growth1 = fees[1]
                except Exception as e:
                    logger.debug(f"Could not read fee growth: {e}")
            
            # ── Token balances in PoolManager ──────────────────
            token0_balance = 0
            token1_balance = 0
            try:
                t0 = self.w3.eth.contract(
                    address=Web3.to_checksum_address(self.token0),
                    abi=ERC20_ABI
                )
                t1 = self.w3.eth.contract(
                    address=Web3.to_checksum_address(self.token1),
                    abi=ERC20_ABI
                )
                token0_balance = t0.functions.balanceOf(
                    self.pool_manager.address
                ).call()
                token1_balance = t1.functions.balanceOf(
                    self.pool_manager.address
                ).call()
            except Exception as e:
                logger.debug(f"Could not read token balances: {e}")
            
            return {
                'token0': self.token0,
                'token1': self.token1,
                'sqrt_price_x96': sqrt_price_x96,
                'tick': tick,
                'liquidity': liquidity,
                'mark_price': mark_price,
                'fee_growth_global0': fee_growth0,
                'fee_growth_global1': fee_growth1,
                'token0_balance': token0_balance,
                'token1_balance': token1_balance
            }
        except Exception as e:
            logger.warning(f"Could not get pool state: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def get_broker_position(self, broker_address: str) -> Dict:
        """Get position for a specific broker via PrimeBroker.getFullState().
        
        Uses the on-chain BrokerState struct which returns all data in one call:
        - collateralBalance, positionBalance, debtPrincipal, debtValue
        - twammSellOwed, twammBuyOwed, v4LPValue
        - netAccountValue, healthFactor, isSolvent
        """
        try:
            broker_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(broker_address),
                abi=PRIME_BROKER_ABI
            )
            state = broker_contract.functions.getFullState().call()
            
            # BrokerState struct fields (in order):
            collateral_balance = state[0]  # uint256 - collateral token balance
            position_balance = state[1]    # uint256 - wRLP balance
            debt_principal = state[2]      # uint128 - raw debt principal
            debt_value = state[3]          # uint256 - principal × NF × indexPrice
            twamm_sell_owed = state[4]     # uint256
            twamm_buy_owed = state[5]      # uint256
            v4_lp_value = state[6]         # uint256
            net_account_value = state[7]   # uint256 - total assets
            health_factor_raw = state[8]   # uint256 - nav / debtValue (1e18 scale)
            is_solvent = state[9]          # bool
            
            # Health factor as float (on-chain is 1e18 scaled, max = type(uint256).max)
            health_factor = health_factor_raw / 1e18 if health_factor_raw < 2**255 else float('inf')
            
            return {
                'collateral': net_account_value,
                'debt': debt_principal,
                'debt_principal': debt_principal,
                'collateral_value': net_account_value,
                'debt_value': debt_value,
                'health_factor': health_factor,
                # Extended fields from BrokerState
                'collateral_balance': collateral_balance,
                'position_balance': position_balance,
                'twamm_sell_owed': twamm_sell_owed,
                'twamm_buy_owed': twamm_buy_owed,
                'v4_lp_value': v4_lp_value,
                'is_solvent': is_solvent
            }
        except Exception as e:
            logger.debug(f"Could not get broker position for {broker_address}: {e}")
            return {}

    def _decode_position_info(self, info_bytes32: bytes) -> dict:
        """Decode tickLower/tickUpper from V4 PositionInfo packed bytes32."""
        val = int.from_bytes(info_bytes32, 'big') if isinstance(info_bytes32, bytes) else int(info_bytes32, 16) if isinstance(info_bytes32, str) else int(info_bytes32)
        tick_lower_raw = (val >> 8) & 0xFFFFFF
        tick_upper_raw = (val >> 32) & 0xFFFFFF
        tick_lower = tick_lower_raw - 0x1000000 if tick_lower_raw >= 0x800000 else tick_lower_raw
        tick_upper = tick_upper_raw - 0x1000000 if tick_upper_raw >= 0x800000 else tick_upper_raw
        return {'tick_lower': tick_lower, 'tick_upper': tick_upper}

    def get_broker_lp_positions(self, broker_address: str, block_number: int) -> List[Dict]:
        """Get all V4 LP NFTs owned by a broker."""
        if not self.posm:
            return []
        positions = []
        try:
            broker_cs = Web3.to_checksum_address(broker_address)
            # Get active token ID from broker contract
            broker_contract = self.w3.eth.contract(
                address=broker_cs,
                abi=PRIME_BROKER_ABI + [{"inputs": [], "name": "activeTokenId",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "stateMutability": "view", "type": "function"}]
            )
            try:
                active_token_id = broker_contract.functions.activeTokenId().call()
            except:
                active_token_id = 0

            # Scan Transfer events to broker using raw get_logs (web3.py version-agnostic)
            # ERC-721 Transfer(address indexed from, address indexed to, uint256 indexed tokenId)
            # topics: [0]=sig, [1]=from, [2]=to, [3]=tokenId
            transfer_topic = Web3.keccak(text="Transfer(address,address,uint256)").hex()
            broker_padded = '0x' + broker_address.lower().replace('0x', '').zfill(64)
            logs = self.w3.eth.get_logs({
                'fromBlock': 0,
                'toBlock': 'latest',
                'address': self.posm.address,
                'topics': [transfer_topic, None, broker_padded],  # Transfer to broker
            })
            candidate_ids = list(set(
                int(log['topics'][3].hex() if hasattr(log['topics'][3], 'hex') else log['topics'][3], 16)
                for log in logs if len(log['topics']) > 3
            ))

            # Build mint block map from raw logs
            for log in logs:
                if len(log['topics']) <= 3:
                    continue
                tid_hex = log['topics'][3].hex() if hasattr(log['topics'][3], 'hex') else log['topics'][3]
                tid = int(tid_hex, 16)
                if tid not in self._mint_block_cache:
                    self._mint_block_cache[tid] = log['blockNumber']

            for token_id in candidate_ids:
                try:
                    owner = self.posm.functions.ownerOf(token_id).call()
                    if owner.lower() != broker_address.lower():
                        continue
                    liquidity = self.posm.functions.getPositionLiquidity(token_id).call()
                    if liquidity == 0:
                        continue

                    tick_lower = 0
                    tick_upper = 0
                    try:
                        info = self.posm.functions.positionInfo(token_id).call()
                        decoded = self._decode_position_info(info)
                        tick_lower = decoded['tick_lower']
                        tick_upper = decoded['tick_upper']
                    except Exception as e:
                        logger.debug(f"Could not decode position info for {token_id}: {e}")

                    # Entry price: pool tick at mint block
                    entry_tick = None
                    entry_price = None
                    mint_block = self._mint_block_cache.get(token_id)
                    if mint_block and self.state_view and self.pool_id:
                        try:
                            pool_id_bytes = self.pool_id if isinstance(self.pool_id, bytes) else bytes.fromhex(str(self.pool_id).replace('0x', ''))
                            slot0 = self.state_view.functions.getSlot0(pool_id_bytes).call(
                                block_identifier=mint_block
                            )
                            entry_tick = slot0[1]  # tick
                            entry_price = math.pow(1.0001, entry_tick)
                        except Exception as e:
                            logger.debug(f"Could not read entry price for token {token_id}: {e}")

                    positions.append({
                        'token_id': token_id,
                        'liquidity': liquidity,
                        'tick_lower': tick_lower,
                        'tick_upper': tick_upper,
                        'entry_tick': entry_tick,
                        'entry_price': entry_price,
                        'mint_block': mint_block,
                        'is_active': token_id == active_token_id,
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"Could not get LP positions for {broker_address}: {e}")
        return positions
    
    def get_events_in_block(self, block_number: int) -> List[Dict]:
        """Get all relevant events in a block using raw topic signatures."""
        events = []
        
        # Event topic signatures (keccak256 of event signature)
        EVENT_TOPICS = {
            # ERC20 Standard Events
            Web3.keccak(text="Transfer(address,address,uint256)").hex(): "Transfer",
            Web3.keccak(text="Approval(address,address,uint256)").hex(): "Approval",
            
            # RLDCore Events
            Web3.keccak(text="PositionModified(bytes32,address,int256,int256)").hex(): "PositionModified",
            Web3.keccak(text="MarketCreated(bytes32,address,address,address)").hex(): "MarketCreated",
            Web3.keccak(text="FundingApplied(bytes32,int256,uint256)").hex(): "FundingApplied",
            
            # Uniswap V4 Pool Manager Events
            Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex(): "Swap",
            Web3.keccak(text="ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)").hex(): "ModifyLiquidity",
            Web3.keccak(text="Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)").hex(): "Initialize",
            
            # TWAMM Events (match IJTM.sol signatures)
            Web3.keccak(text="SubmitOrder(bytes32,bytes32,address,uint256,uint160,bool,uint256,uint256,uint256)").hex(): "SubmitOrder",
            Web3.keccak(text="CancelOrder(bytes32,bytes32,address,uint256)").hex(): "CancelOrder",
            Web3.keccak(text="AutoSettle(bytes32,uint256,uint256,bool)").hex(): "AutoSettle",
            
            # BondFactory Events
            Web3.keccak(text="BondMinted(address,address,uint256,uint256,uint256)").hex(): "BondMinted",
            Web3.keccak(text="BondClosed(address,address,uint256,uint256)").hex(): "BondClosed",
            Web3.keccak(text="BondReturned(address,address)").hex(): "BondReturned",
            Web3.keccak(text="BondClaimed(address,address)").hex(): "BondClaimed",

            # BasisTradeFactory Events
            Web3.keccak(text="BasisTradeOpened(address,address,uint256,uint256,uint256)").hex(): "BasisTradeOpened",
            Web3.keccak(text="BasisTradeClosed(address,address,uint256)").hex(): "BasisTradeClosed",

            # Universal Router Events (just track the execute)
            Web3.keccak(text="UniversalRouterExecute()").hex(): "UniversalRouterExecute",
        }
        
        # Contracts to monitor - include PoolManager and Universal Router
        pool_manager = "0x000000000004444c5dc75cB358380D2e3dE08A90"
        universal_router = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
        
        contract_addresses = [
            self.rld_core.address.lower(),
            self.collateral_token.lower(),
            self.position_token.lower(),
            pool_manager.lower(),
        ]
        
        # Also get TWAMM hook address if available
        twamm_hook = None
        try:
            with open("/home/ubuntu/RLD/contracts/deployments.json") as f:
                deployments = json.load(f)
                twamm_hook = deployments.get("TWAMM") or deployments.get("TWAMMHook")
                if twamm_hook:
                    contract_addresses.append(twamm_hook.lower())
        except:
            pass
        
        # Add BondFactory to monitored addresses
        if self.bond_factory_addr:
            contract_addresses.append(self.bond_factory_addr.lower())
        # Add BasisTradeFactory to monitored addresses
        if self.basis_trade_factory_addr:
            contract_addresses.append(self.basis_trade_factory_addr.lower())
        
        # Add tracked brokers — PrimeBroker uses delegatecall to TWAMM hook,
        # so SubmitOrder/CancelOrder events are emitted from broker addresses
        for broker_addr in self.tracked_brokers:
            ba = broker_addr.lower()
            if ba not in contract_addresses:
                contract_addresses.append(ba)
        
        # Get ALL logs for all tracked contracts
        try:
            logs = self.w3.eth.get_logs({
                'fromBlock': block_number,
                'toBlock': block_number,
                'address': [Web3.to_checksum_address(addr) for addr in contract_addresses]
            })
            
            for log in logs:
                if not log['topics']:
                    continue
                    
                topic0 = log['topics'][0].hex()
                event_name = EVENT_TOPICS.get(topic0, None)
                contract_addr = log['address'].lower()
                
                if event_name is None:
                    # Unknown event - still record it with topic0
                    event_name = f"Unknown_{topic0[:10]}"
                
                # Parse event data based on type
                event_data = self._parse_event_data(event_name, log)
                
                events.append({
                    'event_name': event_name,
                    'tx_hash': log['transactionHash'].hex(),
                    'log_index': log['logIndex'],
                    'market_id': self.market_id,
                    'contract_address': log['address'],
                    'data': event_data
                })
                
        except Exception as e:
            logger.warning(f"Could not get logs for block {block_number}: {e}")
        
        # Also look for Universal Router swaps by checking transactions to the router
        try:
            block = self.w3.eth.get_block(block_number, full_transactions=True)
            for tx in block['transactions']:
                to_addr = tx.get('to', '').lower() if tx.get('to') else ''
                if to_addr == universal_router.lower():
                    # This is a Universal Router transaction - decode and track
                    input_data = tx.get('input', '0x')
                    method_id = input_data[:10] if len(input_data) >= 10 else input_data
                    
                    events.append({
                        'event_name': 'UniversalRouterSwap',
                        'tx_hash': tx['hash'].hex(),
                        'log_index': 9999,  # Synthetic log index
                        'market_id': self.market_id,
                        'contract_address': universal_router,
                        'data': {
                            'from': tx['from'],
                            'method_id': method_id,
                            'value': str(tx.get('value', 0))
                        }
                    })
        except Exception as e:
            logger.debug(f"Could not check Universal Router txs: {e}")
        
        return events
    
    def _parse_event_data(self, event_name: str, log: dict) -> dict:
        """Parse event log data based on event type."""
        topics = log['topics']
        data = log['data'].hex() if log['data'] else ''
        
        try:
            if event_name == "Transfer":
                return {
                    'from': '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None,
                    'to': '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None,
                    'value': str(int(data, 16)) if data else '0'
                }
            
            elif event_name == "Approval":
                return {
                    'owner': '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None,
                    'spender': '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None,
                    'value': str(int(data, 16)) if data else '0'
                }
            
            elif event_name == "PositionModified":
                # PositionModified(bytes32 indexed id, address indexed user, int256 deltaCollateral, int256 deltaDebt)
                market_id = topics[1].hex() if len(topics) > 1 else None
                user = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                # Data contains deltaCollateral (int256) and deltaDebt (int256)
                if len(data) >= 128:
                    delta_coll = int(data[0:64], 16)
                    if delta_coll >= 2**255:
                        delta_coll -= 2**256
                    delta_debt = int(data[64:128], 16)
                    if delta_debt >= 2**255:
                        delta_debt -= 2**256
                    return {
                        'market_id': market_id,
                        'user': user,
                        'deltaCollateral': str(delta_coll),
                        'deltaDebt': str(delta_debt)
                    }
                return {'market_id': market_id, 'user': user, 'raw': data}
            
            elif event_name == "Swap":
                # Swap(bytes32 indexed id, address indexed sender, int128 amount0, int128 amount1, 
                #      uint160 sqrtPriceX96After, uint128 liquidity, int24 tick, uint24 fee)
                # Note: ABI encoding pads each value to 32 bytes (64 hex chars)
                pool_id = topics[1].hex() if len(topics) > 1 else None
                sender = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                # Parse data: each field is padded to 32 bytes (64 hex chars)
                # Data layout: amount0 (32) | amount1 (32) | sqrtPriceX96 (32) | liquidity (32) | tick (32) | fee (32)
                if len(data) >= 384:  # 6 * 64 hex chars = 192 bytes = 384 hex chars
                    # amount0: int128 (signed) - first 32 bytes
                    amount0 = int(data[0:64], 16)
                    if amount0 >= 2**255:  # Check sign bit for 256-bit
                        amount0 -= 2**256
                    # amount1: int128 (signed) - second 32 bytes
                    amount1 = int(data[64:128], 16)
                    if amount1 >= 2**255:
                        amount1 -= 2**256
                    # sqrtPriceX96: uint160 - third 32 bytes
                    sqrt_price = int(data[128:192], 16)
                    # liquidity: uint128 - fourth 32 bytes
                    liquidity = int(data[192:256], 16)
                    # tick: int24 - fifth 32 bytes
                    tick = int(data[256:320], 16)
                    if tick >= 2**255:
                        tick -= 2**256
                    return {
                        'pool_id': pool_id,
                        'sender': sender,
                        'amount0': str(amount0),
                        'amount1': str(amount1),
                        'sqrtPriceX96': str(sqrt_price),
                        'liquidity': str(liquidity),
                        'tick': int(tick)
                    }
                return {'pool_id': pool_id, 'sender': sender, 'raw': data}
            
            elif event_name == "FundingApplied":
                # FundingApplied(bytes32 indexed id, int256 fundingFee, uint256 newNormalizationFactor)
                market_id = topics[1].hex() if len(topics) > 1 else None
                if len(data) >= 128:
                    funding_fee = int(data[0:64], 16)
                    if funding_fee >= 2**255:
                        funding_fee -= 2**256
                    new_nf = int(data[64:128], 16)
                    return {
                        'market_id': market_id,
                        'fundingFee': str(funding_fee),
                        'newNormalizationFactor': str(new_nf)
                    }
                return {'market_id': market_id, 'raw': data}
            
            elif event_name == "SubmitOrder":
                # SubmitOrder(bytes32 indexed poolId, bytes32 indexed orderId,
                #   address owner, uint256 amountIn, uint160 expiration,
                #   bool zeroForOne, uint256 sellRate, uint256 earningsFactorLast, uint256 startEpoch)
                pool_id = topics[1].hex() if len(topics) > 1 else None
                order_id = topics[2].hex() if len(topics) > 2 else None
                result = {'pool_id': pool_id, 'order_id': order_id}
                if len(data) >= 448:  # 7 * 64 hex chars
                    result['owner'] = '0x' + data[24:64]  # address padded to 32 bytes
                    result['amount_in'] = str(int(data[64:128], 16))
                    result['expiration'] = int(data[128:192], 16)
                    result['zero_for_one'] = int(data[192:256], 16) == 1
                    result['sell_rate'] = str(int(data[256:320], 16))
                    result['earnings_factor_last'] = str(int(data[320:384], 16))
                    result['start_epoch'] = int(data[384:448], 16)
                return result
            
            elif event_name == "CancelOrder":
                # CancelOrder(bytes32 indexed poolId, bytes32 indexed orderId, address owner, uint256 sellTokensRefund)
                pool_id = topics[1].hex() if len(topics) > 1 else None
                order_id = topics[2].hex() if len(topics) > 2 else None
                result = {'pool_id': pool_id, 'order_id': order_id}
                if len(data) >= 128:
                    result['owner'] = '0x' + data[24:64]
                    result['sell_tokens_refund'] = str(int(data[64:128], 16))
                return result
            
            elif event_name == "AutoSettle":
                # AutoSettle(bytes32 indexed poolId, uint256 ghostAmount, uint256 proceeds, bool zeroForOne)
                pool_id = topics[1].hex() if len(topics) > 1 else None
                result = {'pool_id': pool_id}
                if len(data) >= 192:
                    result['ghost_amount'] = str(int(data[0:64], 16))
                    result['proceeds'] = str(int(data[64:128], 16))
                    result['zero_for_one'] = int(data[128:192], 16) == 1
                return result
            
            elif event_name == "ModifyLiquidity":
                pool_id = topics[1].hex() if len(topics) > 1 else None
                sender = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                return {'pool_id': pool_id, 'sender': sender, 'raw': data[:100] if data else ''}
            
            elif event_name == "BondMinted":
                # BondMinted(address indexed user, address indexed broker, uint256 notional, uint256 hedge, uint256 duration)
                user = '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None
                broker = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                notional = str(int(data[0:64], 16)) if len(data) >= 64 else '0'
                hedge = str(int(data[64:128], 16)) if len(data) >= 128 else '0'
                duration = int(data[128:192], 16) if len(data) >= 192 else 0
                return {
                    'user': user, 'broker': broker,
                    'notional': notional, 'hedge': hedge, 'duration': duration
                }
            
            elif event_name == "BondClosed":
                # BondClosed(address indexed user, address indexed broker, uint256 collateralReturned, uint256 positionReturned)
                user = '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None
                broker = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                col_returned = str(int(data[0:64], 16)) if len(data) >= 64 else '0'
                pos_returned = str(int(data[64:128], 16)) if len(data) >= 128 else '0'
                return {
                    'user': user, 'broker': broker,
                    'collateralReturned': col_returned, 'positionReturned': pos_returned
                }
            
            elif event_name in ("BondReturned", "BondClaimed"):
                # BondReturned/BondClaimed(address indexed user, address indexed broker)
                user = '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None
                broker = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                return {'user': user, 'broker': broker}

            elif event_name == "BasisTradeOpened":
                # BasisTradeOpened(address indexed user, address indexed broker, uint256 amount, uint256 effectiveLeverage, uint256 duration)
                user = '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None
                broker = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                amount = str(int(data[0:64], 16)) if len(data) >= 64 else '0'
                eff_leverage = str(int(data[64:128], 16)) if len(data) >= 128 else '0'
                duration = int(data[128:192], 16) if len(data) >= 192 else 0
                return {
                    'user': user, 'broker': broker,
                    'notional': amount, 'hedge': eff_leverage, 'duration': duration
                }

            elif event_name == "BasisTradeClosed":
                # BasisTradeClosed(address indexed user, address indexed broker, uint256 sUsdeReturned)
                user = '0x' + topics[1].hex()[-40:] if len(topics) > 1 else None
                broker = '0x' + topics[2].hex()[-40:] if len(topics) > 2 else None
                susde_returned = str(int(data[0:64], 16)) if len(data) >= 64 else '0'
                return {
                    'user': user, 'broker': broker,
                    'collateralReturned': susde_returned, 'positionReturned': '0'
                }

            else:
                # Generic parsing for unknown events
                return {
                    'topics': [t.hex() for t in topics],
                    'data': data[:200] + '...' if len(data) > 200 else data
                }
                
        except Exception as e:
            logger.debug(f"Error parsing {event_name}: {e}")
            return {'error': str(e), 'raw': data[:100] if data else ''}
    
    def get_transactions_in_block(self, block_number: int) -> List[Dict]:
        """Get all transactions interacting with our contracts in a block."""
        transactions = []
        
        # Contracts to track
        tracked_contracts = {
            self.rld_core.address.lower(),
            self.collateral_token.lower(),
            self.position_token.lower(),
            self.pool_manager.address.lower()
        }
        
        try:
            block = self.w3.eth.get_block(block_number, full_transactions=True)
            
            for tx in block['transactions']:
                to_addr = tx.get('to', '').lower() if tx.get('to') else ''
                
                if to_addr in tracked_contracts:
                    # Get transaction receipt for gas used and status
                    try:
                        receipt = self.w3.eth.get_transaction_receipt(tx['hash'])
                        gas_used = receipt['gasUsed']
                        status = receipt['status']
                    except:
                        gas_used = 0
                        status = 1
                    
                    # Extract method ID (first 4 bytes of input)
                    input_data = tx.get('input', '0x')
                    method_id = input_data[:10] if len(input_data) >= 10 else input_data
                    
                    transactions.append({
                        'tx_hash': tx['hash'].hex(),
                        'tx_index': tx['transactionIndex'],
                        'from_address': tx['from'].lower(),
                        'to_address': to_addr,
                        'value': str(tx.get('value', 0)),
                        'gas_used': gas_used,
                        'gas_price': str(tx.get('gasPrice', 0)),
                        'input_data': input_data[:200],  # Store first 200 chars
                        'method_id': method_id,
                        'method_name': self._decode_method_name(method_id),
                        'decoded_args': None,  # Could add ABI decoding
                        'status': status
                    })
        except Exception as e:
            logger.debug(f"Could not get transactions: {e}")
        
        return transactions
    
    def _decode_method_name(self, method_id: str) -> str:
        """Decode method ID to human-readable name."""
        # Common method signatures
        KNOWN_METHODS = {
            '0xa9059cbb': 'transfer',
            '0x23b872dd': 'transferFrom',
            '0x095ea7b3': 'approve',
            '0x3593564c': 'execute',  # Universal Router
            '0x40c10f19': 'mint',
            '0x42842e0e': 'safeTransferFrom',
            '0x70a08231': 'balanceOf',
            '0xdd62ed3e': 'allowance',
            '0x18160ddd': 'totalSupply',
            '0x6a627842': 'mint',  # ERC20 mint
            '0x9dc29fac': 'burn',  # ERC20 burn
            '0x6352211e': 'ownerOf',
            '0x8da5cb5b': 'owner',
            '0x5c975abb': 'paused',
            '0xb6b55f25': 'deposit',
            '0x2e1a7d4d': 'withdraw',
            '0xe2bbb158': 'modifyPosition',  # Custom
            '0x36bdee88': 'modifyLiquidity',  # V4
        }
        return KNOWN_METHODS.get(method_id.lower(), 'unknown')
    
    def snapshot_block(self, block_number: int = None) -> Dict:
        """
        Take a complete snapshot of state at a given block.
        All DB writes are batched into a single transaction.
        Returns the snapshot data.
        """
        if block_number is None:
            block_number = self.w3.eth.block_number
        
        block = self.w3.eth.get_block(block_number)
        block_timestamp = block['timestamp']
        
        logger.info(f"📸 Snapshotting block {block_number} (ts: {block_timestamp})")
        
        snapshot = {
            'block_number': block_number,
            'block_timestamp': block_timestamp,
            'market_id': self.market_id
        }
        
        # ── Collect all data from RPC (no DB writes yet) ──
        
        # 1. Market State
        market_state = self.get_market_state()
        index_price = self.get_index_price()
        market_state['index_price'] = index_price or 0
        snapshot['market_state'] = market_state
        logger.info(f"   📊 Market: NF={market_state.get('normalization_factor', 0)/1e18:.10f}, "
                   f"Debt={market_state.get('total_debt', 0)/1e6:.2f}")
        
        # 2. Pool State
        pool_state = self.get_pool_state()
        pool_id_hex = self.pool_id.hex() if self.pool_id else ""
        if pool_state and self.pool_id:
            snapshot['pool_state'] = pool_state
            logger.info(f"   💧 Pool: Price=${pool_state.get('mark_price', 0):.4f}, "
                       f"Tick={pool_state.get('tick', 0)}, "
                       f"Liq={pool_state.get('liquidity', 0)}")
        
        # 3. Events (collected for batch write below)
        events = self.get_events_in_block(block_number)
        snapshot['events'] = events
        if events:
            logger.info(f"   📝 Events: {len(events)} event(s)")

        # 4 & 5: Collect broker positions, LP positions, and transactions for batch write
        broker_positions = []
        for broker in self.tracked_brokers:
            position = self.get_broker_position(broker)
            if position:
                broker_positions.append({'broker': broker, **position})
        snapshot['broker_positions'] = broker_positions
        if broker_positions:
            logger.info(f"   👤 Brokers: {len(broker_positions)} position(s) tracked")

        all_lp = []
        for broker in self.tracked_brokers:
            lp_positions_data = self.get_broker_lp_positions(broker, block_number)
            for lp in lp_positions_data:
                all_lp.append({'broker': broker, **lp})
        snapshot['lp_positions'] = all_lp
        if all_lp:
            logger.info(f"   📌 LP Positions: {len(all_lp)} NFT(s) across {len(self.tracked_brokers)} broker(s)")

        transactions = self.get_transactions_in_block(block_number)
        snapshot['transactions'] = transactions
        if transactions:
            logger.info(f"   💳 Transactions: {len(transactions)} contract interaction(s)")
        
        # ── Batched DB write: single transaction for all inserts ──
        with write_batch() as conn:
            cur = conn.cursor()
            
            # Market state
            insert_block_state(block_number, block_timestamp, self.market_id, market_state, cur=cur)
            
            # Pool state
            if pool_state and self.pool_id:
                insert_pool_state(block_number, pool_id_hex, pool_state, cur=cur)
            
            # Events
            for event in events:
                insert_event(
                    block_number, event['tx_hash'], event['log_index'],
                    event['event_name'], self.rld_core.address,
                    event['market_id'], event['data'], block_timestamp,
                    cur=cur,
                )
            
            # Bond events
            from db.comprehensive import insert_bond, update_bond_closed
            bond_events = [e for e in events if e['event_name'] in ('BondMinted', 'BondClosed', 'BondReturned', 'BondClaimed')]
            for be in bond_events:
                bd = be.get('data', {})
                try:
                    if be['event_name'] == 'BondMinted':
                        insert_bond(
                            broker_address=bd['broker'],
                            owner=bd['user'],
                            bond_factory=self.bond_factory_addr or '',
                            notional=bd.get('notional', '0'),
                            hedge=bd.get('hedge', '0'),
                            duration=bd.get('duration', 0),
                            created_block=block_number,
                            created_timestamp=block_timestamp,
                            created_tx=be['tx_hash'],
                            cur=cur,
                        )
                        if bd['broker'].lower() not in [b.lower() for b in self.tracked_brokers]:
                            self.tracked_brokers.append(bd['broker'])
                        logger.info(f"   🔗 Bond minted: broker={bd['broker'][:10]}... owner={bd['user'][:10]}...")
                    elif be['event_name'] == 'BondClosed':
                        update_bond_closed(
                            broker_address=bd['broker'],
                            closed_block=block_number,
                            closed_timestamp=block_timestamp,
                            closed_tx=be['tx_hash'],
                            collateral_returned=bd.get('collateralReturned', '0'),
                            position_returned=bd.get('positionReturned', '0'),
                            cur=cur,
                        )
                        logger.info(f"   🔓 Bond closed: broker={bd['broker'][:10]}...")
                    elif be['event_name'] in ('BondReturned', 'BondClaimed'):
                        update_bond_closed(
                            broker_address=bd['broker'],
                            closed_block=block_number,
                            closed_timestamp=block_timestamp,
                            closed_tx=be['tx_hash'],
                            cur=cur,
                        )
                        logger.info(f"   🔓 Bond {be['event_name'].lower()}: broker={bd['broker'][:10]}...")
                except Exception as ex:
                    logger.warning(f"   ⚠️  Failed to process bond event: {ex}")
            
            # Basis trade events
            basis_events = [e for e in events if e['event_name'] in ('BasisTradeOpened', 'BasisTradeClosed')]
            for be in basis_events:
                bd = be.get('data', {})
                try:
                    if be['event_name'] == 'BasisTradeOpened':
                        insert_bond(
                            broker_address=bd['broker'],
                            owner=bd['user'],
                            bond_factory=self.basis_trade_factory_addr or '',
                            notional=bd.get('notional', '0'),
                            hedge=bd.get('hedge', '0'),
                            duration=bd.get('duration', 0),
                            created_block=block_number,
                            created_timestamp=block_timestamp,
                            created_tx=be['tx_hash'],
                            cur=cur,
                        )
                        if bd['broker'].lower() not in [b.lower() for b in self.tracked_brokers]:
                            self.tracked_brokers.append(bd['broker'])
                        logger.info(f"   📈 Basis trade opened: broker={bd['broker'][:10]}... owner={bd['user'][:10]}...")
                    elif be['event_name'] == 'BasisTradeClosed':
                        update_bond_closed(
                            broker_address=bd['broker'],
                            closed_block=block_number,
                            closed_timestamp=block_timestamp,
                            closed_tx=be['tx_hash'],
                            collateral_returned=bd.get('collateralReturned', '0'),
                            position_returned=bd.get('positionReturned', '0'),
                            cur=cur,
                        )
                        logger.info(f"   📉 Basis trade closed: broker={bd['broker'][:10]}...")
                except Exception as ex:
                    logger.warning(f"   ⚠️  Failed to process basis trade event: {ex}")
            
            # Pool state override from swap events
            swap_events = [e for e in events if e['event_name'] == 'Swap']
            if swap_events:
                last_swap = swap_events[-1]
                swap_data = last_swap.get('data', {})
                swap_sqrt = int(swap_data.get('sqrtPriceX96', '0'))
                swap_tick = swap_data.get('tick', 0)
                swap_liq = int(swap_data.get('liquidity', '0'))
                swap_pool_id = swap_data.get('pool_id', '')
                
                if swap_sqrt > 0:
                    swap_mark = self._get_mark_price_from_quoter() or self._mark_price_from_sqrt(swap_sqrt)
                    
                    if swap_pool_id and not os.getenv("POOL_ID"):
                        new_pid = bytes.fromhex(swap_pool_id.replace('0x', ''))
                        if self.pool_id is None or new_pid != self.pool_id:
                            logger.info(f"   🔄 Auto-updating pool_id from swap event: {swap_pool_id}")
                            self.pool_id = new_pid
                    
                    corrected_pool_state = {
                        'token0': self.token0,
                        'token1': self.token1,
                        'sqrt_price_x96': swap_sqrt,
                        'tick': swap_tick,
                        'liquidity': swap_liq,
                        'mark_price': swap_mark,
                        'fee_growth_global0': pool_state.get('fee_growth_global0', 0) if pool_state else 0,
                        'fee_growth_global1': pool_state.get('fee_growth_global1', 0) if pool_state else 0,
                        'token0_balance': pool_state.get('token0_balance', 0) if pool_state else 0,
                        'token1_balance': pool_state.get('token1_balance', 0) if pool_state else 0
                    }
                    
                    corrected_pool_id = swap_pool_id or (self.pool_id.hex() if self.pool_id else "")
                    insert_pool_state(block_number, corrected_pool_id, corrected_pool_state, cur=cur)
                    snapshot['pool_state'] = corrected_pool_state
                    logger.info(f"   🔄 Pool (swap): Price=${swap_mark:.4f}, "
                               f"Tick={swap_tick}, Liq={swap_liq}")
            
            # Broker positions (use pre-collected data — no RPC inside write_batch)
            for bp in broker_positions:
                insert_broker_position(block_number, bp['broker'], self.market_id, bp, cur=cur)

            # LP Positions (use pre-collected data — no RPC inside write_batch)
            for lp in all_lp:
                insert_lp_position(block_number, lp['broker'], lp, cur=cur)
            
            # Transactions
            for tx in transactions:
                insert_transaction(
                    block_number, tx['tx_hash'], tx['tx_index'],
                    tx['from_address'], tx['to_address'], tx['value'],
                    tx['gas_used'], tx['gas_price'], tx['input_data'],
                    tx['method_id'], tx['method_name'], tx['decoded_args'],
                    block_timestamp, tx['status'],
                    cur=cur,
                )
            snapshot['transactions'] = transactions
            
            # Update indexer state
            update_last_indexed_block(block_number, cur=cur)
        
        # ── End of batched write (committed) ──
        
        return snapshot
    
    async def index_range(self, from_block: int, to_block: int):
        """Index a range of blocks."""
        logger.info(f"📚 Indexing blocks {from_block} to {to_block}")
        
        for block in range(from_block, to_block + 1):
            self.snapshot_block(block)
            await asyncio.sleep(0.01)  # Small delay to avoid overwhelming RPC
        
        logger.info(f"✅ Indexed {to_block - from_block + 1} blocks")
    
    async def run(self, from_block: int = None, poll_interval: int = 2):
        """
        Run the indexer continuously.
        
        Args:
            from_block: Starting block (None = resume from last indexed)
            poll_interval: Seconds between polls for new blocks
        """
        self.running = True
        
        # Determine starting block
        if from_block is None:
            from_block = get_last_indexed_block()
            if from_block == 0:
                from_block = self.w3.eth.block_number
        
        logger.info(f"🚀 Starting continuous indexer from block {from_block}")
        
        last_block = from_block - 1
        last_candle_ts = 0  # timestamp watermark for incremental candle builds
        
        while self.running:
            try:
                current_block = self.w3.eth.block_number
                
                if current_block > last_block:
                    # Index new blocks — run in thread pool to release GIL
                    # so uvicorn can serve HTTP requests concurrently
                    for block in range(last_block + 1, current_block + 1):
                        await asyncio.to_thread(self.snapshot_block, block)
                        await asyncio.sleep(0)  # yield to event loop
                    last_block = current_block

                    # Rebuild 5-minute candles incrementally after each poll
                    try:
                        written = build_5m_candles(since_ts=last_candle_ts)
                        if written:
                            logger.debug(f"🕯️  Built {written} 5M candles")
                        # Advance watermark to 2 candle-widths before now so the
                        # most recent (possibly incomplete) bucket is always refreshed
                        import time as _t
                        last_candle_ts = max(0, int(_t.time()) - 600)
                    except Exception as ce:
                        logger.warning(f"5M candle build error: {ce}")
                
                await asyncio.sleep(poll_interval)
                
            except Exception as e:
                logger.error(f"❌ Indexer error: {e}")
                await asyncio.sleep(poll_interval)
    
    def stop(self):
        """Stop the indexer."""
        logger.info("🛑 Stopping indexer...")
        self.running = False


def create_indexer_from_env() -> ComprehensiveIndexer:
    """Create indexer from environment variables and deployments.json."""
    rpc_url = os.getenv("RPC_URL", "http://localhost:8545")
    
    # Load from deployments.json
    deployments_path = "/home/ubuntu/RLD/contracts/deployments.json"
    deployments = {}
    try:
        with open(deployments_path) as f:
            deployments = json.load(f)
    except Exception as e:
        logger.warning(f"Could not load deployments.json: {e}")
    
    # RLDCore from deployments
    rld_core = deployments.get("RLDCore")
    if not rld_core:
        raise ValueError("RLDCore not found in deployments.json")
    
    # PoolManager - not in deployments.json, use mainnet V4 address
    # On mainnet fork, this is the V4 PoolManager
    pool_manager = os.getenv("POOL_MANAGER", "0x000000000004444c5dc75cB358380D2e3dE08A90")
    
    # MarketId from env (set by lifecycle script) or deployments
    market_id = os.getenv("MARKET_ID") or deployments.get("MarketId")
    if not market_id:
        raise ValueError("MARKET_ID environment variable or deployments.json entry required")
    
    # Oracle - prefer env var, fallback to deployments
    oracle = (os.getenv("MOCK_ORACLE") or 
              os.getenv("MOCK_ORACLE_ADDR") or 
              deployments.get("MockRLDAaveOracle") or 
              deployments.get("RLDAaveOracle"))
    
    # Get brokers to track from env
    user_a_broker = os.getenv("USER_A_BROKER")
    mm_broker = os.getenv("MM_BROKER")
    tracked_brokers = [b for b in [user_a_broker, mm_broker] if b]
    
    logger.info(f"Creating indexer with:")
    logger.info(f"  RPC: {rpc_url}")
    logger.info(f"  RLDCore: {rld_core}")
    logger.info(f"  PoolManager: {pool_manager}")
    logger.info(f"  MarketId: {market_id[:20]}...")
    logger.info(f"  Oracle: {oracle}")
    logger.info(f"  Brokers: {tracked_brokers}")
    
    return ComprehensiveIndexer(
        rpc_url=rpc_url,
        rld_core=rld_core,
        pool_manager=pool_manager,
        market_id=market_id,
        oracle_addr=oracle,
        tracked_brokers=tracked_brokers
    )


if __name__ == "__main__":
    import sys
    
    # Quick test - snapshot current block
    indexer = create_indexer_from_env()
    snapshot = indexer.snapshot_block()
    
    print("\n" + "="*60)
    print("📸 BLOCK SNAPSHOT")
    print("="*60)
    print(json.dumps(snapshot, indent=2, default=str))
