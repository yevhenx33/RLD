# Database models matching the SQL schema
from dataclasses import dataclass, field
from typing import Optional
from decimal import Decimal
from datetime import datetime

@dataclass
class Block:
    block_number: int
    block_hash: str
    parent_hash: str
    timestamp: int
    indexed_at: Optional[datetime] = None
    reorged: bool = False

@dataclass
class Market:
    market_id: str
    collateral_token: str
    underlying_token: str
    underlying_pool: str
    position_token: Optional[str] = None
    rate_oracle: Optional[str] = None
    spot_oracle: Optional[str] = None
    funding_model: Optional[str] = None
    liquidation_module: Optional[str] = None
    curator: Optional[str] = None
    created_at_block: Optional[int] = None

@dataclass
class Broker:
    broker_address: str
    owner_address: str
    market_id: str
    collateral_token: Optional[str] = None
    position_token: Optional[str] = None
    created_at_block: Optional[int] = None
    status: str = "active"
    discovered_via: Optional[str] = None

@dataclass
class MarketSnapshot:
    market_id: str
    block_number: int
    normalization_factor: Decimal
    funding_rate: Optional[Decimal] = None
    last_funding_timestamp: Optional[int] = None
    mark_price: Optional[Decimal] = None
    index_price: Optional[Decimal] = None
    spot_price: Optional[Decimal] = None
    total_debt: Optional[Decimal] = None
    debt_cap: Optional[Decimal] = None
    sqrt_price_x96: Optional[Decimal] = None
    tick: Optional[int] = None
    liquidity: Optional[Decimal] = None
    is_filled_forward: bool = False

@dataclass
class BrokerSnapshot:
    broker_address: str
    block_number: int
    collateral_balance: Optional[Decimal] = None
    position_balance: Optional[Decimal] = None
    debt_principal: Optional[Decimal] = None
    debt_value: Optional[Decimal] = None
    net_account_value: Optional[Decimal] = None
    health_factor: Optional[Decimal] = None
    liquidation_price: Optional[Decimal] = None
    is_solvent: Optional[bool] = None
    twamm_order_id: Optional[str] = None
    twamm_sell_owed: Optional[Decimal] = None
    twamm_buy_owed: Optional[Decimal] = None
    v4_token_id: Optional[int] = None
    v4_liquidity: Optional[Decimal] = None
    v4_value: Optional[Decimal] = None
    is_filled_forward: bool = False

@dataclass
class RawEvent:
    block_number: int
    tx_hash: str
    log_index: int
    contract_address: str
    event_name: str
    topics: Optional[list] = None  # Full list of topic hashes
    tx_index: Optional[int] = None
    event_signature: Optional[str] = None
    event_data: Optional[bytes] = None
    decoded_data: Optional[dict] = None
    ref: Optional[str] = None
    id: Optional[int] = None

@dataclass
class FundingUpdate:
    market_id: str
    block_number: int
    tx_hash: str
    old_norm_factor: Decimal
    new_norm_factor: Decimal
    funding_rate: Optional[Decimal] = None
    time_delta: Optional[int] = None
    id: Optional[int] = None

@dataclass
class PositionChange:
    market_id: str
    broker_address: str
    block_number: int
    tx_hash: str
    delta_collateral: Optional[Decimal] = None
    delta_debt: Optional[Decimal] = None
    new_debt_principal: Optional[Decimal] = None
    id: Optional[int] = None

@dataclass
class ReconciliationStatus:
    block_number: int
    entity_type: str
    entity_id: str
    matches: bool
    primary_hash: Optional[str] = None
    secondary_hash: Optional[str] = None
    drift_fields: Optional[dict] = None
    action: Optional[str] = None
    id: Optional[int] = None

@dataclass
class LiquidationCandidate:
    broker_address: str
    market_id: str
    block_number: int
    health_factor: Optional[Decimal] = None
    debt_value: Optional[Decimal] = None
    shortfall: Optional[Decimal] = None
    priority_score: Optional[int] = None
    status: str = "pending"
    executed_tx: Optional[str] = None

@dataclass
class InvariantCheck:
    block_number: int
    all_passed: bool
    wrlp_supply_matches_debt: Optional[bool] = None
    all_markets_consistent: Optional[bool] = None
    nf_monotonic: Optional[bool] = None
    all_balances_positive: Optional[bool] = None
    failure_details: Optional[dict] = None
