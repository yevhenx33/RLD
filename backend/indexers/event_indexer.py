"""
Blockchain event indexer for RLD protocol.
Monitors local Anvil chain for MarketDeployed events and indexes them into SQLite.
"""
import asyncio
import logging
import json
from web3 import Web3
from typing import Optional
from db.markets import (
    init_db, 
    insert_market, 
    market_exists, 
    get_last_indexed_block,
    update_last_indexed_block
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RLDIndexer:
    def __init__(self, rpc_url: str, factory_address: str, factory_abi: list):
        """
        Initialize the indexer.
        
        Args:
            rpc_url: Anvil RPC URL
            factory_address: RLDMarketFactory contract address
            factory_abi: Factory contract ABI
        """
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
        self.factory_address = Web3.to_checksum_address(factory_address)
        self.factory_contract = self.w3.eth.contract(
            address=self.factory_address,
            abi=factory_abi
        )
        self.running = False
        self.poll_interval = 2  # seconds
        
        # Initialize database
        init_db()
        
    async def start(self):
        """Start the indexer background task."""
        self.running = True
        logger.info("🚀 Starting RLD Indexer...")
        
        # Backfill from last indexed block
        last_block = get_last_indexed_block()
        if last_block == 0:
            # First run - start from current block
            last_block = self.w3.eth.block_number
            logger.info(f"📍 First run - starting from block {last_block}")
        else:
            logger.info(f"📍 Resuming from block {last_block}")
        
        while self.running:
            try:
                await self._poll_blocks(last_block)
                last_block = get_last_indexed_block()
                await asyncio.sleep(self.poll_interval)
            except Exception as e:
                logger.error(f"❌ Indexer error: {e}", exc_info=True)
                await asyncio.sleep(self.poll_interval)
    
    def stop(self):
        """Stop the indexer."""
        logger.info("🛑 Stopping RLD Indexer...")
        self.running = False
    
    async def _poll_blocks(self, from_block: int):
        """Poll for new blocks and process events."""
        try:
            current_block = self.w3.eth.block_number
            
            if current_block <= from_block:
                return  # No new blocks
            
            # Process blocks in batches to avoid overwhelming the RPC
            batch_size = 100
            to_block = min(from_block + batch_size, current_block)
            
            logger.info(f"🔍 Scanning blocks {from_block + 1} to {to_block}")
            
            # Get MarketDeployed events
            events = self.factory_contract.events.MarketDeployed.get_logs(
                from_block=from_block + 1,
                to_block=to_block
            )
            
            for event in events:
                await self._process_market_deployed_event(event)
            
            # Update last indexed block
            update_last_indexed_block(to_block)
            
            if events:
                logger.info(f"✅ Indexed {len(events)} market(s) up to block {to_block}")
            
        except Exception as e:
            logger.error(f"Error polling blocks: {e}", exc_info=True)
    
    async def _process_market_deployed_event(self, event):
        """Process a MarketDeployed event and insert into database."""
        try:
            tx_hash = event['transactionHash'].hex()
            
            # Skip if already indexed
            if market_exists(tx_hash):
                logger.debug(f"⏭️  Market {tx_hash} already indexed")
                return
            
            # Extract event data
            args = event['args']
            market_id = args['marketId']
            market_address = args['market']
            position_token = args['positionToken']
            params = args['params']
            
            # Get block timestamp
            block = self.w3.eth.get_block(event['blockNumber'])
            
            # Prepare market data
            market_data = {
                'tx_hash': tx_hash,
                'market_address': market_address,
                'position_token': position_token,
                'underlying_token': params['underlyingToken'],
                'collateral_token': params['collateralToken'],
                'underlying_pool': params['underlyingPool'],
                'curator': params['curator'],
                'spot_oracle': params['spotOracle'],
                'rate_oracle': params['rateOracle'],
                'liquidation_module': params['liquidationModule'],
                'min_col_ratio': params['minColRatio'],
                'maintenance_margin': params['maintenanceMargin'],
                'liquidation_close_factor': params['liquidationCloseFactor'],
                'oracle_period': params['oraclePeriod'],
                'pool_fee': params['poolFee'],
                'tick_spacing': params['tickSpacing'],
                'position_token_name': params['positionTokenName'],
                'position_token_symbol': params['positionTokenSymbol'],
                'deployment_block': event['blockNumber'],
                'deployment_timestamp': block['timestamp'],
                'status': 'active'
            }
            
            # Insert into database
            insert_market(market_data)
            logger.info(f"✅ Indexed market: {params['positionTokenSymbol']} (tx: {tx_hash[:10]}...)")
            
        except Exception as e:
            logger.error(f"Error processing event: {e}", exc_info=True)

# Global indexer instance
_indexer: Optional[RLDIndexer] = None

def get_indexer() -> Optional[RLDIndexer]:
    """Get the global indexer instance."""
    return _indexer

def init_indexer(rpc_url: str, factory_address: str, factory_abi: list) -> RLDIndexer:
    """Initialize the global indexer instance."""
    global _indexer
    _indexer = RLDIndexer(rpc_url, factory_address, factory_abi)
    return _indexer
