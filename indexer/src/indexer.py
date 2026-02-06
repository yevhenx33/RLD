# Main Indexer - Block Processing Pipeline
import asyncio
import logging
from typing import List, Optional
from dataclasses import dataclass

from .config import IndexerConfig
from .models import Block, RawEvent
from .handlers.events import create_event_router, DecodedEvent
from .contracts import EventDecoder, TOPIC_TO_EVENT

logger = logging.getLogger(__name__)

@dataclass
class BlockResult:
    """Result of processing a single block"""
    block_number: int
    block_hash: str
    events_processed: int
    reorg_detected: bool = False
    reconciled: bool = False


class BlockProcessor:
    """
    Core block processing pipeline.
    
    Flow:
    1. Fetch block header
    2. Detect reorgs
    3. Fetch logs for monitored contracts
    4. Decode and route events
    5. Generate snapshots
    6. Periodic reconciliation
    """
    
    def __init__(self, config: IndexerConfig, rpc_client, db, reconciler=None):
        self.config = config
        self.rpc = rpc_client
        self.db = db
        self.reconciler = reconciler
        self.event_router = create_event_router()
        self.event_decoder = EventDecoder()
        
    async def process_block(self, block_number: int) -> BlockResult:
        """Process a single block"""
        
        # 1. Fetch block header
        block = await self._fetch_block(block_number)
        
        # 2. Check for reorgs
        reorg = await self._detect_reorg(block)
        if reorg:
            await self._handle_reorg(block_number)
            return BlockResult(
                block_number=block_number,
                block_hash=block.block_hash,
                events_processed=0,
                reorg_detected=True,
            )
        
        # 3. Store block
        await self._store_block(block)
        
        # 4. Fetch and process logs
        events = await self._fetch_logs(block_number)
        for event in events:
            await self._process_event(event)
            
        # 5. Generate snapshots
        await self._generate_snapshots(block_number)
        
        # 6. Periodic reconciliation
        reconciled = False
        if block_number % self.config.safety.reconcile_every_n_blocks == 0:
            await self._run_reconciliation(block_number)
            reconciled = True
            
        return BlockResult(
            block_number=block_number,
            block_hash=block.block_hash,
            events_processed=len(events),
            reconciled=reconciled,
        )
    
    async def _fetch_block(self, block_number: int) -> Block:
        """Fetch block header from RPC"""
        header = await self.rpc.eth_get_block_by_number(block_number)
        return Block(
            block_number=block_number,
            block_hash=header["hash"],
            parent_hash=header["parentHash"],
            timestamp=int(header["timestamp"], 16),
        )
    
    async def _detect_reorg(self, block: Block) -> bool:
        """Check if block's parent matches our stored parent"""
        stored = await self.db.fetchrow(
            "SELECT block_hash FROM blocks WHERE block_number = $1",
            block.block_number - 1
        )
        if stored and stored["block_hash"] != block.parent_hash:
            logger.warning(f"Reorg detected at block {block.block_number}")
            return True
        return False
    
    async def _handle_reorg(self, block_number: int):
        """Mark reorged blocks and related data"""
        # Find divergence point
        depth = 0
        current = block_number - 1
        
        while depth < self.config.max_reorg_depth:
            stored = await self.db.fetchrow(
                "SELECT block_hash FROM blocks WHERE block_number = $1", current
            )
            chain_block = await self.rpc.eth_get_block_by_number(current)
            
            if stored and stored["block_hash"] == chain_block["hash"]:
                break
            
            # Mark as reorged
            await self.db.execute(
                "UPDATE blocks SET reorged = TRUE WHERE block_number = $1", current
            )
            current -= 1
            depth += 1
            
        logger.info(f"Reorg handled: rolled back {depth} blocks from {block_number}")
    
    async def _store_block(self, block: Block):
        """Insert block into database"""
        await self.db.execute("""
            INSERT INTO blocks (block_number, block_hash, parent_hash, timestamp)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (block_number) DO UPDATE SET
                block_hash = $2, parent_hash = $3, timestamp = $4, reorged = FALSE
        """, block.block_number, block.block_hash, block.parent_hash, block.timestamp)
    
    async def _fetch_logs(self, block_number: int) -> List[RawEvent]:
        """Fetch logs for all monitored contracts"""
        contracts = [
            self.config.contracts.rld_core,
            self.config.contracts.broker_factory,
        ]
        contracts = [c for c in contracts if c]  # Filter empty
        
        if not contracts:
            return []
        
        logs = await self.rpc.eth_get_logs({
            "fromBlock": hex(block_number),
            "toBlock": hex(block_number),
            "address": contracts,
        })
        
        events = []
        for log in logs:
            topics = log.get("topics", [])
            event = RawEvent(
                block_number=block_number,
                tx_hash=log["transactionHash"],
                log_index=int(log["logIndex"], 16),
                tx_index=int(log.get("transactionIndex", "0x0"), 16),
                contract_address=log["address"],
                event_name=self._get_event_name(topics[0]) if topics else "Unknown",
                topics=topics,
                event_signature=topics[0] if topics else None,
                event_data=bytes.fromhex(log["data"][2:]) if log["data"] != "0x" else b"",
            )
            events.append(event)
            
        return events
    
    def _get_event_name(self, topic: str) -> str:
        """Map topic hash to event name"""
        if not topic.startswith("0x"):
            topic = "0x" + topic
        return TOPIC_TO_EVENT.get(topic, "Unknown")
    
    async def _process_event(self, raw_event: RawEvent):
        """Decode event and route to handler"""
        # Store raw event (append-only audit trail)
        await self.db.execute("""
            INSERT INTO raw_events 
            (block_number, tx_hash, log_index, tx_index, contract_address, event_name, event_signature, event_data)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (tx_hash, log_index) DO NOTHING
        """, raw_event.block_number, raw_event.tx_hash, raw_event.log_index,
             raw_event.tx_index, raw_event.contract_address, raw_event.event_name,
             raw_event.event_signature, raw_event.event_data)
        
        # Decode and route
        decoded = self._decode_event(raw_event)
        if decoded:
            await self.event_router.route(decoded, self.db)
    
    def _decode_event(self, raw: RawEvent) -> Optional[DecodedEvent]:
        """Decode raw event data to typed event"""
        if not raw.topics:
            return None
            
        # Build a log dict from the raw event for the decoder
        decoded = self.event_decoder.decode_log({
            "blockNumber": hex(raw.block_number),
            "transactionHash": raw.tx_hash,
            "logIndex": hex(raw.log_index),
            "address": raw.contract_address,
            "topics": raw.topics,
            "data": "0x" + raw.event_data.hex() if raw.event_data else "0x",
        })
        
        if decoded:
            return DecodedEvent(
                name=decoded.event_name,
                block_number=decoded.block_number,
                tx_hash=decoded.tx_hash,
                log_index=decoded.log_index,
                contract_address=decoded.contract_address,
                args=decoded.args,
                raw_data=raw.event_data or b"",
            )
        return None
    
    async def _generate_snapshots(self, block_number: int):
        """Generate/update state snapshots for this block"""
        # Copy forward previous snapshots if no changes
        # In production, this would be more sophisticated
        pass
    
    async def _run_reconciliation(self, block_number: int):
        """Run periodic reconciliation check"""
        if self.reconciler:
            # Get all active brokers
            brokers = await self.db.fetch(
                "SELECT broker_address FROM brokers WHERE status = 'active'"
            )
            for broker in brokers:
                indexed = await self.db.fetchrow("""
                    SELECT * FROM broker_snapshots 
                    WHERE broker_address = $1 AND block_number <= $2
                    ORDER BY block_number DESC LIMIT 1
                """, broker["broker_address"], block_number)
                
                if indexed:
                    result = await self.reconciler.reconcile_broker(
                        broker["broker_address"], block_number, dict(indexed)
                    )
                    
                    await self.db.execute("""
                        INSERT INTO reconciliation_status 
                        (block_number, entity_type, entity_id, matches, drift_fields)
                        VALUES ($1, $2, $3, $4, $5)
                    """, block_number, "broker", broker["broker_address"],
                         result.matches, result.drift_fields)


