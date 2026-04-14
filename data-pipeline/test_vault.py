import hypersync
import asyncio

async def test():
    client = hypersync.HypersyncClient(hypersync.ClientConfig())
    # Vault creation event signature: CreateMetaMorpho(address,address,address,uint256,address,string,string,bytes32)
    # Actually let's query the factory address
    FACTORY = "0xA9c3D3a366466Fa809d1Ae982Fb2c46E5fC41101"
    query = hypersync.Query(
        from_block=18883124,
        to_block=18885124, # small range to find a log
        logs=[hypersync.LogSelection(address=[FACTORY])]
    )
    res = await client.get(query)
    for log in res.data.logs:
        print("Vault created:", log.topics[0])

asyncio.run(test())
