import asyncio, os
from web3 import AsyncWeb3, AsyncHTTPProvider
from dotenv import load_dotenv

load_dotenv()

async def run():
    w3=AsyncWeb3(AsyncHTTPProvider(os.getenv("MAINNET_RPC_URL")))
    print("BLOCK:", await w3.eth.block_number)

asyncio.run(run())
