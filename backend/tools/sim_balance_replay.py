#!/usr/bin/env python3
"""
Pool Balance Replay Simulation
===============================
Verifies the pool balance tracking mechanism by:
1. Reading the on-chain PoolManager token balances at the FIRST ModifyLiquidity block (seed)
2. Replaying ALL swap event deltas from the DB
3. Asserting the final computed balance matches the CURRENT on-chain balance

If they match → the incremental tracking is correct, just needs correct seeding.
If they don't → the delta logic (sign/magnitude) is wrong.
"""
import subprocess
import json
import asyncio
import asyncpg

# ── Config ──
DB_DSN = "postgresql://rld:rld_dev_password@localhost:5432/rld_indexer"
RPC_URL = "http://localhost:8545"
PM_ADDR = "0x000000000004444c5dc75cB358380D2e3dE08A90"
WAUSDC = "0x67F7C08f0c6E93fcFc42C0E8E681Bd8c8E496124"
WRLP = "0x291dAC24cF806F3aB484feaDF167b9b7148dE921"

# Token ordering: token0 = min(wrlp, wausdc) = wRLP, token1 = waUSDC
assert WRLP.lower() < WAUSDC.lower(), "Expected wRLP < waUSDC by address"
TOKEN0_ADDR = WRLP   # wRLP
TOKEN1_ADDR = WAUSDC  # waUSDC
TOKEN0_NAME = "wRLP"
TOKEN1_NAME = "waUSDC"


def cast_call(contract: str, sig: str, *args, block: str = "latest") -> str:
    """Call a contract view function via cast."""
    cmd = ["cast", "call", contract, sig] + list(args) + [
        "--rpc-url", RPC_URL, "--block", str(block)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def get_balance(token: str, account: str, block: str = "latest") -> int:
    """Get ERC20 balanceOf via cast."""
    raw = cast_call(token, "balanceOf(address)", account, block=block)
    return int(raw, 16)


def decode_int128(hex_word: str) -> int:
    """Decode a 32-byte hex word as int128 (signed)."""
    val = int(hex_word, 16)
    if val >= (1 << 127):
        val -= (1 << 256)
    return val


async def main():
    conn = await asyncpg.connect(DB_DSN)

    # ── Step 1: Find the first ModifyLiquidity block ──
    first_lp_block = await conn.fetchval("""
        SELECT block_number FROM events
        WHERE event_name = 'ModifyLiquidity'
        ORDER BY block_number LIMIT 1
    """)
    print(f"First ModifyLiquidity at block: {first_lp_block}")

    # ── Step 2: Read on-chain balances RIGHT AFTER the LP creation block ──
    seed_t0 = get_balance(TOKEN0_ADDR, PM_ADDR, block=str(first_lp_block))
    seed_t1 = get_balance(TOKEN1_ADDR, PM_ADDR, block=str(first_lp_block))
    print(f"\nInitial seed (block {first_lp_block}):")
    print(f"  {TOKEN0_NAME}: {seed_t0:>20,} raw  ({seed_t0/1e6:>14,.2f} human)")
    print(f"  {TOKEN1_NAME}: {seed_t1:>20,} raw  ({seed_t1/1e6:>14,.2f} human)")

    # ── Step 3: Get ALL swap events in chronological order ──
    swaps = await conn.fetch("""
        SELECT block_number, data
        FROM events
        WHERE event_name = 'Swap'
        ORDER BY block_number, id
    """)
    print(f"\nTotal swap events to replay: {len(swaps)}")

    # ── Step 4: Replay swaps ──
    t0 = seed_t0
    t1 = seed_t1
    for i, swap in enumerate(swaps):
        data = swap["data"]
        if isinstance(data, str):
            data = json.loads(data)
        raw = data["raw"]
        # Strip 0x prefix, decode ABI: int128 amount0, int128 amount1, ...
        hex_data = raw[2:] if raw.startswith("0x") else raw
        # Each ABI word is 32 bytes = 64 hex chars
        word0 = hex_data[0:64]    # int128 amount0
        word1 = hex_data[64:128]  # int128 amount1

        amount0 = decode_int128(word0)
        amount1 = decode_int128(word1)

        # Pool handler logic: new = prev - amount (V4 amounts are swapper-centric)
        t0 = t0 - amount0
        t1 = t1 - amount1

        if i < 3 or i >= len(swaps) - 3:
            print(f"  Swap #{i+1:3d} block={swap['block_number']}: "
                  f"a0={amount0:>18,} a1={amount1:>18,}  →  "
                  f"t0={t0:>20,} t1={t1:>20,}")
        elif i == 3:
            print(f"  ... ({len(swaps) - 6} swaps omitted) ...")

    # ── Step 5: Get current on-chain balances ──
    latest_block = await conn.fetchval(
        "SELECT MAX(block_number) FROM block_states"
    )
    actual_t0 = get_balance(TOKEN0_ADDR, PM_ADDR, block="latest")
    actual_t1 = get_balance(TOKEN1_ADDR, PM_ADDR, block="latest")

    print(f"\n{'='*60}")
    print(f"RESULTS (latest block: {latest_block})")
    print(f"{'='*60}")
    print(f"  Computed {TOKEN0_NAME}: {t0:>20,} ({t0/1e6:>14,.2f} human)")
    print(f"  Actual   {TOKEN0_NAME}: {actual_t0:>20,} ({actual_t0/1e6:>14,.2f} human)")
    print(f"  Delta    {TOKEN0_NAME}: {t0 - actual_t0:>20,} ({(t0-actual_t0)/1e6:>14,.2f} human)")
    print()
    print(f"  Computed {TOKEN1_NAME}: {t1:>20,} ({t1/1e6:>14,.2f} human)")
    print(f"  Actual   {TOKEN1_NAME}: {actual_t1:>20,} ({actual_t1/1e6:>14,.2f} human)")
    print(f"  Delta    {TOKEN1_NAME}: {t1 - actual_t1:>20,} ({(t1-actual_t1)/1e6:>14,.2f} human)")
    print()

    # ── Step 6: Verdict ──
    if t0 == actual_t0 and t1 == actual_t1:
        print("✅ EXACT MATCH — incremental tracking logic is correct.")
        print("   Root cause: incorrect initial seed, NOT the delta tracking.")
    elif abs(t0 - actual_t0) < 1000 and abs(t1 - actual_t1) < 1000:
        print("⚠️  CLOSE MATCH (within rounding) — tracking is essentially correct.")
        print("   Tiny rounding errors from fee computation.")
    else:
        print("❌ MISMATCH — delta tracking logic has a bug.")
        print("   The sign or magnitude of swap amounts is wrong.")

    # ── Step 7: Show what block_states currently has ──
    bs_row = await conn.fetchrow("""
        SELECT token0_balance, token1_balance
        FROM block_states
        WHERE market_id = (SELECT market_id FROM markets LIMIT 1)
        ORDER BY block_number DESC LIMIT 1
    """)
    if bs_row:
        bs_t0 = int(bs_row["token0_balance"] or 0)
        bs_t1 = int(bs_row["token1_balance"] or 0)
        print(f"\n  Current block_states {TOKEN0_NAME}: {bs_t0:>20,}")
        print(f"  Current block_states {TOKEN1_NAME}: {bs_t1:>20,}")
        print(f"  shows the seed was wrong by: t0={seed_t0 - (bs_t0 - (t0 - seed_t0)):,}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
