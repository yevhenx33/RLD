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

from comprehensive_indexer_db import (
    init_comprehensive_db,
    insert_block_state,
    insert_pool_state,
    insert_event,
    insert_broker_position,
    get_last_indexed_block,
    update_last_indexed_block,
    get_block_summary,
    get_latest_summary
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
     "outputs": [{"name": "collateral", "type": "int256"}, {"name": "debtPrincipal", "type": "int256"}],
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
    ], "name": "MarketCreated", "type": "event"}
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
        
        # Initialize database
        init_comprehensive_db()
        
        self.running = False
        logger.info(f"✅ ComprehensiveIndexer initialized")
        logger.info(f"   Market: {market_id[:16]}...")
        logger.info(f"   Token0: {self.token0}")
        logger.info(f"   Token1: {self.token1}")
    
    def _compute_pool_id(self) -> bytes:
        """Compute V4 pool ID from pool key components."""
        # Pool ID is keccak256 of the PoolKey struct
        try:
            # Get TWAMM hook address - try deployments.json first, then env var
            twamm_hook = None
            try:
                with open("/home/ubuntu/RLD/contracts/deployments.json") as f:
                    deployments = json.load(f)
                    # Try both possible keys
                    twamm_hook = deployments.get("TWAMM") or deployments.get("TWAMMHook")
            except:
                pass
            
            # Fallback to env var
            if not twamm_hook:
                twamm_hook = os.getenv("TWAMM_HOOK")
            
            if not twamm_hook:
                logger.warning("TWAMM hook address not found - pool state will be unavailable")
                return None
            
            logger.info(f"   TWAMM Hook: {twamm_hook}")
            
            # PoolKey: currency0, currency1, fee (500), tickSpacing (5), hooks
            # These values MUST match the deployed pool!
            from eth_abi import encode
            pool_key_encoded = encode(
                ['address', 'address', 'uint24', 'int24', 'address'],
                [self.token0, self.token1, 500, 5, Web3.to_checksum_address(twamm_hook)]
            )
            pool_id = Web3.keccak(pool_key_encoded)
            logger.info(f"   Pool ID: {pool_id.hex()}")
            return pool_id
        except Exception as e:
            logger.warning(f"Could not compute pool ID: {e}")
            return None
    
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
            
            # Calculate mark price from sqrtPriceX96
            # Match Solidity: rawPriceX18 = sqrtPriceX96² × 1e18 / 2^192
            # This gives price = token1/token0 in 18-decimal precision
            if sqrt_price_x96 > 0:
                raw_price_x18 = (sqrt_price_x96 * sqrt_price_x96 * 10**18) // (2**192)
                
                # If waUSDC is token0: rawPrice = wRLP/waUSDC, need to invert
                # If waUSDC is token1: rawPrice = waUSDC/wRLP (correct)
                if self.wausdc_is_token0:
                    # Need to invert: wrlpPriceX18 = 1e18² / rawPriceX18
                    mark_price = (10**36 / raw_price_x18) / 10**18 if raw_price_x18 > 0 else 0
                else:
                    mark_price = raw_price_x18 / 10**18
            else:
                mark_price = 0
            
            return {
                'token0': self.token0,
                'token1': self.token1,
                'sqrt_price_x96': sqrt_price_x96,
                'tick': tick,
                'liquidity': liquidity,
                'mark_price': mark_price,
                'fee_growth_global0': 0,
                'fee_growth_global1': 0
            }
        except Exception as e:
            logger.warning(f"Could not get pool state: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def get_broker_position(self, broker_address: str) -> Dict:
        """Get position for a specific broker."""
        try:
            position = self.rld_core.functions.getPosition(
                self.market_id_bytes,
                Web3.to_checksum_address(broker_address)
            ).call()
            
            collateral = position[0]
            debt_principal = position[1]
            
            # Get market state for debt calculation
            state = self.get_market_state()
            nf = state.get('normalization_factor', 1e18)
            
            # Actual debt = principal * NF
            actual_debt = (debt_principal * nf) // (10**18) if nf > 0 else 0
            
            # Health factor calculation would need oracle prices
            # Simplified: collateral / debt if debt > 0
            health_factor = 0.0
            if actual_debt > 0 and collateral > 0:
                health_factor = collateral / actual_debt
            
            return {
                'collateral': collateral,
                'debt': actual_debt,
                'debt_principal': debt_principal,
                'collateral_value': collateral,  # In collateral token units
                'debt_value': actual_debt,
                'health_factor': health_factor
            }
        except Exception as e:
            logger.warning(f"Could not get broker position for {broker_address}: {e}")
            return {}
    
    def get_events_in_block(self, block_number: int) -> List[Dict]:
        """Get all relevant events in a block."""
        events = []
        
        try:
            # PositionModified events
            pos_events = self.rld_core.events.PositionModified.get_logs(
                from_block=block_number,
                to_block=block_number
            )
            for e in pos_events:
                events.append({
                    'event_name': 'PositionModified',
                    'tx_hash': e['transactionHash'].hex(),
                    'log_index': e['logIndex'],
                    'market_id': e['args']['id'].hex(),
                    'data': {
                        'user': e['args']['user'],
                        'deltaCollateral': str(e['args']['deltaCollateral']),
                        'deltaDebt': str(e['args']['deltaDebt'])
                    }
                })
        except Exception as e:
            logger.debug(f"Could not get PositionModified events: {e}")
        
        try:
            # MarketCreated events
            market_events = self.rld_core.events.MarketCreated.get_logs(
                from_block=block_number,
                to_block=block_number
            )
            for e in market_events:
                events.append({
                    'event_name': 'MarketCreated',
                    'tx_hash': e['transactionHash'].hex(),
                    'log_index': e['logIndex'],
                    'market_id': e['args']['id'].hex(),
                    'data': {
                        'collateral': e['args']['collateral'],
                        'underlying': e['args']['underlying'],
                        'pool': e['args']['pool']
                    }
                })
        except Exception as e:
            logger.debug(f"Could not get MarketCreated events: {e}")
        
        return events
    
    def snapshot_block(self, block_number: int = None) -> Dict:
        """
        Take a complete snapshot of state at a given block.
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
        
        # 1. Market State
        market_state = self.get_market_state()
        index_price = self.get_index_price()
        market_state['index_price'] = index_price or 0
        
        insert_block_state(block_number, block_timestamp, self.market_id, market_state)
        snapshot['market_state'] = market_state
        logger.info(f"   📊 Market: NF={market_state.get('normalization_factor', 0)/1e18:.10f}, "
                   f"Debt={market_state.get('total_debt', 0)/1e6:.2f}")
        
        # 2. Pool State
        pool_state = self.get_pool_state()
        if pool_state and self.pool_id:
            insert_pool_state(block_number, self.pool_id.hex() if self.pool_id else "", pool_state)
            snapshot['pool_state'] = pool_state
            logger.info(f"   💧 Pool: Price=${pool_state.get('mark_price', 0):.4f}, "
                       f"Tick={pool_state.get('tick', 0)}, "
                       f"Liq={pool_state.get('liquidity', 0)}")
        
        # 3. Events
        events = self.get_events_in_block(block_number)
        for event in events:
            insert_event(
                block_number, event['tx_hash'], event['log_index'],
                event['event_name'], self.rld_core.address,
                event['market_id'], event['data'], block_timestamp
            )
        snapshot['events'] = events
        if events:
            logger.info(f"   📝 Events: {len(events)} event(s)")
        
        # 4. Broker Positions
        broker_positions = []
        for broker in self.tracked_brokers:
            position = self.get_broker_position(broker)
            if position:
                insert_broker_position(block_number, broker, self.market_id, position)
                broker_positions.append({'broker': broker, **position})
        snapshot['broker_positions'] = broker_positions
        if broker_positions:
            logger.info(f"   👤 Brokers: {len(broker_positions)} position(s) tracked")
        
        # Update indexer state
        update_last_indexed_block(block_number)
        
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
        
        while self.running:
            try:
                current_block = self.w3.eth.block_number
                
                if current_block > last_block:
                    # Index new blocks
                    for block in range(last_block + 1, current_block + 1):
                        self.snapshot_block(block)
                    last_block = current_block
                
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
