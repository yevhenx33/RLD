#!/usr/bin/env python3
"""
JTM TWAMM Earnings Simulation
==============================
Pure Python model of the JTM earningsFactor accounting to verify the
ghost-earnings fix (earningsFactorAtInterval cap for expired orders).

Reproduces:
  1. The bug  — expired orders steal earnings via inflated earningsFactor
  2. The fix  — capping earningsFactor at the epoch snapshot freezes earnings

Run: python3 backend/tools/jtm_simulation.py
"""

from dataclasses import dataclass, field
from typing import Dict, Optional
import math

# ── Constants ───────────────────────────────────────────────────────

Q96 = 2**96
RATE_SCALER = 10**18
EXPIRATION_INTERVAL = 3600   # 1 hour
DISCOUNT_RATE_BPS_PER_SEC = 1
MAX_DISCOUNT_BPS = 500
TWAP_PRICE = 3_380_000       # 3.38 waUSDC per wRLP (6 decimals)


# ── Data Structures ─────────────────────────────────────────────────

@dataclass
class Order:
    owner: str
    sell_rate: int           # scaled (× RATE_SCALER)
    earnings_factor_last: int
    expiration: int
    zero_for_one: bool       # True = selling token0 (wRLP)
    deposit: int             # raw tokens deposited

@dataclass
class StreamPool:
    sell_rate_current: int = 0
    earnings_factor_current: int = 0
    sell_rate_ending: Dict[int, int] = field(default_factory=dict)
    earnings_factor_at_interval: Dict[int, int] = field(default_factory=dict)

@dataclass
class JITState:
    stream0_for1: StreamPool = field(default_factory=StreamPool)  # wRLP sellers
    stream1_for0: StreamPool = field(default_factory=StreamPool)  # waUSDC sellers
    accrued0: int = 0   # ghost wRLP
    accrued1: int = 0   # ghost waUSDC
    last_update: int = 0
    last_clear: int = 0
    orders: Dict[str, Order] = field(default_factory=dict)
    # Physical token balances held by the hook
    balance0: int = 0   # wRLP in contract
    balance1: int = 0   # waUSDC in contract
    # Tokens owed to users (claimable)
    tokens_owed0: Dict[str, int] = field(default_factory=dict)
    tokens_owed1: Dict[str, int] = field(default_factory=dict)


# ── Simulation Engine ───────────────────────────────────────────────

