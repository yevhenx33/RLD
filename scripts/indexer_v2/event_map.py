"""
event_map.py — Shared topic0 → event name mapping.
Reused by mock_producer, processor, ingestor, and scraper.
"""
from web3 import Web3

EVENTS = {
    # ── RLD CORE ────────────────────────────────────────────────
    "MarketCreated(bytes32,address,address,address)":           "MarketCreated",
    "PositionModified(bytes32,address,int256,int256)":          "PositionModified",
    "MarketStateUpdated(bytes32,uint128,uint128)":              "MarketStateUpdated",
    "FundingApplied(bytes32,uint256,uint256,int256,uint256)":   "FundingApplied",
    "Liquidation(bytes32,address,address,uint256,uint256,uint256)": "Liquidation",
    "BadDebtRegistered(bytes32,uint128,uint128)":               "BadDebtRegistered",
    "BadDebtSocialized(bytes32,uint128,uint128,uint128)":       "BadDebtSocialized",
    "AccountBalanceChanged(address,address,int256,uint256,bytes32)": "AccountBalanceChanged",

    # ── FACTORY ─────────────────────────────────────────────────
    "BrokerCreated(address,address,uint256)":                   "BrokerCreated",

    # ── BROKER ──────────────────────────────────────────────────
    "OperatorUpdated(address,bool)":                            "OperatorUpdated",
    "LiquidityAdded(uint256,uint128)":                          "LiquidityAdded",
    "LiquidityRemoved(uint256,uint128,bool)":                   "LiquidityRemoved",
    "ActivePositionChanged(uint256,uint256)":                   "ActivePositionChanged",
    "TwammOrderSubmitted(bytes32,bool,uint256,uint256)":        "TwammOrderSubmitted",
    "TwammOrderCancelled(bytes32,uint256,uint256)":             "TwammOrderCancelled",
    "TwammOrderClaimed(bytes32,uint256,uint256)":               "TwammOrderClaimed",
    "ActiveTwammOrderChanged(bytes32,bytes32)":                 "ActiveTwammOrderChanged",
    "StateAudit(address,uint256,uint256,uint128,uint256,uint256)": "StateAudit",

    # ── V4 ──────────────────────────────────────────────────────
    "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)": "V4_Swap",
    "ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)": "V4_ModifyLiquidity",

    # ── ERC20 ───────────────────────────────────────────────────
    "Transfer(address,address,uint256)":                        "ERC20_Transfer",

    # ── AAVE ────────────────────────────────────────────────────
    "Supply(address,address,address,uint256,uint16)":           "Aave_Supply",
    "Mint(address,address,uint256,uint256,uint256)":            "aToken_Mint",
    "BalanceTransfer(address,address,uint256,uint256)":         "aToken_BalanceTransfer",
    "ReserveDataUpdated(address,uint256,uint256,uint256,uint256,uint256)": "Aave_ReserveDataUpdated",

    # ── ROUTER ──────────────────────────────────────────────────
    "Deposited(address,uint256,uint256)":                       "Router_Deposited",
    "SwapExecuted(address,uint8,uint256,uint256)":              "Router_SwapExecuted",
    "ShortPositionUpdated(address,uint256,uint256)":            "Router_ShortPositionUpdated",
    "ShortPositionClosed(address,uint256,uint256)":             "Router_ShortPositionClosed",
}

# Build topic0 hex → event name
# Web3.keccak().hex() returns '0x...' prefixed strings
TOPIC_MAP: dict[str, str] = {}
for sig, name in EVENTS.items():
    topic0 = Web3.keccak(text=sig).hex()
    # Ensure 0x prefix for consistent lookups
    if not topic0.startswith("0x"):
        topic0 = "0x" + topic0
    TOPIC_MAP[topic0] = name


def topic0_for(event_sig: str) -> str:
    """Compute keccak256 topic0 for an event signature string."""
    return Web3.keccak(text=event_sig).hex()


def decode_topic_address(topic: str) -> str:
    """Extract an address from a 32-byte hex topic (right-aligned)."""
    clean = topic.replace("0x", "")
    return "0x" + clean[-40:]


def decode_uint256(hex_str: str) -> int:
    """Decode a 32-byte hex string as uint256."""
    return int(hex_str.replace("0x", ""), 16)


def decode_int256(hex_str: str) -> int:
    """Decode a 32-byte hex string as int256 (two's complement)."""
    val = int(hex_str.replace("0x", ""), 16)
    if val >= 2**255:
        val -= 2**256
    return val
