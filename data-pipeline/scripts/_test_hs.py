import hypersync, asyncio, time, sys
import os

async def main():
    token = os.getenv("ENVIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ENVIO_API_TOKEN is required")
    sys.stdout.write("Creating client...\n")
    sys.stdout.flush()
    client = hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=token,
    ))
    head = await client.get_height()
    sys.stdout.write("Head: %d\n" % head)
    sys.stdout.flush()
    
    sys.stdout.write("Querying blocks 16291127 -> 17000000...\n")
    sys.stdout.flush()
    query = hypersync.Query(
        from_block=16291127,
        to_block=17000000,
        logs=[hypersync.LogSelection(
            address=["0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"],
            topics=[["0x804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a"]],
        )],
        field_selection=hypersync.FieldSelection(
            log=[hypersync.LogField.BLOCK_NUMBER, hypersync.LogField.TOPIC1, hypersync.LogField.DATA],
            block=[hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP],
        ),
    )
    t0 = time.time()
    res = await client.get(query)
    sys.stdout.write("Got %d events, %d blocks in %.1fs\n" % (
        len(res.data.logs), len(res.data.blocks), time.time()-t0))
    sys.stdout.flush()

asyncio.run(main())
sys.stdout.write("DONE\n")
sys.stdout.flush()
