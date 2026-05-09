#!/usr/bin/env python3
"""
Probe: Interest accounting in Fluid delta accumulation.

Core question: The operate() event emits supplyAmount/borrowAmount in NORMAL
token units. If we simply sum these deltas, we get NET FLOWS, but miss interest
accrual. The vault's actual balance = rawShares × exchangePrice / 1e12.

This probe:
1. Sums all supply/borrow deltas for a known vault
2. Compares against the vault's current RPC state
3. Computes the "missing" interest = currentBalance - Σ(deltas)
"""
import os, sys, requests
import clickhouse_connect
from eth_abi import decode as abi_decode
from eth_utils import keccak

RPC_URL = os.environ["MAINNET_RPC_URL"]
RESOLVER = "0x814c8c7ceb1411b364c2940c4b9380e739e06686"
PRECISION = 10**12
X64 = (1 << 64) - 1

VAULT_CONSTANTS_TYPE = "(address,address,address,address,address,address,address,address,(address,address),(address,address),uint256,uint256,bytes32,bytes32,bytes32,bytes32)"
VAULT_CONFIGS_TYPE = "(uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint16,address,uint256,uint256,address,uint256)"
VAULT_EXCHANGE_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,int256,int256,int256,int256)"
VAULT_TOTALS_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256)"
VAULT_LIMITS_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
VAULT_BRANCH_TYPE = "(uint256,int256,uint256,uint256,uint256,uint256,int256)"
VAULT_STATE_TYPE = f"(uint256,int256,uint256,uint256,uint256,uint256,{VAULT_BRANCH_TYPE})"
LIQ_USER_SUPPLY_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
LIQ_USER_BORROW_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
VAULT_ENTIRE_TYPE = f"(address,uint256,uint256,{VAULT_CONSTANTS_TYPE},{VAULT_CONFIGS_TYPE},{VAULT_EXCHANGE_TYPE},{VAULT_TOTALS_TYPE},{VAULT_LIMITS_TYPE},{VAULT_STATE_TYPE},{LIQ_USER_SUPPLY_TYPE},{LIQ_USER_BORROW_TYPE})"


def decode_int256(hex_str):
    val = int(hex_str, 16)
    return val - (1 << 256) if val >= (1 << 255) else val


def bignum_decode(field_64bit):
    return (field_64bit >> 8) << (field_64bit & 0xFF)