class JTMSim:
    # Fix modes:
    #   'none'      = current buggy code
    #   'cap'       = cap earningsFactor at epoch snapshot (doesn't work: snapshot=0)
    #   'defer'     = defer sellRate subtraction until sync/cancel 
    #   'snapshot'  = net BEFORE epoch crossing, then cap at snapshot (correct fix)
    def __init__(self, fix_mode: str = 'none'):
        self.state = JITState()
        self.state.last_update = 1737770400  # start time
        self.state.last_clear = self.state.last_update
        self.fix_mode = fix_mode
        self.label = {'none': 'BUGGY', 'cap': 'CAP_FIX', 'defer': 'DEFER_FIX', 'snapshot': 'SNAPSHOT_FIX'}[fix_mode]
        self.clears = 0

    def _get_interval(self, t: int) -> int:
        return (t // EXPIRATION_INTERVAL) * EXPIRATION_INTERVAL

    def submit_order(self, owner: str, zero_for_one: bool, amount: int, duration: int, t: int):
        """Submit a TWAMM order."""
        self._accrue_and_net(t)
        
        current_interval = self._get_interval(t)
        expiration = current_interval + duration
        sell_rate = amount // duration
        scaled_sell_rate = sell_rate * RATE_SCALER
        
        stream = self.state.stream0_for1 if zero_for_one else self.state.stream1_for0
        stream.sell_rate_current += scaled_sell_rate
        stream.sell_rate_ending[expiration] = stream.sell_rate_ending.get(expiration, 0) + scaled_sell_rate
        
        order_id = f"{owner}_{expiration}_{zero_for_one}"
        actual_deposit = sell_rate * duration
        self.state.orders[order_id] = Order(
            owner=owner, sell_rate=scaled_sell_rate,
            earnings_factor_last=stream.earnings_factor_current,
            expiration=expiration, zero_for_one=zero_for_one,
            deposit=actual_deposit
        )
        
        # Physical transfer in
        if zero_for_one:
            self.state.balance0 += actual_deposit
        else:
            self.state.balance1 += actual_deposit
        
        tok = "wRLP" if zero_for_one else "waUSDC"
        print(f"  [{self.label}] t={t}: {owner} submits {actual_deposit/1e6:.0f} {tok} order, exp={expiration}, sellRate={sell_rate}")

    def _cross_epoch(self, stream: StreamPool, epoch: int):
        """Cross an epoch boundary — snapshot earningsFactor and remove expired sellRate."""
        expiring = stream.sell_rate_ending.get(epoch, 0)
        if expiring > 0:
            # Store the earningsFactor snapshot at this epoch
            stream.earnings_factor_at_interval[epoch] = stream.earnings_factor_current
            if self.fix_mode != 'defer':
                # Original behavior: subtract immediately (causes ghost earnings)
                stream.sell_rate_current -= expiring
            # With 'defer': keep expired sellRate in denominator until sync/cancel

    def _record_earnings(self, stream: StreamPool, earnings: int):
        """Record earnings into the stream's earningsFactor."""
        if stream.sell_rate_current > 0 and earnings > 0:
            delta = (earnings * Q96 * RATE_SCALER) // stream.sell_rate_current
            stream.earnings_factor_current += delta

    def _internal_net(self, t: int):
        """Layer 1: Net opposing ghost balances at TWAP price."""
        a0 = self.state.accrued0
        a1 = self.state.accrued1
        if a0 == 0 or a1 == 0:
            return
        
        # Match at TWAP: 1 wRLP = TWAP_PRICE waUSDC
        # matchable_from_0 = a0 tokens of wRLP = a0 * TWAP_PRICE waUSDC value
        # matchable_from_1 = a1 tokens of waUSDC
        value0_in_1 = (a0 * TWAP_PRICE) // 10**6  # wRLP value in waUSDC terms
        
        if value0_in_1 <= a1:
            # All of token0 can be matched
            matched0 = a0
            matched1 = value0_in_1
        else:
            # All of token1 can be matched
            matched1 = a1
            matched0 = (a1 * 10**6) // TWAP_PRICE
        
        if matched0 > 0 and matched1 > 0:
            self.state.accrued0 -= matched0
            self.state.accrued1 -= matched1
            # Record earnings: wRLP sellers earn waUSDC, waUSDC sellers earn wRLP
            self._record_earnings(self.state.stream0_for1, matched1)  # wRLP sellers get waUSDC ✅
            self._record_earnings(self.state.stream1_for0, matched0)  # waUSDC sellers get wRLP ✅

    def _accrue_and_net(self, t: int):
        """Accrue ghost balances, cross epochs, and net."""
        if t <= self.state.last_update:
            return
        
        current = self.state.last_update
        while current < t:
            next_epoch = self._get_interval(current) + EXPIRATION_INTERVAL
            step_end = min(next_epoch, t)
            dt = step_end - current
            
            if dt > 0:
                # Accrue ghost balances
                if self.state.stream0_for1.sell_rate_current > 0:
                    self.state.accrued0 += (self.state.stream0_for1.sell_rate_current * dt) // RATE_SCALER
                if self.state.stream1_for0.sell_rate_current > 0:
                    self.state.accrued1 += (self.state.stream1_for0.sell_rate_current * dt) // RATE_SCALER
            
            # At epoch boundary:
            if step_end == next_epoch:
                if self.fix_mode == 'snapshot':
                    # FIX: Net FIRST (so earnings are recorded while sellRateCurrent
                    # still includes expiring orders), THEN cross epoch (snapshot + subtract)
                    self._internal_net(step_end)
                    self._cross_epoch(self.state.stream0_for1, next_epoch)
                    self._cross_epoch(self.state.stream1_for0, next_epoch)
                else:
                    # Original: cross epoch first, then net (snapshot is 0!)
                    self._cross_epoch(self.state.stream0_for1, next_epoch)
                    self._cross_epoch(self.state.stream1_for0, next_epoch)
            
            current = step_end
        
        # Net after accrual (for non-epoch steps, or if not already netted at epoch)
        self._internal_net(t)
        self.state.last_update = t

    def clear(self, caller: str, zero_for_one: bool, t: int):
        """Layer 3: Clear accrued tokens at discount."""
        self._accrue_and_net(t)
        
        available = self.state.accrued0 if zero_for_one else self.state.accrued1
        if available == 0:
            return
        
        stream = self.state.stream0_for1 if zero_for_one else self.state.stream1_for0
        if stream.sell_rate_current == 0:
            return
        
        discount = min((t - self.state.last_clear) * DISCOUNT_RATE_BPS_PER_SEC, MAX_DISCOUNT_BPS)
        
        # Payment at TWAP minus discount
        if zero_for_one:
            # Buy wRLP, pay waUSDC
            payment = (available * TWAP_PRICE * (10000 - discount)) // (10**6 * 10000)
            self.state.accrued0 = 0
            self._record_earnings(stream, payment)
            self.state.balance1 += payment  # arb pays waUSDC in
            self.clears += 1
        else:
            payment = (available * 10**6 * (10000 - discount)) // (TWAP_PRICE * 10000)
            self.state.accrued1 = 0
            self._record_earnings(stream, payment)
            self.state.balance0 += payment  # arb pays wRLP in
            self.clears += 1
        
        self.state.last_clear = t

    def get_order_state(self, order_id: str) -> tuple:
        """getCancelOrderState equivalent — returns (buyTokensOwed, sellRefund)."""
        order = self.state.orders[order_id]
        stream = self.state.stream0_for1 if order.zero_for_one else self.state.stream1_for0
        
        effective_ef = stream.earnings_factor_current
        if self.fix_mode in ('cap', 'snapshot') and self.state.last_update >= order.expiration:
            snapshot = stream.earnings_factor_at_interval.get(order.expiration, 0)
            if snapshot > 0 and snapshot < effective_ef:
                effective_ef = snapshot
        # 'defer' mode: no special capping
        # 'snapshot' mode: snapshot now contains the correct value because
        # netting ran BEFORE _crossEpoch snapshotted it
        
        # Compute earnings
        ef_delta = effective_ef - order.earnings_factor_last
        buy_owed = 0
        if ef_delta > 0:
            buy_owed = (order.sell_rate * ef_delta) // (Q96 * RATE_SCALER)
        
        # Compute refund
        sell_refund = 0
        if self.state.last_update < order.expiration:
            remaining = order.expiration - self.state.last_update
            sell_refund = (order.sell_rate * remaining) // RATE_SCALER
        
        return buy_owed, sell_refund

    def sync_order(self, order_id: str):
        """Sync/cancel an order — settle earnings and remove from stream."""
        order = self.state.orders[order_id]
        buy, ref = self.get_order_state(order_id)
        stream = self.state.stream0_for1 if order.zero_for_one else self.state.stream1_for0
        
        # Credit tokens
        if order.zero_for_one:
            self.state.tokens_owed1[order.owner] = self.state.tokens_owed1.get(order.owner, 0) + buy
        else:
            self.state.tokens_owed0[order.owner] = self.state.tokens_owed0.get(order.owner, 0) + buy
        
        # For 'defer' mode: NOW subtract from sellRateCurrent
        if self.fix_mode == 'defer' and self.state.last_update >= order.expiration:
            stream.sell_rate_current -= order.sell_rate
        
        # Update order's earningsFactor snapshot
        order.earnings_factor_last = stream.earnings_factor_current
        
        tok_buy = 'waUSDC' if order.zero_for_one else 'wRLP'
        print(f"  [{self.label}] sync {order.owner}: {buy/1e6:.2f} {tok_buy} earned")

    def solvency_report(self, header: str = ""):
        """Print full solvency analysis."""
        print(f"\n{'='*60}")
        print(f"  SOLVENCY: {self.label}  {header}")
        print(f"{'='*60}")
        
        total_buy0 = 0   # total wRLP owed as buy tokens
        total_buy1 = 0   # total waUSDC owed as buy tokens
        total_ref0 = 0   # total wRLP refunds
        total_ref1 = 0   # total waUSDC refunds
        
        for oid, order in self.state.orders.items():
            buy, ref = self.get_order_state(oid)
            expired = self.state.last_update >= order.expiration
            buy_tok = "waUSDC" if order.zero_for_one else "wRLP"
            sell_tok = "wRLP" if order.zero_for_one else "waUSDC"
            status = "[EXPIRED]" if expired else "[ACTIVE]"
            
            print(f"\n  {order.owner} {status}")
            print(f"    deposit: {order.deposit/1e6:.0f} {sell_tok}")
            print(f"    buyOwed: {buy/1e6:.2f} {buy_tok}   sellRefund: {ref/1e6:.2f} {sell_tok}")
            
            if order.zero_for_one:
                total_buy1 += buy   # wRLP sellers earn waUSDC
                total_ref0 += ref   # wRLP refunds
            else:
                total_buy0 += buy   # waUSDC sellers earn wRLP
                total_ref1 += ref   # waUSDC refunds
        
        # Add accrued
        demand0 = total_buy0 + total_ref0 + self.state.accrued0
        demand1 = total_buy1 + total_ref1 + self.state.accrued1
        
        delta0 = self.state.balance0 - demand0
        delta1 = self.state.balance1 - demand1
        
        print(f"\n  {'─'*50}")
        print(f"  wRLP:   demand={demand0/1e6:>10.2f}  supply={self.state.balance0/1e6:>10.2f}  Δ={delta0/1e6:>10.2f}  {'✅' if delta0 >= 0 else '❌ INSOLVENT'}")
        print(f"  waUSDC: demand={demand1/1e6:>10.2f}  supply={self.state.balance1/1e6:>10.2f}  Δ={delta1/1e6:>10.2f}  {'✅' if delta1 >= 0 else '❌ INSOLVENT'}")
        
        return delta0, delta1


# ── Run Simulation ──────────────────────────────────────────────────

def run_scenario(fix_mode: str = 'none'):
    sim = JTMSim(fix_mode=fix_mode)
    T0 = sim.state.last_update
    
    print(f"\n{'#'*60}")
    print(f"  SCENARIO: {sim.label}")
    print(f"{'#'*60}")
    
    # ══════════════════════════════════════════════════════════════
    # Scenario that reproduces the on-chain ghost earnings bug:
    #
    # Phase 1: SIM submits massive 100K waUSDC→wRLP (1h duration)
    #          BROKER submits small 333 wRLP→waUSDC (1h duration)
    #          These two partially net (333 wRLP worth), rest accrues
    #
    # Phase 2: Both expire at 1h. _crossEpoch snapshots earningsFactor
    #          and removes sellRates from sellRateCurrent.
    #
    # Phase 3: CHAOS submits 5000 wRLP→waUSDC (2h) + BROKER_B 100 waUSDC→wRLP (2h)
    #          The opposing flow creates netting & clears. Each netting/clearing
    #          calls _recordEarnings on BOTH streams. But stream1For0 now has
    #          sellRateCurrent = 100 waUSDC rate (tiny), while the expired SIM
    #          order had sellRate = 100K waUSDC rate (massive).
    #          Post-expiration earnings inflate SIM's buyOwed exponentially.
    # ══════════════════════════════════════════════════════════════
    
    print("\n── Phase 1: Submit initial opposing orders ──")
    
    # SIM: 100,000 waUSDC → wRLP over 1 hour (zfo=false)
    sim.submit_order("SIM", False, 100_000 * 10**6, EXPIRATION_INTERVAL, T0)
    
    # BROKER_A: 333 wRLP → waUSDC over 1 hour (zfo=true)
    sim.submit_order("BROKER_A", True, 333 * 10**6, EXPIRATION_INTERVAL, T0)
    
    # ── Phase 2: 1 hour passes — both orders expire ──
    print("\n── Phase 2: Epoch crosses, initial orders expire ──")
    t1 = T0 + EXPIRATION_INTERVAL
    sim._accrue_and_net(t1)
    
    print(f"  After 1h: BOTH orders expired")
    print(f"    accrued0={sim.state.accrued0/1e6:.2f} wRLP")
    print(f"    accrued1={sim.state.accrued1/1e6:.2f} waUSDC")
    print(f"    stream0For1.sellRateCurrent={sim.state.stream0_for1.sell_rate_current} (should be 0)")
    print(f"    stream1For0.sellRateCurrent={sim.state.stream1_for0.sell_rate_current} (should be 0)")
    
    sim_id_1for0 = f"SIM_{T0 + EXPIRATION_INTERVAL}_False"
    buy_pre, _ = sim.get_order_state(sim_id_1for0)
    print(f"    SIM buyOwed (wRLP): {buy_pre/1e6:.2f}")
    
    d0_pre, d1_pre = sim.solvency_report("after initial orders expire")
    
    # ── Phase 3: New asymmetric orders + lots of clearing ──
    print("\n── Phase 3: New orders + clearing (post-expiration activity) ──")
    
    t2 = T0 + EXPIRATION_INTERVAL + 60  # just after expiry
    
    # CHAOS: 5000 wRLP → waUSDC over 2h (dominant stream)
    sim.submit_order("CHAOS", True, 5000 * 10**6, 2 * EXPIRATION_INTERVAL, t2)
    
    # BROKER_B: 100 waUSDC → wRLP over 2h (small opposing stream — creates nettable flow)
    sim.submit_order("BROKER_B", False, 100 * 10**6, 2 * EXPIRATION_INTERVAL, t2 + 10)
    
    # Clear every 5 minutes for 2 hours
    for i in range(24):
        tc = t2 + (i + 1) * 300
        sim.clear("BOT", True, tc)   # buy accrued wRLP, pay waUSDC
    
    # Let orders expire
    t4 = t2 + 2 * EXPIRATION_INTERVAL + 60
    sim._accrue_and_net(t4)
    
    # One last clear of remaining accrued
    sim.clear("BOT", True, t4)
    
    print(f"  Completed {sim.clears} clears")
    
    # ── SIM order now ──
    buy_post, _ = sim.get_order_state(sim_id_1for0)
    print(f"\n  SIM buyOwed BEFORE post-exp activity: {buy_pre/1e6:.2f} wRLP")
    print(f"  SIM buyOwed AFTER  post-exp activity: {buy_post/1e6:.2f} wRLP")
    print(f"  Ghost growth: +{(buy_post - buy_pre)/1e6:.2f} wRLP")
    if buy_post > buy_pre:
        print(f"  ⚠️ Expired order earned {(buy_post - buy_pre)/1e6:.2f} wRLP POST-EXPIRATION!")
    else:
        print(f"  ✅ No post-expiration earnings growth")
    
    # ── Final Solvency ──
    d0_final, d1_final = sim.solvency_report("FINAL — after all activity")
    
    return d0_final, d1_final


# ── Main ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  JTM TWAMM EARNINGS SIMULATION")
    print("  Verifying earningsFactorAtInterval cap fix")
    print("=" * 60)
    
    results = {}
    for mode in ['none', 'snapshot']:
        d0, d1 = run_scenario(fix_mode=mode)
        results[mode] = (d0, d1)
    
    # Summary
    print(f"\n{'='*60}")
    print(f"  COMPARISON")
    print(f"{'='*60}")
    for mode, (d0, d1) in results.items():
        label = {'none': 'BUGGY', 'snapshot': 'SNAPSHOT_FIX'}[mode]
        s0 = '✅' if d0 >= 0 else '❌'
        s1 = '✅' if d1 >= 0 else '❌'
        print(f"  {label:>14}: wRLP Δ={d0/1e6:>+12.2f} {s0}  waUSDC Δ={d1/1e6:>+12.2f} {s1}")
    
    d0_fix, d1_fix = results['snapshot']
    if d0_fix >= 0 and d1_fix >= 0:
        print(f"\n  🎉 SNAPSHOT FIX VERIFIED — both tokens solvent!")
    else:
        print(f"\n  ⚠️ Still insolvent — fix needs adjustment")
