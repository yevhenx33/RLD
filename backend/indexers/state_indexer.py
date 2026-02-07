"""
Market State Indexer.
Polls RLDCore to get live market state data (normalizationFactor, totalDebt, etc.)
Stores snapshots in the separate market_state.db.
"""
import asyncio
import logging
import json
import os
from web3 import Web3
from typing import Optional, List
from db.market_state import (
    init_market_state_db,
    upsert_market,
    upsert_risk_params,
    insert_state_snapshot,
    get_all_market_ids,
    market_exists_by_id,
    get_state_indexer_last_block,
    update_state_indexer_block
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MarketStateIndexer:
    """
    Indexes live market state from RLDCore.
    - Listens for MarketDeployed events to discover new markets
    - Periodically polls getMarketState() for all known markets
    - Stores state snapshots for historical tracking
    """
    
    def __init__(self, rpc_url: str, core_address: str, factory_address: str, 
                 core_abi: list, factory_abi: list):
        """
        Initialize the market state indexer.
        
        Args:
            rpc_url: Anvil RPC URL
            core_address: RLDCore contract address
            factory_address: RLDMarketFactory contract address
            core_abi: RLDCore ABI
            factory_abi: Factory ABI (for event parsing)
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.core_address = Web3.to_checksum_address(core_address)
        self.factory_address = Web3.to_checksum_address(factory_address)
        
        self.core_contract = self.w3.eth.contract(
            address=self.core_address,
            abi=core_abi
        )
        self.factory_contract = self.w3.eth.contract(
            address=self.factory_address,
            abi=factory_abi
        )
        
        self.running = False
        self.event_poll_interval = 3  # seconds - for new markets
        self.state_poll_interval = 10  # seconds - for state updates
        
        # Initialize separate database
        init_market_state_db()
        logger.info(f"✅ MarketStateIndexer initialized")
        logger.info(f"   Core: {self.core_address}")
        logger.info(f"   Factory: {self.factory_address}")
    
    async def start(self):
        """Start the indexer background tasks."""
        self.running = True
        logger.info("🚀 Starting MarketStateIndexer...")
        
        # Run both tasks concurrently
        await asyncio.gather(
            self._poll_market_events(),
            self._poll_market_states()
        )
    
    def stop(self):
        """Stop the indexer."""
        logger.info("🛑 Stopping MarketStateIndexer...")
        self.running = False
    
    async def _poll_market_events(self):
        """Poll for MarketDeployed events to discover new markets."""
        last_block = get_state_indexer_last_block()
        if last_block == 0:
            last_block = self.w3.eth.block_number - 100  # Start from recent
            logger.info(f"📍 First run - starting from block {last_block}")
        
        while self.running:
            try:
                current_block = self.w3.eth.block_number
                
                if current_block > last_block:
                    batch_size = 100
                    to_block = min(last_block + batch_size, current_block)
                    
                    # Get MarketDeployed events
                    try:
                        events = self.factory_contract.events.MarketDeployed.get_logs(
                            from_block=last_block + 1,
                            to_block=to_block
                        )
                        
                        for event in events:
                            await self._process_market_deployed(event)
                        
                        update_state_indexer_block(to_block)
                        last_block = to_block
                        
                        if events:
                            logger.info(f"✅ Found {len(events)} new market(s)")
                            
                    except Exception as e:
                        # Event may not exist or different signature
                        logger.debug(f"Event query error (may be normal): {e}")
                        update_state_indexer_block(to_block)
                        last_block = to_block
                
                await asyncio.sleep(self.event_poll_interval)
                
            except Exception as e:
                logger.error(f"❌ Event poll error: {e}")
                await asyncio.sleep(self.event_poll_interval)
    
    async def _process_market_deployed(self, event):
        """Process a MarketDeployed event."""
        try:
            args = event['args']
            market_id = args['id'].hex() if isinstance(args['id'], bytes) else args['id']
            
            # Skip if already indexed
            if market_exists_by_id(market_id):
                return
            
            # Get block timestamp
            block = self.w3.eth.get_block(event['blockNumber'])
            
            # Store market metadata
            market_data = {
                'market_id': market_id,
                'tx_hash': event['transactionHash'].hex(),
                'broker_factory': args.get('brokerFactory'),
                'position_token': args.get('positionToken'),
                'position_token_symbol': None,  # Will be fetched from state
                'collateral_token': args.get('collateral'),
                'underlying_token': args.get('underlyingPool'),
                'deployment_block': event['blockNumber'],
                'deployment_timestamp': block['timestamp']
            }
            
            upsert_market(market_data)
            logger.info(f"📝 Indexed new market: {market_id[:16]}...")
            
            # Immediately fetch and store state
            await self._fetch_and_store_state(market_id)
            
        except Exception as e:
            logger.error(f"Error processing MarketDeployed: {e}", exc_info=True)
    
    async def _poll_market_states(self):
        """Periodically poll market states for all known markets."""
        while self.running:
            try:
                market_ids = get_all_market_ids()
                
                if market_ids:
                    logger.debug(f"📊 Polling state for {len(market_ids)} market(s)")
                    
                    for market_id in market_ids:
                        await self._fetch_and_store_state(market_id)
                        await asyncio.sleep(0.1)  # Small delay between calls
                
                await asyncio.sleep(self.state_poll_interval)
                
            except Exception as e:
                logger.error(f"❌ State poll error: {e}")
                await asyncio.sleep(self.state_poll_interval)
    
    async def _fetch_and_store_state(self, market_id: str):
        """Fetch market state from RLDCore and store snapshot."""
        try:
            # Convert market_id to bytes32
            market_id_bytes = bytes.fromhex(market_id.replace('0x', ''))
            
            # Get market state
            state = self.core_contract.functions.getMarketState(market_id_bytes).call()
            config = self.core_contract.functions.getMarketConfig(market_id_bytes).call()
            addresses = self.core_contract.functions.getMarketAddresses(market_id_bytes).call()
            
            current_block = self.w3.eth.block_number
            
            # Store state snapshot
            state_data = {
                'normalization_factor': state[0],  # uint128
                'total_debt': state[1],            # uint128
                'last_update_timestamp': state[2]  # uint48
            }
            insert_state_snapshot(market_id, state_data, current_block)
            
            # Update risk params
            risk_params = {
                'min_col_ratio': config[0],              # uint64
                'maintenance_margin': config[1],         # uint64
                'liquidation_close_factor': config[2],   # uint64
                'funding_period': config[3],             # uint32
                'debt_cap': config[4],                   # uint128
                'broker_verifier': config[6]             # address (index 6 after bytes32)
            }
            upsert_risk_params(market_id, risk_params)
            
            # Update market addresses if we have them
            market_update = {
                'market_id': market_id,
                'collateral_token': addresses[0],     # collateralToken
                'underlying_token': addresses[1],     # underlyingToken
                'underlying_pool': addresses[2],      # underlyingPool
                'rate_oracle': addresses[3],          # rateOracle
                'spot_oracle': addresses[4],          # spotOracle
                'curator': addresses[7],              # curator
                'liquidation_module': addresses[8],   # liquidationModule
                'position_token': addresses[9]        # positionToken
            }
            upsert_market(market_update)
            
            logger.debug(f"📊 Updated state for {market_id[:16]}...")
            
        except Exception as e:
            logger.warning(f"Could not fetch state for {market_id[:16]}: {e}")

# Manual market registration (for markets deployed outside indexer)
def register_market_manually(market_id: str, w3: Web3, core_contract):
    """
    Manually register a market that was deployed before the indexer started.
    """
    try:
        # Normalize market_id to have 0x prefix
        if not market_id.startswith('0x'):
            market_id = '0x' + market_id
        
        market_id_bytes = bytes.fromhex(market_id.replace('0x', ''))
        
        # Verify market exists
        is_valid = core_contract.functions.isValidMarket(market_id_bytes).call()
        if not is_valid:
            logger.error(f"Market {market_id} is not valid")
            return False
        
        # Get addresses
        addresses = core_contract.functions.getMarketAddresses(market_id_bytes).call()
        position_token_addr = addresses[9]
        
        # Try to get position token symbol
        position_token_symbol = None
        try:
            # Minimal ERC20 ABI for symbol()
            erc20_abi = [{"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"}]
            token_contract = w3.eth.contract(address=position_token_addr, abi=erc20_abi)
            position_token_symbol = token_contract.functions.symbol().call()
        except Exception as e:
            logger.warning(f"Could not fetch token symbol: {e}")
        
        market_data = {
            'market_id': market_id,  # Normalized with 0x prefix
            'collateral_token': addresses[0],
            'underlying_token': addresses[1],
            'underlying_pool': addresses[2],
            'rate_oracle': addresses[3],
            'spot_oracle': addresses[4],
            'curator': addresses[7],
            'liquidation_module': addresses[8],
            'position_token': position_token_addr,
            'position_token_symbol': position_token_symbol
        }
        
        upsert_market(market_data)
        logger.info(f"✅ Manually registered market: {market_id[:16]}... ({position_token_symbol})")
        return True
        
    except Exception as e:
        logger.error(f"Failed to register market: {e}")
        return False

# Global indexer instance
_state_indexer: Optional[MarketStateIndexer] = None

def get_state_indexer() -> Optional[MarketStateIndexer]:
    """Get the global state indexer instance."""
    return _state_indexer

def init_state_indexer(rpc_url: str, core_address: str, factory_address: str,
                       core_abi: list, factory_abi: list) -> MarketStateIndexer:
    """Initialize the global state indexer instance."""
    global _state_indexer
    _state_indexer = MarketStateIndexer(
        rpc_url, core_address, factory_address, core_abi, factory_abi
    )
    return _state_indexer
