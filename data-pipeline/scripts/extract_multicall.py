import asyncio
import os
import lzma
from datetime import datetime, timezone
import asyncpg
from web3 import AsyncWeb3, AsyncHTTPProvider
from web3.exceptions import ContractLogicError
from dotenv import load_dotenv

load_dotenv()

# Web3 Setup
RPC_URL = os.getenv("MAINNET_RPC_URL")
w3 = AsyncWeb3(AsyncHTTPProvider(RPC_URL))

# Core Stable ABIs
MULTICALL3_ADDR = "0xcA11bde05977b3631167028862bE2a173976CA11"
ADDRESS_PROVIDER = "0x2f39d218133AFaB8F2B819B1066c7E434Ad94E9e"

MULTICALL3_ABI = [{
    "inputs": [
        {"internalType": "bool", "name": "requireSuccess", "type": "bool"},
        {"components": [
            {"internalType": "address", "name": "target", "type": "address"},
            {"internalType": "bytes", "name": "callData", "type": "bytes"}
        ], "internalType": "struct Multicall3.Call[]", "name": "calls", "type": "tuple[]"}
    ],
    "name": "tryAggregate",
    "outputs": [
        {"components": [
            {"internalType": "bool", "name": "success", "type": "bool"},
            {"internalType": "bytes", "name": "returnData", "type": "bytes"}
        ], "internalType": "struct Multicall3.Result[]", "name": "", "type": "tuple[]"}
    ],
    "stateMutability": "payable",
    "type": "function"
}]

PROVIDER_ABI = [
    {"inputs":[],"name":"getPool","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"getPriceOracle","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}
]

