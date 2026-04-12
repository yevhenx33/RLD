#!/usr/bin/env python3
"""Download all Fluid LogOperate events via HyperSync → Parquet."""
import asyncio, os, time, sys
import hypersync
import pyarrow as pa
import pyarrow.parquet as pq

OUTDIR = "/mnt/data/hypersync_staging/fluid"

def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)

async def main():
    token = os.environ.get("ENVIO_API_TOKEN")
    if not token:
        print("ERROR: ENVIO_API_TOKEN not set"); sys.exit(1)

    client = hypersync.HypersyncClient(
        hypersync.ClientConfig(
            url="https://eth.hypersync.xyz",
            bearer_token=token,
        )
    )

    os.makedirs(OUTDIR, exist_ok=True)

    START_BLOCK = 19_258_464
    log(f"Downloading Fluid LogOperate events from block {START_BLOCK:,}...")

    t0 = time.time()
    current_block = START_BLOCK
    total_logs = 0
    batch_num = 0
    all_rows = []

    while True:
        query = hypersync.Query(
            from_block=current_block,
            logs=[
                hypersync.LogSelection(
                    address=["0x52Aa899454998Be5b000Ad077a46Bbe360F4e497"],
                    topics=[["0x4d93b232a24e82b284ced7461bf4deacffe66759d5c24513e6f29e571ad78d15"]],
                )
            ],
            field_selection=hypersync.FieldSelection(
                log=[
                    hypersync.LogField.BLOCK_NUMBER,
                    hypersync.LogField.TRANSACTION_HASH,
                    hypersync.LogField.LOG_INDEX,
                    hypersync.LogField.ADDRESS,
                    hypersync.LogField.TOPIC0,
                    hypersync.LogField.TOPIC1,
                    hypersync.LogField.TOPIC2,
                    hypersync.LogField.TOPIC3,
                    hypersync.LogField.DATA,
                ],
                block=[
                    hypersync.BlockField.NUMBER,
                    hypersync.BlockField.TIMESTAMP,
                ],
            ),
        )

        res = await client.get(query)
        n_logs = len(res.data.logs)
        total_logs += n_logs
        batch_num += 1

        # Build block timestamp map
        block_ts = {}
        for b in res.data.blocks:
            if b.number is not None and b.timestamp is not None:
                # HyperSync may return timestamps as hex strings
                ts = b.timestamp
                if isinstance(ts, str) and ts.startswith('0x'):
                    ts = int(ts, 16)
                block_ts[b.number] = int(ts)

        for ev in res.data.logs:
            all_rows.append({
                "block_number": ev.block_number,
                "block_timestamp": block_ts.get(ev.block_number, 0),
                "tx_hash": ev.transaction_hash or "",
                "log_index": ev.log_index or 0,
                "address": ev.address or "",
                "topic0": ev.topics[0] if ev.topics and ev.topics[0] else "",
                "topic1": ev.topics[1] if ev.topics and len(ev.topics) > 1 and ev.topics[1] else "",
                "topic2": ev.topics[2] if ev.topics and len(ev.topics) > 2 and ev.topics[2] else "",
                "topic3": ev.topics[3] if ev.topics and len(ev.topics) > 3 and ev.topics[3] else "",
                "data": ev.data or "",
            })

        archive_height = res.archive_height or 0
        if n_logs > 0 and archive_height > START_BLOCK:
            pct = min(100.0, (res.next_block - START_BLOCK) / (archive_height - START_BLOCK) * 100)
            log(f"  batch {batch_num}: {n_logs:,} logs | block {current_block:,}→{res.next_block:,} | total: {total_logs:,} | {pct:.1f}%")
        elif batch_num <= 3:
            log(f"  batch {batch_num}: {n_logs} logs | next={res.next_block} archive={archive_height}")

        # Terminate when caught up to archive
        if res.next_block >= archive_height and archive_height > 0:
            log(f"  Reached archive head at block {archive_height:,}")
            break

        # Safety: no progress
        if res.next_block <= current_block:
            log(f"  WARN: no progress (next_block={res.next_block} <= current={current_block}), stopping")
            break

        current_block = res.next_block

    elapsed = time.time() - t0

    if not all_rows:
        log("No events downloaded!"); return

    # Save as Parquet
    log(f"Saving {total_logs:,} events to Parquet ({elapsed:.1f}s download)...")
    table = pa.table({
        "block_number": pa.array([r["block_number"] for r in all_rows], type=pa.uint64()),
        "block_timestamp": pa.array([r["block_timestamp"] for r in all_rows], type=pa.uint64()),
        "tx_hash": [r["tx_hash"] for r in all_rows],
        "log_index": pa.array([r["log_index"] for r in all_rows], type=pa.uint32()),
        "address": [r["address"] for r in all_rows],
        "topic0": [r["topic0"] for r in all_rows],
        "topic1": [r["topic1"] for r in all_rows],
        "topic2": [r["topic2"] for r in all_rows],
        "topic3": [r["topic3"] for r in all_rows],
        "data": [r["data"] for r in all_rows],
    })

    outpath = os.path.join(OUTDIR, "fluid_logoperate.parquet")
    pq.write_table(table, outpath)
    size_mb = os.path.getsize(outpath) / (1024 * 1024)
    log(f"DONE! {total_logs:,} events → {outpath} ({size_mb:.1f} MB)")

if __name__ == "__main__":
    asyncio.run(main())
