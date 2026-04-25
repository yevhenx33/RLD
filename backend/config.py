
import os

from dotenv import load_dotenv

# --- CONTRACTS ---
# Aave V3 Pool (Ethereum Mainnet)
AAVE_POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# Uniswap V3 Pool (USDC/ETH 0.05%) for Price Oracle
UNI_POOL_ADDRESS = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"

# sUSDe (Ethena Staked USDe — ERC-4626 Vault)
SUSDE_ADDRESS = "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"

# ─────────────────────────────────────────────────────────────
# PROTOCOL REGISTRY
# ─────────────────────────────────────────────────────────────
# Each protocol defines:
#   - enabled: whether the daemon actively indexes it
#   - adapter: module name in rates/adapters/
#   - pool_address: the contract to call
#   - assets: dict of {symbol: {address, decimals}}
#
# To add a new protocol:
#   1. Add entry here with enabled=True
#   2. Create rates/adapters/{adapter}.py implementing ProtocolAdapter
#   3. Restart container
# ─────────────────────────────────────────────────────────────

PROTOCOLS = {
    "aave_v3": {
        "name": "Aave V3",
        "enabled": True,
        "adapter": "aave_v3",
        "pool_address": AAVE_POOL_ADDRESS,
        "assets": {
            "USDC": {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "decimals": 6},
            "DAI":  {"address": "0x6B175474E89094C44Da98b954EedeAC495271d0F", "decimals": 18},
            "USDT": {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "decimals": 6},
        },
    },
    "fluid": {
        "name": "Fluid",
        "enabled": False,
        "adapter": "fluid",
        "pool_address": None,
        "assets": {},
    },
    "euler": {
        "name": "Euler",
        "enabled": False,
        "adapter": "euler",
        "pool_address": None,
        "assets": {},
    },
}

# ─────────────────────────────────────────────────────────────
# STANDALONE DATA SOURCES (not protocol-specific)
# ─────────────────────────────────────────────────────────────

STANDALONE_SOURCES = {
    "ETH": {
        "type": "onchain_price",
        "pool_address": UNI_POOL_ADDRESS,
        "slot0_selector": "0x3850c7bd",
    },
    "sUSDe": {
        "type": "onchain_erc4626",
        "address": SUSDE_ADDRESS,
        "selector": "0x07a2d13a",  # convertToAssets(uint256)
    },
    "SOFR": {
        "type": "offchain_api",
        "url": "https://markets.newyorkfed.org/api/rates/secured/sofr/search.json",
        "genesis": "2023-03-01",
    },
}

# ─────────────────────────────────────────────────────────────
# BACKWARD-COMPAT: flat ASSETS view (used by legacy code)
# ─────────────────────────────────────────────────────────────

ASSETS = {}
for proto_id, proto in PROTOCOLS.items():
    if not proto["enabled"]:
        continue
    for symbol, asset_cfg in proto["assets"].items():
        # Legacy table names for Aave V3 (migration compat)
        legacy_table = {
            ("aave_v3", "USDC"): "rates",
            ("aave_v3", "DAI"):  "rates_dai",
            ("aave_v3", "USDT"): "rates_usdt",
        }
        ASSETS[symbol] = {
            "address": asset_cfg["address"],
            "decimals": asset_cfg["decimals"],
            "table": legacy_table.get((proto_id, symbol), f"rates_{proto_id}_{symbol.lower()}"),
            "type": "onchain",
            "protocol": proto_id,
        }

# Add standalone sources to ASSETS for backward compat
ASSETS["SOFR"] = {"source_file": "SOFR.xlsx", "table": "sofr_rates", "type": "offchain_file"}
ASSETS["sUSDe"] = {"table": "susde_yields", "type": "onchain_erc4626", "address": SUSDE_ADDRESS}

# --- DATABASE ---
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.getenv("DB_DIR", os.path.join(BACKEND_DIR, "data"))
DB_NAME = "aave_rates.db"
DB_PATH = os.path.join(DB_DIR, DB_NAME)
CLEAN_DB_NAME = "clean_rates.db"
CLEAN_DB_PATH = os.path.join(DB_DIR, CLEAN_DB_NAME)

# --- GRAPHQL SOURCES ---
load_dotenv(os.path.join(BACKEND_DIR, "../.env"))

ETH_PRICE_GRAPH_URL = os.getenv("ETH_PRICE_GRAPH_URL")
if not ETH_PRICE_GRAPH_URL:
    print("Warning: ETH_PRICE_GRAPH_URL not found in .env")
