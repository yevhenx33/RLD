"""
Auto-discover RLD market configuration from on-chain data.
Given just RLD_CORE + MARKET_ID, resolves all addresses needed to index the market.
"""
import os
import json
import logging
from web3 import Web3

logger = logging.getLogger(__name__)

# Minimal ABIs for discovery
DISCOVERY_ABI = [
    {"inputs": [{"name": "id", "type": "bytes32"}], "name": "getMarketAddresses",
     "outputs": [{"components": [
         {"name": "collateralToken", "type": "address"},
         {"name": "underlyingToken", "type": "address"},
         {"name": "underlyingPool", "type": "address"},
         {"name": "rateOracle", "type": "address"},
         {"name": "spotOracle", "type": "address"},
         {"name": "markOracle", "type": "address"},
         {"name": "fundingModel", "type": "address"},
         {"name": "curator", "type": "address"},
         {"name": "liquidationModule", "type": "address"},
         {"name": "positionToken", "type": "address"},
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"},

    {"inputs": [{"name": "id", "type": "bytes32"}], "name": "getMarketState",
     "outputs": [{"components": [
         {"name": "normalizationFactor", "type": "uint128"},
         {"name": "totalDebt", "type": "uint128"},
         {"name": "lastUpdateTimestamp", "type": "uint48"},
     ], "name": "", "type": "tuple"}],
     "stateMutability": "view", "type": "function"},

    {"inputs": [{"name": "id", "type": "bytes32"}], "name": "isValidMarket",
     "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "view", "type": "function"},
]


def discover_market(rpc_url: str, rld_core: str, market_id: str,
                    twamm_hook: str = None, pool_manager: str = None) -> dict:
    """
    Discover all market configuration from chain.

    Required env/args:
        rpc_url: RPC endpoint
        rld_core: RLDCore contract address
        market_id: bytes32 market ID

    Optional (auto-resolved if not provided):
        twamm_hook: TWAMM hook address (must be provided or in TWAMM_HOOK env)
        pool_manager: V4 PoolManager address (defaults to mainnet)

    Returns dict with all indexer config.
    """
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    if not w3.is_connected():
        raise ConnectionError(f"Cannot connect to RPC: {rpc_url}")

    core = w3.eth.contract(
        address=Web3.to_checksum_address(rld_core),
        abi=DISCOVERY_ABI
    )

    market_id_bytes = bytes.fromhex(market_id.replace("0x", ""))

    # Verify market exists
    is_valid = core.functions.isValidMarket(market_id_bytes).call()
    if not is_valid:
        raise ValueError(f"Market {market_id} does not exist on RLDCore {rld_core}")

    # Get addresses
    addrs = core.functions.getMarketAddresses(market_id_bytes).call()
    collateral_token = addrs[0]   # waUSDC
    underlying_token = addrs[1]   # aUSDC
    rate_oracle = addrs[3]        # RLDAaveOracle or MockOracle
    position_token = addrs[9]     # wRLP

    # Get state to confirm it's alive
    state = core.functions.getMarketState(market_id_bytes).call()
    nf = state[0]
    total_debt = state[1]
    last_update = state[2]

    # Pool manager — defaults to mainnet V4
    if not pool_manager:
        pool_manager = os.getenv("POOL_MANAGER", "0x000000000004444c5dc75cB358380D2e3dE08A90")

    # TWAMM hook — must be provided
    if not twamm_hook:
        twamm_hook = os.getenv("TWAMM_HOOK")
    if not twamm_hook:
        raise ValueError("TWAMM_HOOK must be provided (not discoverable from RLDCore)")

    # Token order for V4 pool ID
    token0 = min(collateral_token.lower(), position_token.lower())
    token1 = max(collateral_token.lower(), position_token.lower())

    config = {
        "rpc_url": rpc_url,
        "rld_core": rld_core,
        "market_id": market_id,
        "pool_manager": pool_manager,
        "twamm_hook": twamm_hook,
        "collateral_token": collateral_token,
        "position_token": position_token,
        "underlying_token": underlying_token,
        "rate_oracle": rate_oracle,
        "token0": Web3.to_checksum_address(token0),
        "token1": Web3.to_checksum_address(token1),
        "normalization_factor": nf,
        "total_debt": total_debt,
        "last_update": last_update,
    }

    logger.info("═══ Market Discovery ═══")
    logger.info(f"  RLDCore:          {rld_core}")
    logger.info(f"  Market ID:        {market_id[:20]}...")
    logger.info(f"  Collateral:       {collateral_token}")
    logger.info(f"  Position Token:   {position_token}")
    logger.info(f"  Rate Oracle:      {rate_oracle}")
    logger.info(f"  TWAMM Hook:       {twamm_hook}")
    logger.info(f"  Token0:           {config['token0']}")
    logger.info(f"  Token1:           {config['token1']}")
    logger.info(f"  NF:               {nf / 1e18:.10f}")
    logger.info(f"  Total Debt:       {total_debt / 1e6:,.0f}")
    logger.info("═══════════════════════")

    return config


def discover_from_env() -> dict:
    """Discover market config from environment variables."""
    rpc_url = os.environ.get("RPC_URL", "http://localhost:8545")
    rld_core = os.environ.get("RLD_CORE")
    market_id = os.environ.get("MARKET_ID")
    twamm_hook = os.environ.get("TWAMM_HOOK")
    pool_manager = os.environ.get("POOL_MANAGER")

    if not rld_core:
        raise ValueError("RLD_CORE environment variable is required")
    if not market_id:
        raise ValueError("MARKET_ID environment variable is required")

    return discover_market(rpc_url, rld_core, market_id, twamm_hook, pool_manager)
