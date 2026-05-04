"""Download Aave events in explicit 1M-block batches (not relying on next_block)."""
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
    print("Head: %d" % head, flush=True)
    
    all_events = []
    block_ts = {}
    BATCH = 1_000_000
    from_block = 16_291_127
    
    while from_block < head:
        to_block = min(from_block + BATCH, head)
        query = hypersync.Query(
            from_block=from_block,
            to_block=to_block,
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
        
        for b in res.data.blocks:
            if b.number is not None and b.timestamp is not None:
                ts = b.timestamp
                if isinstance(ts, str):
                    ts = int(ts, 16) if ts.startswith("0x") else int(ts)
                block_ts[b.number] = ts
        
        all_events.extend(res.data.logs)
        print("  %10d -> %10d: +%6d events (total=%8d, %.1fs)" % (
            from_block, to_block, len(res.data.logs), len(all_events), time.time()-t0), flush=True)
        from_block = to_block + 1
    
    print("\nTOTAL: %d events, %d blocks with timestamps" % (len(all_events), len(block_ts)), flush=True)
    
    # Quick decode sample
    RAY = 10**27
    from collections import Counter
    symbols = Counter()
    for e in all_events:
        topics = e.topics or []
        if len(topics) >= 2:
            addr = "0x" + topics[1][26:].lower()
            symbols[addr] += 1
    
    print("\nTop reserves by event count:")
    for addr, count in symbols.most_common(10):
        print("  %s: %d events" % (addr[:12] + "...", count), flush=True)

asyncio.run(main())
