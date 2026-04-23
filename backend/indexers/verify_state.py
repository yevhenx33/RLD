#!/usr/bin/env python3
"""
verify_state.py — Side-by-side comparison of on-chain state vs indexer DB.

Checks:
  1. Market config (addresses, pool_id)
  2. Broker list (from BrokerCreated events vs DB)
  3. Pool state (Slot0: sqrtPriceX96, tick, liquidity) from V4 StateView vs block_states
  4. Normalization factor from RLDCore vs block_states
  5. Index price from MockOracle vs block_states
  6. Events total in DB

Usage:
  python3 verify_state.py
  # or with explicit overrides:
  RPC_URL=http://localhost:8545 DATABASE_URL=postgresql://... python3 verify_state.py
"""

import asyncio
import json
import os
import pathlib
from typing import Any

import asyncpg
from eth_abi import decode as abi_decode
from web3 import Web3

# ── Config ─────────────────────────────────────────────────────────────────

RPC_URL = os.getenv("RPC_URL", "http://localhost:8545")
DB_URL = os.getenv("DATABASE_URL", "postgresql://rld:rld_dev_password@localhost:5432/rld_indexer")
DEPLOYMENT_JSON = os.getenv("DEPLOYMENT_JSON", str(
    pathlib.Path(__file__).parents[2] / "docker" / "deployment.json"
))

w3 = Web3(Web3.HTTPProvider(RPC_URL))

# ── ABI call helpers ─────────────────────────────────────────────────────

def call(to: str, sig: str, args: list, types: list[str]) -> Any:
    """Generic eth_call helper with ABI encoding."""
    selector = Web3.keccak(text=sig)[:4]
    if args:
        from eth_abi import encode
        call_data = selector + encode([t for t in types[:len(args)]], args)
    else:
        call_data = selector
    result = w3.eth.call({"to": Web3.to_checksum_address(to), "data": call_data.hex()})
    if not result:
        return None
    return_types = [t for t in types[len(args):]] if args else types
    try:
        decoded = abi_decode(return_types, bytes(result))
        return decoded[0] if len(decoded) == 1 else decoded
    except Exception:
        return result.hex()


def call_raw(to: str, calldata: str) -> bytes:
    return w3.eth.call({"to": Web3.to_checksum_address(to), "data": calldata})


# ── Formatting ─────────────────────────────────────────────────────────────

OK   = "  ✓"
FAIL = "  ✗"
WARN = "  ⚠"
SKIP = "  -"


def match(onchain, indexed, label: str, transform=str, tol=None) -> str:
    a = transform(onchain) if onchain is not None else "N/A"
    b = transform(indexed) if indexed is not None else "NULL"
    if onchain is None or indexed is None:
        verdict = WARN
    elif tol is not None:
        verdict = OK if abs(float(onchain) - float(indexed)) <= tol else FAIL
    else:
        verdict = OK if a.lower() == b.lower() else FAIL
    return f"{verdict}  {label:<35} on-chain={a}  indexed={b}"


# ── Main comparison ─────────────────────────────────────────────────────────