POOL_ABI = [
    {"inputs":[],"name":"getReservesList","outputs":[{"internalType":"address[]","name":"","type":"address[]"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getReserveData","outputs":[{"components":[{"components":[{"internalType":"uint256","name":"data","type":"uint256"}],"internalType":"struct DataTypes.ReserveConfigurationMap","name":"configuration","type":"tuple"},{"internalType":"uint128","name":"liquidityIndex","type":"uint128"},{"internalType":"uint128","name":"currentLiquidityRate","type":"uint128"},{"internalType":"uint128","name":"variableBorrowIndex","type":"uint128"},{"internalType":"uint128","name":"currentVariableBorrowRate","type":"uint128"},{"internalType":"uint128","name":"currentStableBorrowRate","type":"uint128"},{"internalType":"uint40","name":"lastUpdateTimestamp","type":"uint40"},{"internalType":"uint16","name":"id","type":"uint16"},{"internalType":"address","name":"aTokenAddress","type":"address"},{"internalType":"address","name":"stableDebtTokenAddress","type":"address"},{"internalType":"address","name":"variableDebtTokenAddress","type":"address"},{"internalType":"address","name":"interestRateStrategyAddress","type":"address"},{"internalType":"uint128","name":"accToTreasury","type":"uint128"},{"internalType":"uint128","name":"unbacked","type":"uint128"},{"internalType":"uint128","name":"isolationModeTotalDebt","type":"uint128"}],"internalType":"struct DataTypes.ReserveData","name":"","type":"tuple"}],"stateMutability":"view","type":"function"}
]

ORACLE_ABI = [
    {"inputs":[{"internalType":"address","name":"asset","type":"address"}],"name":"getAssetPrice","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]

ERC20_ABI = [
    {"inputs":[],"name":"totalSupply","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"symbol","outputs":[{"internalType":"string","name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],"stateMutability":"view","type":"function"}
]

RAY = 10**27
START_BLOCK = 16_950_340
BLOCKS_PER_HOUR = 300
db_config = {"user": "postgres", "password": "postgres", "database": "rld_data", "host": "127.0.0.1", "port": 5433}

# Known overrides for Aave symbols to normalize them
RESERVES_MAP = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
    "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH",
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "WBTC",
}

async def fetch_block_data(multicall, block_number, assetsData, oracleAddress, db_pool):
    try:
        block = await w3.eth.get_block(block_number)
        block_ts = datetime.fromtimestamp(block.timestamp, tz=timezone.utc)
        
        # Build Multicall Requests
        calls = []
        
        # 1. Setup Contracts
        oracle = w3.eth.contract(address=w3.to_checksum_address(oracleAddress), abi=ORACLE_ABI)
        pool_addr = assetsData[list(assetsData.keys())[0]]["pool_addr"]
        pool = w3.eth.contract(address=w3.to_checksum_address(pool_addr), abi=POOL_ABI)
        erc20 = w3.eth.contract(abi=ERC20_ABI)
        
        # 2. For each asset, fetch 1) price 2) aToken supply 3) vToken supply 4) Pool ReserveData
        for asset, data in assetsData.items():
            # Oracle Price
            calls.append({"target": oracle.address, "callData": oracle.encode_abi("getAssetPrice", args=[w3.to_checksum_address(asset)])})
            # AToken total supply
            calls.append({"target": w3.to_checksum_address(data["aToken"]), "callData": erc20.encode_abi("totalSupply")})
            # DebtToken total supply
            calls.append({"target": w3.to_checksum_address(data["vToken"]), "callData": erc20.encode_abi("totalSupply")})
            # Pool getReserveData
            calls.append({"target": w3.to_checksum_address(pool_addr), "callData": pool.encode_abi("getReserveData", args=[w3.to_checksum_address(asset)])})
            
        # Execute perfectly via Multicall3 tryAggregate (requireSuccess=False)
        results = await multicall.functions.tryAggregate(False, calls).call(block_identifier=block_number)
        
        idx = 0
        insert_records = []
        
        for i, a_addr in enumerate(assetsData.keys()):
            data = assetsData[a_addr]
            
            # Decode responses safely
            succ_o, b_o = results[idx]
            succ_a, b_a = results[idx+1]
            succ_v, b_v = results[idx+2]
            succ_p, b_p = results[idx+3]
            idx += 4
            
            # Skip asset if contracts didn't exist yet at this block
            if not (succ_o and succ_a and succ_v and succ_p):
                continue
                
            a_supply = w3.codec.decode(["uint256"], b_a)[0]
            v_supply = w3.codec.decode(["uint256"], b_v)[0]
            
            # Pool data decode
            pool_data = w3.codec.decode(["(uint256,uint128,uint128,uint128,uint128,uint128,uint40,uint16,address,address,address,address,uint128,uint128,uint128)"], b_p)[0]
            
            # Extract variables 
            supply_rate = pool_data[2] / RAY
            borrow_rate = pool_data[4] / RAY 
            
            # Balances
            supplied = a_supply / (10**data["decimals"])
            borrowed = v_supply / (10**data["decimals"])
            
            # Oracle Price 
            raw_c_price = w3.codec.decode(["uint256"], b_o)[0]
            asset_price = raw_c_price / 1e8 
            
            # Normalized
            supplied_usd = supplied * asset_price
            borrowed_usd = borrowed * asset_price
            utilization_rate = borrowed_usd / supplied_usd if supplied_usd > 0 else 0.0
            
            insert_records.append((
                block_ts, block_number, data["symbol"], a_addr.lower(),
                float(supplied_usd), float(borrowed_usd),
                float(supply_rate), float(borrow_rate), float(utilization_rate),
                float(asset_price)
            ))
            
        if not insert_records:
            print(f"⚠ Block {block_number}: No valid assets deployed yet.")
            return

        async with db_pool.acquire() as conn:
            await conn.executemany('''
                INSERT INTO aave_hourly_state 
                (timestamp, block_number, symbol, reserve_address, supplied_usd, borrowed_usd, supply_rate, borrow_rate, utilization_rate, price_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (timestamp, reserve_address) DO NOTHING
            ''', insert_records)
        
        print(f"✓ Block {block_number:,} | {len(insert_records)} assets stored.")
        
    except Exception as e:
        print(f"✗ Failed block {block_number}: {e}")

async def build_asset_registry(pool_address):
    print("Building Asset Registry...")
    pool = w3.eth.contract(address=w3.to_checksum_address(pool_address), abi=POOL_ABI)
    erc20 = w3.eth.contract(abi=ERC20_ABI)
    
    asset_list = await pool.functions.getReservesList().call()
    print(f"Discovered {len(asset_list)} assets. Fetching definitions parallel...")
    
    assets_data = {}
    
    async def fetch_asset(asset):
        a = asset.lower()
        res_data = await pool.functions.getReserveData(asset).call()
        
        # Fetch decimals and symbol
        asset_contract = w3.eth.contract(address=w3.to_checksum_address(asset), abi=ERC20_ABI)
        decimals = await asset_contract.functions.decimals().call()
        
        if a in RESERVES_MAP:
            symbol = RESERVES_MAP[a]
        else:
            try:
                symbol = await asset_contract.functions.symbol().call()
            except:
                symbol = a[:8]
                
        return asset, {
            "aToken": res_data[8],
            "vToken": res_data[10],
            "decimals": decimals,
            "symbol": symbol,
            "pool_addr": pool_address
        }
    
    tasks = [fetch_asset(asset) for asset in asset_list]
    results = await asyncio.gather(*tasks)
    
    for asset, data in results:
        assets_data[asset] = data
        
    return assets_data

async def main():
    print("=" * 60)
    print("RLD Data Pipeline — Multicall3 Stable ABI Extraction")
    print("=" * 60)

    try:
        latest_block = await w3.eth.block_number
        print(f"Latest Block from RPC: {latest_block:,}")
    except Exception as e:
        print(f"⚠ ERROR: Could not connect to RPC: {e}")
        return

    if START_BLOCK > latest_block:
        print(f"⚠ ERROR: RPC latest block ({latest_block:,}) is behind START_BLOCK ({START_BLOCK:,}).")
        return

    # Initialize Core Setup
    addr_provider = w3.eth.contract(address=w3.to_checksum_address(ADDRESS_PROVIDER), abi=PROVIDER_ABI)
    pool_address = await addr_provider.functions.getPool().call()
    oracle_address = await addr_provider.functions.getPriceOracle().call()
    
    # Pre-build asset dict
    assetsData = await build_asset_registry(pool_address)
    multicall = w3.eth.contract(address=w3.to_checksum_address(MULTICALL3_ADDR), abi=MULTICALL3_ABI)

    # Postgres Pool
    db_pool = await asyncpg.create_pool(**db_config, min_size=5, max_size=20)

    # Block range
    current_target = START_BLOCK
    targets = []
    
    # To be extremely efficient and avoid trying to insert massive blocks for already synced dbs,
    # let's grab the actual max block locally.
    async with db_pool.acquire() as conn:
        latest_db_block = await conn.fetchval("SELECT MAX(block_number) FROM aave_hourly_state")
    if latest_db_block and latest_db_block >= START_BLOCK:
         current_target = latest_db_block + BLOCKS_PER_HOUR
         print(f"Resuming sync from Block {current_target:,}")
         
    while current_target <= latest_block:
        targets.append(current_target)
        current_target += BLOCKS_PER_HOUR

    print(f"Found {len(targets)} hourly block snapshots to query.")

    BATCH_SIZE = 5
    for i in range(0, len(targets), BATCH_SIZE):
        batch = targets[i:i+BATCH_SIZE]
        tasks = [fetch_block_data(multicall, blk, assetsData, oracle_address, db_pool) for blk in batch]
        await asyncio.gather(*tasks)
        
    # Refresh materialized view (OLTP layer)
    async with db_pool.acquire() as r_conn:
        await r_conn.execute("SELECT refresh_latest_pool_state();")
        
    print("\n✓ Pipeline complete. View refreshed.")
    await db_pool.close()

if __name__ == "__main__":
    asyncio.run(main())