def main():
    ch = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "rld_clickhouse"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASS", "")),
    )

    # Test vault: 0xeabbfca72... (ETH/USDC, type 10000, simple collateral)
    # This vault deposits ETH as collateral and borrows USDC
    vault = "0xeabbfca72f8a8bf14c4ac59e69ecb2eb69f0811c"
    col_token = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"  # ETH
    debt_token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"  # USDC

    for token, label in [(col_token, "COLLATERAL (ETH)"), (debt_token, "DEBT (USDC)")]:
        print(f"\n{'='*80}")
        print(f"Vault: {vault}")
        print(f"Token: {label} ({token})")
        print(f"{'='*80}")

        # 1. Get all event data for this vault+token pair
        rows = ch.query(f"""
            SELECT data, block_number, block_timestamp,
                   substring(data, 259, 64) AS w4_totals,
                   substring(data, 323, 64) AS w5_exchange
            FROM fluid_events
            WHERE (event_name = 'Operate' OR event_name = 'LogOperate')
              AND lower(concat('0x', substring(topic1, 27))) = '{vault}'
              AND lower(concat('0x', substring(topic2, 27))) = '{token}'
            ORDER BY block_number ASC
        """).result_rows

        if not rows:
            print("  No events found")
            continue

        # 2. Accumulate deltas and track exchange prices
        sum_supply_delta = 0
        sum_borrow_delta = 0
        sum_supply_positive = 0  # deposits
        sum_supply_negative = 0  # withdrawals
        sum_borrow_positive = 0  # borrows
        sum_borrow_negative = 0  # repays
        first_ex_supply = None
        last_ex_supply = None
        first_ex_borrow = None
        last_ex_borrow = None

        # Also track raw share accumulation
        sum_raw_supply = 0
        sum_raw_borrow = 0

        for data_hex, block_num, block_ts, w4_hex, w5_hex in rows:
            raw = data_hex[2:]
            supply_delta = decode_int256(raw[0:64])
            borrow_delta = decode_int256(raw[64:128])

            sum_supply_delta += supply_delta
            sum_borrow_delta += borrow_delta

            if supply_delta > 0:
                sum_supply_positive += supply_delta
            elif supply_delta < 0:
                sum_supply_negative += supply_delta

            if borrow_delta > 0:
                sum_borrow_positive += borrow_delta
            elif borrow_delta < 0:
                sum_borrow_negative += borrow_delta

            # Extract exchange prices from this event
            if w5_hex and len(w5_hex) >= 64:
                ex_packed = int(w5_hex, 16)
                ex_supply = (ex_packed >> 91) & X64
                ex_borrow = (ex_packed >> 155) & X64
                if ex_supply > 0:
                    if first_ex_supply is None:
                        first_ex_supply = ex_supply
                    last_ex_supply = ex_supply
                if ex_borrow > 0:
                    if first_ex_borrow is None:
                        first_ex_borrow = ex_borrow
                    last_ex_borrow = ex_borrow

                # Convert normal delta to raw shares and accumulate
                if supply_delta != 0 and ex_supply > 0:
                    raw_delta = (supply_delta * PRECISION) // ex_supply
                    sum_raw_supply += raw_delta
                if borrow_delta != 0 and ex_borrow > 0:
                    raw_delta = (borrow_delta * PRECISION) // ex_borrow
                    sum_raw_borrow += raw_delta

        print(f"\n  Events: {len(rows)}")
        print(f"  Period: {rows[0][2]} -> {rows[-1][2]}")

        print(f"\n  --- Method 1: Simple Delta Accumulation (IGNORES interest) ---")
        print(f"  Σ(supplyDelta) = {sum_supply_delta:,}")
        print(f"  Σ(borrowDelta) = {sum_borrow_delta:,}")
        print(f"    Deposits:    +{sum_supply_positive:,}")
        print(f"    Withdrawals: {sum_supply_negative:,}")
        print(f"    Borrows:     +{sum_borrow_positive:,}")
        print(f"    Repays:      {sum_borrow_negative:,}")

        print(f"\n  --- Method 2: Raw Share Accumulation (INCLUDES interest) ---")
        print(f"  Σ(rawSupplyShares) = {sum_raw_supply:,}")
        print(f"  Σ(rawBorrowShares) = {sum_raw_borrow:,}")
        if last_ex_supply and last_ex_supply > 0:
            current_normal_supply = (sum_raw_supply * last_ex_supply) // PRECISION
            print(f"  Current normal supply = rawShares × lastExPrice / 1e12")
            print(f"    = {sum_raw_supply:,} × {last_ex_supply:,} / 1e12 = {current_normal_supply:,}")
        if last_ex_borrow and last_ex_borrow > 0:
            current_normal_borrow = (sum_raw_borrow * last_ex_borrow) // PRECISION
            print(f"  Current normal borrow = rawShares × lastExPrice / 1e12")
            print(f"    = {sum_raw_borrow:,} × {last_ex_borrow:,} / 1e12 = {current_normal_borrow:,}")

        print(f"\n  --- Exchange Price Growth ---")
        if first_ex_supply and last_ex_supply:
            growth = (last_ex_supply / first_ex_supply - 1) * 100
            print(f"  Supply: {first_ex_supply:,} -> {last_ex_supply:,} (+{growth:.4f}%)")
        if first_ex_borrow and last_ex_borrow:
            growth = (last_ex_borrow / first_ex_borrow - 1) * 100
            print(f"  Borrow: {first_ex_borrow:,} -> {last_ex_borrow:,} (+{growth:.4f}%)")

        # 3. Get current RPC state for comparison
        try:
            sig = "0x" + keccak(text="getVaultEntireData(address)")[:4].hex()
            addr_padded = vault.removeprefix("0x").rjust(64, "0")
            resp = requests.post(RPC_URL, json={
                "jsonrpc": "2.0", "id": 1, "method": "eth_call",
                "params": [{"to": RESOLVER, "data": sig + addr_padded}, "latest"]
            }, timeout=60).json()
            raw_rpc = bytes.fromhex(resp["result"][2:])
            entire = abi_decode([VAULT_ENTIRE_TYPE], raw_rpc, strict=False)[0]
            rpc_totals = entire[6]

            rpc_supply_vault = int(rpc_totals[0])
            rpc_borrow_vault = int(rpc_totals[1])

            print(f"\n  --- RPC Current State (latest block) ---")
            print(f"  RPC totalSupplyVault = {rpc_supply_vault:,}")
            print(f"  RPC totalBorrowVault = {rpc_borrow_vault:,}")

            # Compare methods
            if rpc_supply_vault > 1000:
                delta_pct_m1 = abs(sum_supply_delta - rpc_supply_vault) / rpc_supply_vault * 100
                print(f"\n  Supply comparison:")
                print(f"    Method 1 (delta sum):     {sum_supply_delta:,} -> error: {delta_pct_m1:.2f}%")
                if last_ex_supply:
                    current_m2 = (sum_raw_supply * last_ex_supply) // PRECISION
                    delta_pct_m2 = abs(current_m2 - rpc_supply_vault) / rpc_supply_vault * 100
                    print(f"    Method 2 (raw × exPrice): {current_m2:,} -> error: {delta_pct_m2:.2f}%")
                    interest = current_m2 - sum_supply_delta
                    print(f"    Implied interest earned:  {interest:,}")

            if rpc_borrow_vault > 1000:
                delta_pct_m1 = abs(sum_borrow_delta - rpc_borrow_vault) / rpc_borrow_vault * 100
                print(f"\n  Borrow comparison:")
                print(f"    Method 1 (delta sum):     {sum_borrow_delta:,} -> error: {delta_pct_m1:.2f}%")
                if last_ex_borrow:
                    current_m2 = (sum_raw_borrow * last_ex_borrow) // PRECISION
                    delta_pct_m2 = abs(current_m2 - rpc_borrow_vault) / rpc_borrow_vault * 100
                    print(f"    Method 2 (raw × exPrice): {current_m2:,} -> error: {delta_pct_m2:.2f}%")
                    interest = current_m2 - sum_borrow_delta
                    print(f"    Implied interest owed:    {interest:,}")

        except Exception as exc:
            print(f"  RPC FAILED: {exc}")


if __name__ == "__main__":
    main()
