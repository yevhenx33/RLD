import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# RPC
RPC_URL = os.getenv("MAINNET_RPC_URL", "http://localhost:8545") # Fallback for local testing if needed

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data/aave_v3/raw"
CLEAN_DIR = BASE_DIR / "data/aave_v3/clean"

# Aave V3 Constants
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
TOPIC0 = "0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"
START_BLOCK = 16_950_340   # April 01 2023 00:00:11 UTC
RAY = 10**27

# Limits
RPS = 20
DEFAULT_BATCH_SIZE = 2_000

# Mapping of aToken underlying reserve to Asset Symbol
RESERVES = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
    "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "wstETH",
    "0xae78736cd615f374d3085123a210448e74fc6393": "rETH",
    "0xbe9895146f7af43049ca1c1ae358b0541ea49704": "cbETH",
    "0x5f98805a4e8be255a32880fdec7f6728c6568ba0": "LUSD",
    "0x853d955acef822db058eb8505911ed77f175b99e": "FRAX",
    "0x83f20f44975d03b1b09e64809b757c47f942beea": "sDAI",
    "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e": "crvUSD",
    "0xcd5fe23c85820f7b72d0926fc9b05b43e359b7ee": "weETH",
    "0xa35b1b31ce002fbf2058d22f30f95d405200a15b": "EthX",
    "0xd533a949740bb3306d119cc777fa900ba034cd52": "CRV",
    "0xba100000625a3754423978a60c9317c58a424e3d": "BAL",
    "0x514910771af9ca656af840dff83e8264ecf986ca": "LINK",
    "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2": "MKR",
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": "UNI",
    "0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f": "SNX",
    "0x111111111117dc0aa78b770fa6a738034120c302": "1INCH",
    "0xd33526068d116ce69f19a9ee46f0bd304f21a51f": "RPL",
    "0x5a98fcbea516cf06857215779fd812ca3bef1b32": "LDO",
    "0xae7ab96520de3a18e5e111b5eaab095312d7fe84": "stETH",
    "0x8236a87084f8b84306f72007f36f2618a5634494": "LBTC",
    "0x6c3ea9036406852006290770bedfcaba0e23a0e8": "PYUSD",
    "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": "USDe",
    "0x57e114b691db790c35207b2e685d4a43181e6061": "ENA",
}
