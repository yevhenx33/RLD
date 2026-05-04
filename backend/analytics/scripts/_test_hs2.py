import hypersync, asyncio, time, sys, os

async def main():
    token = os.getenv("ENVIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ENVIO_API_TOKEN is required")
    client = hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=token,
    ))
    head = await client.get_height()
    sys.stdout.write("Head: %d\n" % head)
    sys.stdout.flush()
    
    all_events = []
    from_block = 16291127
    
    while from_block < head:
        query = hypersync.Query(
            from_block=from_block,
            to_block=head,
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
        nb = res.next_block
        all_events.extend(res.data.logs)
        sys.stdout.write("  %d -> %d: +%d events (total=%d, %.1fs)\n" % (
            from_block, nb, len(res.data.logs), len(all_events), time.time()-t0))
        sys.stdout.flush()
        if nb <= from_block:
            break
        from_block = nb
    
    sys.stdout.write("TOTAL: %d events\n" % len(all_events))
    sys.stdout.flush()

asyncio.run(main())
