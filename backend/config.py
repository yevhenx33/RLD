
import os

# --- CONTRACTS ---
# Aave V3 Pool (Ethereum Mainnet)
AAVE_POOL_ADDRESS = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# Uniswap V3 Pool (USDC/ETH 0.05%) for Price Oracle
UNI_POOL_ADDRESS = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640" 

# --- ASSETS TO INDEX ---
# Symbol -> Configuration
ASSETS = {
    "USDC": {
        "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "table": "rates", # Legacy table name
        "decimals": 6,
        "type": "onchain"
    },
    "DAI": {
        "address": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "table": "rates_dai",
        "decimals": 18,
        "type": "onchain"
    },
    "USDT": {
        "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "table": "rates_usdt",
        "decimals": 6,
        "type": "onchain"
    },
    "SOFR": {
        "source_file": "SOFR.xlsx",
        "table": "sofr_rates",
        "type": "offchain_file"
    }
}

# --- DATABASE ---
# --- DATABASE ---
DB_DIR = os.getenv("DB_DIR", os.path.dirname(__file__))
DB_NAME = "aave_rates.db"
DB_PATH = os.path.join(DB_DIR, DB_NAME)
CLEAN_DB_NAME = "clean_rates.db"
CLEAN_DB_PATH = os.path.join(DB_DIR, CLEAN_DB_NAME)

# --- GRAPHQL SOURCES (Fallback/History) ---
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))

ETH_PRICE_GRAPH_URL = os.getenv("ETH_PRICE_GRAPH_URL")
if not ETH_PRICE_GRAPH_URL:
    print("Warning: ETH_PRICE_GRAPH_URL not found in .env")
