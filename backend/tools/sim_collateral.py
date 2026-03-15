#!/usr/bin/env python3
"""
Broker Collateral Verification Simulation
==========================================
For each broker, compares:
  A) Derived collateral (from indexed DB data + current prices)
  B) On-chain ground truth (from getFullState() RPC call)

Components:
  1. waUSDC balance       → value = balance (1:1)
  2. wRLP balance         → value = balance * mark_price / index_price
  3. LP position value    → V4 math from (liquidity, tickLower, tickUpper, currentTick)
  4. TWAMM order value    → remaining sell + accrued buy
  5. Net Account Value    → sum of all components

Usage: python3 backend/tools/sim_collateral.py
"""
import subprocess
import json
import asyncio
import asyncpg
import math

DB_DSN = "postgresql://rld:rld_dev_password@localhost:5432/rld_indexer"
RPC_URL = "http://localhost:8545"

# ABI for getFullState()
FULL_STATE_ABI = [{
    "inputs": [],
    "name": "getFullState",
    "outputs": [{
        "components": [
            {"name": "collateralBalance", "type": "uint256"},
            {"name": "positionBalance", "type": "uint256"},
            {"name": "debtPrincipal", "type": "uint128"},
            {"name": "debtValue", "type": "uint256"},
            {"name": "twammSellOwed", "type": "uint256"},
            {"name": "twammBuyOwed", "type": "uint256"},
            {"name": "v4LPValue", "type": "uint256"},
            {"name": "netAccountValue", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
            {"name": "isSolvent", "type": "bool"},
        ],
        "name": "",
        "type": "tuple"
    }],
    "stateMutability": "view",
    "type": "function"
}]


def cast_call(contract: str, sig: str, rpc=RPC_URL) -> str:
    """Raw cast call returning hex output."""
    cmd = ["cast", "call", contract, sig, "--rpc-url", rpc]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def lp_value_at_tick(liquidity: int, tick_lower: int, tick_upper: int,
                     current_tick: int, mark_price: float) -> float:
    """
    Compute the USD value of an LP position at the current tick.
    token0 = wRLP (position token), token1 = waUSDC (stablecoin)
    Both are 6 decimals.
    """
    if liquidity == 0:
        return 0.0

    sa = 1.0001 ** (tick_lower / 2)
    sb = 1.0001 ** (tick_upper / 2)
    sp = 1.0001 ** (current_tick / 2)

    if current_tick < tick_lower:
        # All in token0
        amount0 = liquidity * (1/sa - 1/sb)
        amount1 = 0
    elif current_tick >= tick_upper:
        # All in token1
        amount0 = 0
        amount1 = liquidity * (sb - sa)
    else:
        # In range
        amount0 = liquidity * (1/sp - 1/sb)
        amount1 = liquidity * (sp - sa)

    # Convert from raw (6 dec) to human
    t0_human = amount0 / 1e6
    t1_human = amount1 / 1e6

    # value = wRLP_amount * mark_price + waUSDC_amount * 1
    return t0_human * mark_price + t1_human


def twamm_value(amount_in: int, start_epoch: int, expiration: int,
                current_time: int, zero_for_one: bool, mark_price: float) -> float:
    """
    Estimate the value of a TWAMM order.
    Simplified: assumes linear fill, remaining sell tokens are valued.
    """
    if current_time >= expiration:
        # Fully filled — value is in buy tokens (already settled)
        return 0.0

    duration = expiration - start_epoch
    if duration <= 0:
        return 0.0

    elapsed = current_time - start_epoch
    sell_rate = amount_in / duration
    sold = sell_rate * elapsed
    remaining = amount_in - sold

    # remaining sell tokens:
    # zero_for_one = True: selling token0 (wRLP) → value = remaining / 1e6 * mark_price
    # zero_for_one = False: selling token1 (waUSDC) → value = remaining / 1e6
    if zero_for_one:
        return (remaining / 1e6) * mark_price
    else:
        return remaining / 1e6


async def main():
    conn = await asyncpg.connect(DB_DSN)

    # ── Get market config ──
    market = await conn.fetchrow("SELECT * FROM markets LIMIT 1")
    market_id = market["market_id"]
    wausdc = market["wausdc"].lower()
    wrlp = market["wrlp"].lower()

    # ── Get current state from block_states ──
    latest = await conn.fetchrow("""
        SELECT mark_price, index_price, tick, block_timestamp
        FROM block_states WHERE market_id = $1
        ORDER BY block_number DESC LIMIT 1
    """, market_id)
    mark_price = float(latest["mark_price"])
    index_price = float(latest["index_price"])
    current_tick = int(latest["tick"])
    current_time = int(latest["block_timestamp"])

    print(f"Market: {market_id[:16]}...")
    print(f"Mark Price:   ${mark_price:.6f}")
    print(f"Index Price:  ${index_price:.6f}")
    print(f"Current Tick: {current_tick}")
    print(f"Current Time: {current_time}")
    print()

    # ── Get all brokers ──
    brokers = await conn.fetch("""
        SELECT address, owner, wausdc_balance, wrlp_balance, debt_principal
        FROM brokers WHERE market_id = $1
    """, market_id)

    print(f"{'='*90}")
    print(f"{'Broker':<14} {'Component':<18} {'Derived':>16} {'On-Chain':>16} {'Delta':>12}")
    print(f"{'='*90}")

    total_derived_nav = 0
    total_onchain_nav = 0

    for b in brokers:
        addr = b["address"]
        owner = b["owner"][:10] + "..."

        # ── Derived values from DB ──
        wausdc_bal = float(b["wausdc_balance"] or 0)  # already human-readable
        wrlp_bal = float(b["wrlp_balance"] or 0)

        # wRLP value: on-chain uses index_price for solvency, not mark_price
        # Check PrimeBroker logic — NAV typically uses index_price
        wrlp_val = wrlp_bal * index_price

        # LP positions for this broker
        lp_positions = await conn.fetch("""
            SELECT liquidity, tick_lower, tick_upper, is_burned
            FROM lp_positions
            WHERE broker_address = $1 AND is_burned = FALSE
        """, addr.lower())

        lp_val = 0.0
        for lp in lp_positions:
            liq = int(lp["liquidity"])
            lp_val += lp_value_at_tick(liq, lp["tick_lower"], lp["tick_upper"],
                                        current_tick, mark_price)

        # TWAMM orders for this broker
        twamm_orders = await conn.fetch("""
            SELECT amount_in, start_epoch, expiration, zero_for_one, is_cancelled
            FROM twamm_orders
            WHERE owner = $1 AND is_cancelled = FALSE
        """, addr.lower())

        twamm_val = 0.0
        for order in twamm_orders:
            twamm_val += twamm_value(
                int(order["amount_in"]), int(order["start_epoch"]),
                int(order["expiration"]), current_time,
                order["zero_for_one"], mark_price
            )

        derived_nav = wausdc_bal + wrlp_val + lp_val + twamm_val

        # ── On-chain ground truth via getFullState() ──
        try:
            raw = cast_call(addr, "getFullState()(uint256,uint256,uint128,uint256,uint256,uint256,uint256,uint256,uint256,bool)")
            lines = [l.strip().split(' ')[0].split('[')[0].strip() for l in raw.split('\n') if l.strip()]

            # Parse: collateralBalance, positionBalance, debtPrincipal, debtValue,
            #         twammSellOwed, twammBuyOwed, v4LPValue, netAccountValue, healthFactor, isSolvent
            oc_collateral = int(lines[0]) / 1e6 if len(lines) > 0 else 0
            oc_position = int(lines[1]) / 1e6 if len(lines) > 1 else 0
            oc_debt_principal = int(lines[2]) / 1e6 if len(lines) > 2 else 0
            oc_debt_value = int(lines[3]) / 1e6 if len(lines) > 3 else 0
            oc_twamm_sell = int(lines[4]) / 1e6 if len(lines) > 4 else 0
            oc_twamm_buy = int(lines[5]) / 1e6 if len(lines) > 5 else 0
            oc_v4lp = int(lines[6]) / 1e6 if len(lines) > 6 else 0
            oc_nav = int(lines[7]) / 1e6 if len(lines) > 7 else 0
            oc_hf = int(lines[8]) / 1e18 if len(lines) > 8 else 0
        except Exception as e:
            print(f"  ⚠️  getFullState() failed for {addr}: {e}")
            continue

        # ── Compare ──
        def row(component, derived, onchain):
            delta = derived - onchain
            flag = "✅" if abs(delta) < 1 else "❌"
            print(f"  {owner:<12} {component:<18} {derived:>14,.2f} {onchain:>14,.2f} {delta:>10,.2f} {flag}")

        row("waUSDC", wausdc_bal, oc_collateral)
        row("wRLP value", wrlp_val, oc_position * index_price)
        row("wRLP qty", wrlp_bal, oc_position)
        row("LP value", lp_val, oc_v4lp)
        row("TWAMM sell", twamm_val, oc_twamm_sell)
        row("TWAMM buy", 0, oc_twamm_buy)
        row("NAV", derived_nav, oc_nav)
        row("Debt principal", float(b["debt_principal"] or 0), oc_debt_principal)
        row("Health Factor", 0, oc_hf)
        print()

        total_derived_nav += derived_nav
        total_onchain_nav += oc_nav

    print(f"{'='*90}")
    print(f"  SYSTEM TOTAL NAV  Derived: ${total_derived_nav:>14,.2f}   On-Chain: ${total_onchain_nav:>14,.2f}   Delta: ${total_derived_nav - total_onchain_nav:>10,.2f}")
    print(f"{'='*90}")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
