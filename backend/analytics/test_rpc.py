import asyncio
import os

import pytest
from dotenv import load_dotenv

load_dotenv()
MAINNET_RPC_URL = os.getenv("MAINNET_RPC_URL")


@pytest.mark.skipif(not MAINNET_RPC_URL, reason="requires MAINNET_RPC_URL")
def test_mainnet_rpc_block_number():
    web3 = pytest.importorskip("web3")
    async_http_provider = getattr(web3, "AsyncHTTPProvider")
    async_web3 = getattr(web3, "AsyncWeb3")

    async def run():
        client = async_web3(async_http_provider(MAINNET_RPC_URL))
        block_number = await client.eth.block_number
        assert isinstance(block_number, int)
        assert block_number > 0

    asyncio.run(run())
