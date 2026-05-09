#!/usr/bin/env python3
"""
Fluid Vault APY Backfill
========================
Backfills supply_apy and borrow_apy into fluid_vault_timeseries by joining
against the per-token APY data already in fluid_timeseries.

For each vault:
  supply_apy = collateral_token's supply_apy from fluid_timeseries
  borrow_apy = debt_token's borrow_apy from fluid_timeseries

Uses ReplacingMergeTree semantics: re-inserts the full row with APY populated,
the newer insert_at wins at merge time.
"""

from __future__ import annotations

import logging
import os
import sys

import clickhouse_connect

log = logging.getLogger("fluid_vault_apy_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")


def get_ch() -> clickhouse_connect.driver.Client:
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "rld_clickhouse"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", os.getenv("CLICKHOUSE_PASS", "")),
    )


def backfill_apy(ch) -> int:
    """
    Backfill APY by joining vault timeseries against per-token APY data.
    
    Strategy:
      1. Build daily per-token APY lookup from fluid_timeseries
      2. Read all vault timeseries rows
      3. For each row, look up collateral token's supply_apy and debt token's borrow_apy
      4. Re-insert rows with APY populated
    """
    
    # Step 1: Build daily per-token APY lookup
    # {(token, day_str) -> (supply_apy, borrow_apy)}
    log.info("Loading per-token daily APY from fluid_timeseries...")
    apy_rows = ch.query("""
        SELECT entity_id, toStartOfDay(timestamp) AS day,
               avg(supply_apy) AS avg_supply_apy,
               avg(borrow_apy) AS avg_borrow_apy
        FROM fluid_timeseries
        WHERE protocol = 'FLUID_MARKET'
        GROUP BY entity_id, day
        ORDER BY entity_id, day
    """).result_rows
    
    token_daily_apy: dict[tuple[str, str], tuple[float, float]] = {}
    for entity_id, day, supply_apy, borrow_apy in apy_rows:
        key = (str(entity_id).lower(), str(day)[:10])
        token_daily_apy[key] = (float(supply_apy or 0), float(borrow_apy or 0))
    log.info("Loaded %d token-day APY entries", len(token_daily_apy))
    
    # Also load from fluid_reserve_metrics for additional coverage
    metrics_rows = ch.query("""
        SELECT token, toStartOfDay(timestamp) AS day,
               avg(supply_apy) AS avg_supply_apy,
               avg(borrow_apy) AS avg_borrow_apy
        FROM fluid_reserve_metrics
        GROUP BY token, day
        ORDER BY token, day
    """).result_rows
    
    metrics_count = 0
    for token, day, supply_apy, borrow_apy in metrics_rows:
        key = (str(token).lower(), str(day)[:10])
        if key not in token_daily_apy:
            token_daily_apy[key] = (float(supply_apy or 0), float(borrow_apy or 0))
            metrics_count += 1
    log.info("Added %d entries from fluid_reserve_metrics (total: %d)", metrics_count, len(token_daily_apy))
    
    # Step 2: Read all vault timeseries rows
    log.info("Reading vault timeseries...")
    columns = [
        "timestamp", "vault_id", "symbol", "collateral_token", "debt_token",
        "supply_raw_shares", "borrow_raw_shares", "supply_tokens", "borrow_tokens",
        "supply_usd", "borrow_usd", "utilization",
        "supply_ex_price", "borrow_ex_price", "supply_apy", "borrow_apy",
        "supply_inflow_usd", "supply_outflow_usd",
        "borrow_inflow_usd", "borrow_outflow_usd",
        "net_supply_flow_usd", "net_borrow_flow_usd", "event_count",
    ]
    vault_rows = ch.query(f"""
        SELECT {', '.join(columns)}
        FROM fluid_vault_timeseries FINAL
        ORDER BY vault_id, timestamp
    """).result_rows
    log.info("Read %d vault timeseries rows", len(vault_rows))
    
    # Step 3: Compute APY for each row
    updated_rows = []
    matched = 0
    unmatched = 0
    
    for row in vault_rows:
        row_dict = dict(zip(columns, row))
        day_str = str(row_dict["timestamp"])[:10]
        col_token = str(row_dict["collateral_token"]).lower()
        debt_token = str(row_dict["debt_token"]).lower()
        
        # Look up collateral token supply APY
        col_apy = token_daily_apy.get((col_token, day_str))
        supply_apy = col_apy[0] if col_apy else 0.0
        
        # Look up debt token borrow APY
        debt_apy = token_daily_apy.get((debt_token, day_str))
        borrow_apy = debt_apy[1] if debt_apy else 0.0
        
        if supply_apy > 0 or borrow_apy > 0:
            matched += 1
        else:
            unmatched += 1
        
        row_dict["supply_apy"] = supply_apy
        row_dict["borrow_apy"] = borrow_apy
        updated_rows.append(row_dict)
    
    log.info("APY matched: %d rows, unmatched: %d rows (%.1f%% coverage)",
             matched, unmatched, 
             matched / max(1, matched + unmatched) * 100)
    
    # Step 4: Re-insert with APY populated
    if not updated_rows:
        log.warning("No rows to write")
        return 0
    
    data = [[row[col] for col in columns] for row in updated_rows]
    ch.insert("fluid_vault_timeseries", data, column_names=columns)
    log.info("Wrote %d rows with APY backfilled", len(data))
    
    # Verify
    verify = ch.query("""
        SELECT 
            countIf(supply_apy > 0 OR borrow_apy > 0) AS with_apy,
            count() AS total,
            avg(supply_apy) AS avg_supply_apy,
            avg(borrow_apy) AS avg_borrow_apy
        FROM fluid_vault_timeseries FINAL
    """).result_rows
    if verify:
        r = verify[0]
        log.info("Verification: %d/%d rows have APY (avg supply=%.4f, avg borrow=%.4f)",
                 r[0], r[1], r[2], r[3])
    
    return len(data)


def main():
    ch = get_ch()
    written = backfill_apy(ch)
    log.info("BACKFILL COMPLETE: %d rows updated", written)


if __name__ == "__main__":
    main()
