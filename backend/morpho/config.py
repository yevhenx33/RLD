"""
Morpho Blue Indexer — Configuration & Contract Constants.

Ethereum Mainnet addresses, ABI selectors, and indexer settings.
"""

import os

# ─── RPC ──────────────────────────────────────────────────────
MAINNET_RPC_URL = os.getenv("MAINNET_RPC_URL", "https://eth.llamarpc.com")

# ─── Core Contracts ───────────────────────────────────────────
MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"
ADAPTIVE_CURVE_IRM = "0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC"

# Morpho Blue deployment block (Jan 10, 2024)
MORPHO_GENESIS_BLOCK = 18_883_124

# ─── Function Selectors ──────────────────────────────────────

# Morpho Blue
SEL_MARKET = "0x5c60e39a"         # market(bytes32) → (uint128,uint128,uint128,uint128,uint128,uint128)
SEL_POSITION = "0x93c52062"       # position(bytes32,address) → (uint256,uint128,uint128)
SEL_ID_TO_MARKET_PARAMS = "0x2c3c9157"  # idToMarketParams(bytes32) → (address,address,address,address,uint256)

# ERC-4626 Vault
SEL_TOTAL_ASSETS = "0x01e1d114"   # totalAssets() → uint256
SEL_TOTAL_SUPPLY = "0x18160ddd"   # totalSupply() → uint256

# Oracle
SEL_PRICE = "0xa035b1fe"          # price() → uint256

# AdaptiveCurveIRM
SEL_RATE_AT_TARGET = "0xd3c520db" # rateAtUTarget(bytes32) → int256

# Multicall3
SEL_AGGREGATE3 = "0x82ad56cb"     # aggregate3((address,bool,bytes)[]) → (bool,bytes)[]

# ─── Event Signatures ────────────────────────────────────────
# keccak256 of event signatures
EVENT_CREATE_MARKET = "0xac4b2400f169220b0c0afdde7a0b32e775ba727ea1cb30b35f935cdaab8683ac"
EVENT_LIQUIDATE = "0xa4946ede45d0c6f06a0f5ce92c9ad3b4751e2b7a8ec974bfab4d08b7e7fc54a3"

# ─── Morpho API ───────────────────────────────────────────────
MORPHO_API_URL = "https://api.morpho.org/graphql"

# ─── DB ───────────────────────────────────────────────────────
DB_DIR = os.getenv("DB_DIR", os.path.join(os.path.dirname(__file__), "data"))
DB_PATH = os.path.join(DB_DIR, "morpho.db")

# ─── Indexer Settings ────────────────────────────────────────
SNAPSHOT_INTERVAL_SEC = 3600       # 1 hour
MIN_MARKET_SUPPLY_USD = 10_000     # Only track markets with >$10K supply
MULTICALL_BATCH_SIZE = 100         # Max calls per multicall
