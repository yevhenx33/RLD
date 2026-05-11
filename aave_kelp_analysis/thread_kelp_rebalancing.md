# The rsETH Exploit Didn't Break DeFi Lending — It Fixed the Market Structure

🧵 5 charts. On-chain data only. No opinions without receipts.

---

## 1/5 — The Great Rebalancing

Before the rsETH exploit, one protocol held 87% of all lending supply. That's not a market — it's a single point of failure.

In 3 weeks, $13.8B left Aave V3. Capital didn't leave DeFi. It diversified.

Aave: 87% → 73%
Spark: 8% → 17%
Morpho: 5% → 10%

> 📎 thread_1_rebalancing.png

---

## 2/5 — Capital Found Better Homes

Spark absorbed +$1.7B (+55%) — mostly wstETH and WETH from LPs fleeing Aave's utilization lock.

Morpho grew +$0.8B (+39%) — isolated architecture meant zero contagion from rsETH. Users had no reason to panic.

The remaining ~$11B? Leveraged positions unwinding. That's not capital "leaving DeFi" — it's excess leverage being cleaned from the system.

> 📎 thread_2_capital_homes.png

---

## 3/5 — Competition Restored Rate Discipline

Pre-exploit, WETH borrow rates were identical across protocols (~2.0–2.3%). The exploit created violent divergence: Spark spiked to 12.3%, Aave to 8.4%.

Capital flows arbitraged the spread. Users borrowed where rates were low, supplied where rates were high. By May, all three converged to the 2.3–4.0% band.

No governance intervention. No emergency proposals. Just market discipline.

> 📎 thread_3_weth_discipline.png

---

## 4/5 — The USDC Arbitrage Opportunity

Peak divergence (April 20):
• Aave USDC borrow: 14.0%
• Spark USDC borrow: 4.7%
• Morpho USDC borrow: 7.2%

A 9pp spread on the same asset. In TradFi, arbs close this in seconds. In DeFi, it took ~10 days — but it closed.

The mechanism: users repaid expensive Aave borrows and re-opened on Spark/Morpho. The market's immune response.

> 📎 thread_4_usdc_arbitrage.png

---

## 5/5 — The Case for IRS

Both WETH and USDC showed the same pattern: crisis spreads of 9pp, compressed back to 1–2pp over 10 days via manual retail arbitrage.

Manual rate arbitrage across protocols takes 10 days to close a 9pp spread. An interest rate swap protocol would compress it to hours.

The competitive multi-protocol market isn't a crisis outcome — it's a feature. The rate spread data is proof that an IRS is the missing financial plumbing to make it efficient.

> 📎 thread_5_irs_thesis.png

---

*Data: RLD ClickHouse on-chain event tables. Daily snapshots (argMax). Rates: avg daily APY (Morpho: supply-weighted WETH, median USDC >$10M markets). Window: Apr 13 – May 10, 2026.*
