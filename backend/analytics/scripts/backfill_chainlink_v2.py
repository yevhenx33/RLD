"""
Backfill AnswerUpdated events from configured Chainlink feeds.
Resumable: reads max(block_number) from chainlink_prices to continue.

Usage: python scripts/backfill_chainlink_v2.py
"""
import asyncio, datetime, logging, os, pickle, time, sys
import clickhouse_connect, hypersync, pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger()

ANSWER_UPDATED = "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f"
CHUNK = 50_000
CHAINLINK_START_BLOCK = 18_900_000


def load_metadata():
    with open("/tmp/aggregators.pkl", "rb") as f:
        agg_data = pickle.load(f)
    with open("/tmp/feed_info.pkl", "rb") as f:
        feed_info = pickle.load(f)

    all_addrs = agg_data["all_agg_addrs"]
    agg_to_proxy = agg_data["agg_to_proxy"]

    meta = {}  # addr -> (feed_name, decimals)
    for addr in all_addrs:
        proxy, desc, dec = agg_to_proxy.get(addr, (addr, "?", 8))
        info = feed_info.get(proxy, {})
        name = info.get("description", desc) or f"unknown_{proxy[:10]}"
        decimals = info.get("decimals", dec)
        meta[addr] = (name, decimals)

    return all_addrs, meta


async def backfill(addresses, meta, from_block, ch):
    token = os.getenv("ENVIO_API_TOKEN", "").strip()
    if not token:
        raise RuntimeError("ENVIO_API_TOKEN is required")
    client = hypersync.HypersyncClient(
        hypersync.ClientConfig(url="https://eth.hypersync.xyz", bearer_token=token)
    )
    head = await client.get_height()
    log.info(f"Range: {from_block:,} → {head:,} ({len(addresses)} addresses)")

    total = 0
    cursor = from_block
    t0 = time.time()

    while cursor <= head:
        end = min(cursor + CHUNK - 1, head)
        try:
            res = await client.get(hypersync.Query(
                from_block=cursor, to_block=end,
                logs=[hypersync.LogSelection(
                    address=addresses, topics=[[ANSWER_UPDATED]]
                )],
                field_selection=hypersync.FieldSelection(
                    log=[hypersync.LogField.ADDRESS, hypersync.LogField.BLOCK_NUMBER,
                         hypersync.LogField.TOPIC1, hypersync.LogField.DATA],
                    block=[hypersync.BlockField.NUMBER, hypersync.BlockField.TIMESTAMP],
                ),
            ))
        except Exception as e:
            log.warning(f"HyperSync error at {cursor}: {e}, retrying in 5s")
            await asyncio.sleep(5)
            continue

        bts = {}
        for b in res.data.blocks:
            if b.number is not None and b.timestamp is not None:
                v = int(b.timestamp, 16) if isinstance(b.timestamp, str) else b.timestamp
                bts[b.number] = datetime.datetime.fromtimestamp(v, tz=datetime.UTC)

        rows = []
        for ev in res.data.logs:
            a = ev.address.lower()
            m = meta.get(a)
            if not m:
                continue
            name, dec = m
            raw = int(ev.topics[1], 16)
            if raw > (1 << 255):
                raw -= 1 << 256
            price = raw / (10 ** dec)
            if price <= 0:
                continue
            d = ev.data or "0x"
            if d != "0x" and len(d) > 2:
                ts = datetime.datetime.fromtimestamp(int(d, 16), tz=datetime.UTC)
            else:
                ts = bts.get(ev.block_number)
                if ts is None:
                    continue
            rows.append({"feed": name, "price": price,
                         "block_number": ev.block_number, "timestamp": ts})

        if rows:
            ch.insert_df("chainlink_prices", pd.DataFrame(rows))
            total += len(rows)

        pct = (cursor - from_block) / max(head - from_block, 1) * 100
        elapsed = time.time() - t0
        log.info(f"  {end:,} ({pct:.0f}%)  +{len(rows)}  total={total:,}  {elapsed:.0f}s")
        cursor = end + 1

    log.info(f"✅ Done: {total:,} rows in {time.time()-t0:.0f}s")
    return total


def main():
    ch = clickhouse_connect.get_client(host="localhost", port=8123)
    addresses, meta = load_metadata()

    # Resume from last block
    r = ch.query("SELECT max(block_number) FROM chainlink_prices")
    last = r.result_rows[0][0] if r.result_rows and r.result_rows[0][0] else 0
    from_block = max(last + 1, CHAINLINK_START_BLOCK)
    log.info(f"Resuming from block {from_block:,}")

    asyncio.run(backfill(addresses, meta, from_block, ch))

    # Final summary
    stats = ch.query_df(
        "SELECT feed, count() AS n, round(argMax(price,timestamp),4) AS latest "
        "FROM chainlink_prices GROUP BY feed ORDER BY n DESC"
    )
    log.info(f"\n{len(stats)} feeds loaded:")
    for _, r in stats.iterrows():
        log.info(f"  {r['feed']:<40} {r['n']:>6} rows  latest={r['latest']}")
    ch.close()


if __name__ == "__main__":
    main()
