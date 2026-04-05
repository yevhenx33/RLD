"""
Morpho Blue Indexer — Main Entry Point.

Runs as a FastAPI service with background hourly collection.
"""
import asyncio, time, logging, os, sys
from fastapi import FastAPI
from contextlib import asynccontextmanager

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from morpho.db import init_db, get_conn, get_sync_value
from morpho.discovery import discover_markets_and_vaults
from morpho.collector import collect_snapshot
from morpho.config import SNAPSHOT_INTERVAL_SEC

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger(__name__)

async def background_loop():
    """Hourly snapshot collection loop."""
    while True:
        try:
            last_ts = get_sync_value("last_snapshot_ts")
            now = int(time.time())
            if last_ts and now - int(last_ts) < SNAPSHOT_INTERVAL_SEC - 60:
                wait = SNAPSHOT_INTERVAL_SEC - (now - int(last_ts))
                log.info(f"Next snapshot in {wait}s")
                await asyncio.sleep(min(wait, 60))
                continue

            # Re-discover markets/vaults every cycle
            discover_markets_and_vaults()
            # Collect snapshot
            collect_snapshot()
        except Exception as e:
            log.error(f"Snapshot error: {e}", exc_info=True)
        await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Morpho indexer starting...")
    init_db()
    # Initial discovery + snapshot
    try:
        discover_markets_and_vaults()
        collect_snapshot()
    except Exception as e:
        log.error(f"Initial collection failed: {e}", exc_info=True)
    # Start background loop
    task = asyncio.create_task(background_loop())
    yield
    task.cancel()

app = FastAPI(title="Morpho Blue Indexer", lifespan=lifespan)

@app.get("/")
def root():
    return {"service": "morpho-indexer", "status": "ok"}

@app.get("/status")
def status():
    return {
        "last_snapshot_ts": get_sync_value("last_snapshot_ts"),
        "last_snapshot_block": get_sync_value("last_snapshot_block"),
    }

@app.get("/markets")
def list_markets():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT mp.*, ms.utilization, ms.borrow_apy, ms.supply_apy,
                   ms.total_supply_assets, ms.total_borrow_assets, ms.oracle_price
            FROM market_params mp
            LEFT JOIN market_snapshots ms ON mp.market_id = ms.market_id
                AND ms.timestamp = (SELECT MAX(timestamp) FROM market_snapshots WHERE market_id = mp.market_id)
            ORDER BY CAST(ms.total_supply_assets AS REAL) DESC
        """).fetchall()
        return [dict(r) for r in rows]

@app.get("/vaults")
def list_vaults():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT vm.*, vs.share_price, vs.total_assets, vs.total_supply
            FROM vault_meta vm
            LEFT JOIN vault_snapshots vs ON vm.vault_address = vs.vault_address
                AND vs.timestamp = (SELECT MAX(timestamp) FROM vault_snapshots WHERE vault_address = vm.vault_address)
            ORDER BY CAST(vs.total_assets AS REAL) DESC
        """).fetchall()
        return [dict(r) for r in rows]

@app.get("/market/{market_id}/history")
def market_history(market_id: str, limit: int = 168):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM market_snapshots WHERE market_id = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (market_id, limit)).fetchall()
        return [dict(r) for r in rows]

@app.get("/vault/{vault_address}/allocations")
def vault_allocations(vault_address: str):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT va.*, mp.collateral_symbol, mp.loan_symbol
            FROM vault_allocations va
            JOIN market_params mp ON va.market_id = mp.market_id
            WHERE va.vault_address = ? AND va.timestamp = (
                SELECT MAX(timestamp) FROM vault_allocations WHERE vault_address = ?
            )
            ORDER BY CAST(va.supply_assets AS REAL) DESC
        """, (vault_address, vault_address)).fetchall()
        return [dict(r) for r in rows]

@app.get("/market/{market_id}/vaults")
def market_vaults(market_id: str):
    """Which vaults are exposed to this market (latest snapshot)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT va.*, vm.name as vault_name, vm.asset_symbol
            FROM vault_allocations va
            JOIN vault_meta vm ON va.vault_address = vm.vault_address
            WHERE va.market_id = ? AND va.timestamp = (
                SELECT MAX(timestamp) FROM vault_allocations WHERE market_id = ?
            )
            ORDER BY CAST(va.supply_assets AS REAL) DESC
        """, (market_id, market_id)).fetchall()
        return [dict(r) for r in rows]