async def run():
    # Load deployment.json
    with open(DEPLOYMENT_JSON) as f:
        cfg = json.load(f)

    market_id = cfg["market_id"]
    rld_core = cfg["rld_core"]
    state_view = cfg.get("v4_state_view", "")
    mock_oracle = cfg["mock_oracle"]
    broker_factory = cfg["broker_factory"]
    pool_id = cfg["pool_id"]
    pool_id_bytes = bytes.fromhex(pool_id[2:] if pool_id.startswith("0x") else pool_id)

    conn = await asyncpg.connect(DB_URL)
    current_block = w3.eth.block_number
    latest_block = await conn.fetchrow(
        "SELECT block_number, block_timestamp FROM block_states WHERE market_id=$1 ORDER BY block_number DESC LIMIT 1",
        market_id
    )

    print(f"\n{'═'*80}")
    print("  RLD Protocol State Comparison")
    print(f"  on-chain block: {current_block}")
    print(f"  last indexed:   {latest_block['block_number'] if latest_block else 'none'}")
    print(f"  market_id:      {market_id}")
    print(f"{'═'*80}\n")


    # ── 1. Market config from DB ───────────────────────────────────────────
    print("[ 1. Market config ]")
    db_market = await conn.fetchrow("SELECT * FROM markets WHERE market_id=$1", market_id)
    if not db_market:
        print(f"{FAIL}  Market not found in DB")
        await conn.close()
        return

    for field, cfg_key in [
        ("broker_factory", "broker_factory"),
        ("mock_oracle", "mock_oracle"),
        ("twamm_hook", "twamm_hook"),
        ("pool_id", "pool_id"),
        ("wausdc", "wausdc"),
    ]:
        cfg_val = cfg.get(cfg_key, "")
        db_val = db_market[field] if db_market else None
        v = OK if cfg_val.lower() == (db_val or "").lower() else FAIL
        print(f"{v}  {field:<35} deployment.json={cfg_val[:20]}...  db={str(db_val)[:20]}...")

    # ── 2. Brokers ─────────────────────────────────────────────────────────
    print("\n[ 2. Brokers ]")
    # On-chain: scan BrokerCreated events
    topic0 = Web3.keccak(text="BrokerCreated(address,address,bytes32)").hex()
    logs = w3.eth.get_logs({
        "fromBlock": "earliest",
        "toBlock": "latest",
        "address": Web3.to_checksum_address(broker_factory),
        "topics": [topic0],
    })
    onchain_brokers = set()
    for log in logs:
        broker_addr = "0x" + log["topics"][1].hex()[-40:]
        onchain_brokers.add(broker_addr.lower())

    db_brokers = await conn.fetch("SELECT address, owner FROM brokers WHERE market_id=$1", market_id)
    db_broker_addrs = {r["address"].lower() for r in db_brokers}

    print(f"{OK}  BrokerCreated events on-chain: {len(onchain_brokers)}")
    print(f"{OK if len(db_brokers) == len(onchain_brokers) else FAIL}  Brokers in indexer DB:         {len(db_brokers)}")

    missing_from_db = onchain_brokers - db_broker_addrs
    extra_in_db = db_broker_addrs - onchain_brokers
    if missing_from_db:
        for addr in missing_from_db:
            print(f"{FAIL}  Missing from DB: {addr}")
    if extra_in_db:
        for addr in extra_in_db:
            print(f"{WARN}  Extra in DB (not on-chain): {addr}")

    # Print known brokers
    for r in sorted(db_brokers, key=lambda x: x["address"]):
        ok = OK if r["address"].lower() in onchain_brokers else FAIL
        print(f"{ok}  broker={r['address'][:20]}...  owner={r['owner'][:20]}...")

    # ── 3. Pool state (V4 StateView getSlot0) ──────────────────────────────
    print("\n[ 3. Pool state (V4 Slot0) ]")
    db_state = latest_block  # already fetched
    db_full = None
    if db_state:
        db_full = await conn.fetchrow(
            "SELECT * FROM block_states WHERE market_id=$1 AND block_number=$2",
            market_id, db_state["block_number"]
        )

    # StateView.getSlot0(PoolId) → (sqrtPriceX96, tick, protocolFee, lpFee)
    # PoolId is bytes32
    try:
        slot0_sig = "getSlot0(bytes32)"
        selector = Web3.keccak(text=slot0_sig)[:4]
        calldata = selector + pool_id_bytes
        raw = call_raw(state_view, calldata.hex())
        # Returns: (uint160 sqrtPriceX96, int24 tick, uint24 protocolFee, uint24 lpFee)
        (sqrt_price, tick, pfee, lfee) = abi_decode(
            ["uint160", "int24", "uint24", "uint24"], bytes(raw)
        )
        mark_price = (sqrt_price / 2**96) ** 2

        db_sqrt = int(db_full["sqrt_price_x96"]) if db_full and db_full and db_full["sqrt_price_x96"] else None
        db_tick = db_full["tick"] if db_full and db_full["tick"] is not None else None
        db_mark = float(db_full["mark_price"]) if db_full and db_full["mark_price"] else None

        print(f"{OK}  sqrtPriceX96 (on-chain): {sqrt_price}")
        print(f"{OK if db_sqrt == sqrt_price else (WARN if db_sqrt is None else FAIL)}  sqrtPriceX96 (indexed):  {db_sqrt} (no Swap event yet if NULL)")
        print(f"{OK}  tick (on-chain): {tick}")
        print(f"{OK if db_tick == tick else (WARN if db_tick is None else FAIL)}  tick (indexed):  {db_tick}")
        print(f"{OK}  mark_price (on-chain):  {mark_price:.8f}")
        print(f"{OK if db_mark and abs(db_mark - mark_price) < 0.001 else (WARN if db_mark is None else FAIL)}  mark_price (indexed):   {f'{db_mark:.8f}' if db_mark else 'NULL (no Swap event yet)'}")
    except Exception as e:
        print(f"{WARN}  getSlot0 failed: {e}")

    # ── 4. Liquidity ──────────────────────────────────────────────────────
    print("\n[ 4. Liquidity ]")
    try:
        liq_sig = "getLiquidity(bytes32)"
        selector = Web3.keccak(text=liq_sig)[:4]
        calldata = selector + pool_id_bytes
        raw = call_raw(state_view, calldata.hex())
        (liquidity,) = abi_decode(["uint128"], bytes(raw))
        db_liq = int(db_full["liquidity"]) if db_full and db_full["liquidity"] else None
        print(f"{OK}  liquidity (on-chain): {liquidity}")
        print(f"{OK if db_liq == liquidity else FAIL}  liquidity (indexed):  {db_liq}")
    except Exception as e:
        print(f"{WARN}  getLiquidity failed: {e}")

    # ── 5. Normalization factor ────────────────────────────────────────────
    print("\n[ 5. Normalization factor (RLDCore) ]")
    try:
        from eth_abi import encode as abi_encode
        mid_bytes = bytes.fromhex(market_id[2:] if market_id.startswith("0x") else market_id)
        # getMarketState returns (uint128 normFactor, uint128 ?, uint128 timestamp, uint128 ?)
        ms_selector = Web3.keccak(text="getMarketState(bytes32)")[:4]
        calldata = ms_selector + abi_encode(["bytes32"], [mid_bytes])
        raw = call_raw(rld_core, calldata.hex())
        if raw:
            vals = abi_decode(["uint128", "uint128", "uint128", "uint128"], bytes(raw))
            norm_factor = vals[0]  # first field is normalizationFactor (1e18 = 1.0)
            db_nf = int(db_full["normalization_factor"]) if db_full and db_full["normalization_factor"] else None
            print(f"{OK}  normFactor (on-chain): {norm_factor} ({norm_factor/1e18:.6f})")
            print(f"{OK if db_nf == norm_factor else (WARN if db_nf is None else FAIL)}  normFactor (indexed):  {db_nf} (no block_states yet if NULL)")
        else:
            print(f"{WARN}  getMarketState returned empty")
    except Exception as e:
        print(f"{WARN}  normFactor call failed: {e}")

    # ── 6. Index price (MockOracle) ────────────────────────────────────────
    print("\n[ 6. Index price (MockOracle) ]")
    try:
        # MockOracle emits RateUpdated(uint256 newRateRay, uint256 timestamp)
        # topic0 = keccak256("RateUpdated(uint256,uint256)")
        topic0_rate = Web3.keccak(text="RateUpdated(uint256,uint256)").hex()
        logs_rate = w3.eth.get_logs({
            "fromBlock": "earliest",
            "toBlock": "latest",
            "address": Web3.to_checksum_address(mock_oracle),
            "topics": [topic0_rate],
        })
        if logs_rate:
            last_rate_log = logs_rate[-1]
            raw_data = last_rate_log["data"]
            if isinstance(raw_data, bytes):
                raw_data = raw_data.hex()
            if raw_data.startswith("0x"):
                raw_data = raw_data[2:]
            (rate_ray, ts) = abi_decode(["uint256", "uint256"], bytes.fromhex(raw_data))
            index_price = rate_ray / 1e27  # RAY = 1e27
            print(f"{OK}  index_rate_ray (on-chain): {rate_ray} = {index_price*100:.4f}% APY")
            print(f"{OK}  last RateUpdated events:   {len(logs_rate)}")
        else:
            # Fall back: scan all oracle events with the deployed topic
            all_logs = w3.eth.get_logs({"fromBlock": "earliest","toBlock": "latest","address": Web3.to_checksum_address(mock_oracle)})
            print(f"{WARN}  No RateUpdated events found. Total oracle events: {len(all_logs)}")
            if all_logs:
                last_log = all_logs[-1]
                raw_data = last_log["data"]
                if isinstance(raw_data, bytes):
                    raw_data = raw_data.hex()
                if raw_data.startswith("0x"):
                    raw_data = raw_data[2:]
                (rate_ray, ts) = abi_decode(["uint256", "uint256"], bytes.fromhex(raw_data))
                index_price = rate_ray / 1e27
                print(f"{OK}  last oracle event rate_ray: {rate_ray} = {index_price*100:.4f}% APY (topic={last_log['topics'][0].hex()[:16]}...)")
        db_ip = float(db_full["index_price"]) if db_full and db_full["index_price"] else None
        print(f"{WARN if db_ip is None else OK}  index_price (indexed): {db_ip} (NULL = no block_state event yet)")
    except Exception as e:
        print(f"{WARN}  oracle read failed: {e}")

    # ── 7. Events summary ─────────────────────────────────────────────────
    print("\n[ 7. Indexer progress ]")
    state_row = await conn.fetchrow(
        "SELECT last_indexed_block, total_events FROM indexer_state WHERE market_id=$1", market_id
    )
    events_by_type = await conn.fetch(
        "SELECT event_name, COUNT(*) as cnt FROM events WHERE market_id=$1 GROUP BY event_name ORDER BY cnt DESC",
        market_id
    )
    print(f"{OK}  current_block:      {current_block}")
    print(f"{OK}  last_indexed_block: {state_row['last_indexed_block'] if state_row else 'N/A'}")
    lag = current_block - (state_row["last_indexed_block"] if state_row else current_block)
    print(f"{OK if lag < 5 else WARN}  block_lag:          {lag}")
    print(f"{OK}  total_events (DB):  {state_row['total_events'] if state_row else 0}")
    if events_by_type:
        print("\n  Events by type:")
        for row in events_by_type:
            print(f"         {row['event_name']:<35} {row['cnt']}")

    print(f"\n{'═'*80}")
    print("  Done.")
    print(f"{'═'*80}\n")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
