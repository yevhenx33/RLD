import asyncio
import os

import hypersync
import pytest

HYPERSYNC_URL = os.getenv("HYPERSYNC_URL")
FACTORY_ADDRESS = "0xA9c3D3a366466Fa809d1Ae982Fb2c46E5fC41101"


@pytest.mark.skipif(not HYPERSYNC_URL, reason="requires HYPERSYNC_URL")
def test_vault_factory_logs_query():
    async def run():
        client = hypersync.HypersyncClient(
            hypersync.ClientConfig(url=HYPERSYNC_URL)
        )
        query = hypersync.Query(
            from_block=18883124,
            to_block=18885124,
            logs=[hypersync.LogSelection(address=[FACTORY_ADDRESS])],
        )
        response = await client.get(query)
        assert response.data is not None
        assert response.data.logs is not None

    asyncio.run(run())