class Indexer:
    """Main indexer orchestrator"""
    
    def __init__(self, config: IndexerConfig):
        self.config = config
        self.running = False
        self.processor = None
        
    async def start(self):
        """Start the indexer"""
        self.running = True
        logger.info("Starting RLD Indexer...")
        
        # Initialize RPC client and DB
        # ... (connection setup) ...
        
        # Get starting block
        last_block = await self._get_last_indexed_block()
        current_block = last_block + 1 if last_block else self.config.chain.start_block
        
        while self.running:
            try:
                # Get latest block
                latest = await self._get_chain_head()
                
                # Process blocks up to finality threshold
                safe_block = latest - self.config.chain.finality_blocks
                
                while current_block <= safe_block and self.running:
                    result = await self.processor.process_block(current_block)
                    
                    if result.events_processed > 0:
                        logger.info(f"Block {current_block}: {result.events_processed} events")
                    
                    current_block += 1
                    
                # Wait for new blocks
                await asyncio.sleep(self.config.poll_interval_seconds)
                
            except Exception as e:
                logger.error(f"Indexer error: {e}")
                await asyncio.sleep(5)
                
    async def stop(self):
        """Stop the indexer gracefully"""
        self.running = False
        
    async def _get_last_indexed_block(self) -> Optional[int]:
        """Get highest indexed block number"""
        # Query DB
        return None
    
    async def _get_chain_head(self) -> int:
        """Get current chain head"""
        # Query RPC
        return 0
