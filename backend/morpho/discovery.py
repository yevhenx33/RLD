"""Morpho Blue Indexer — Market & Vault Discovery via Morpho API."""
import json, time, logging
from urllib.request import Request, urlopen
from morpho.config import MORPHO_API_URL, MIN_MARKET_SUPPLY_USD
from morpho.db import get_conn

log = logging.getLogger(__name__)

MARKETS_QUERY = """{
  markets(where: { chainId_in: [1] }, first: 500, orderBy: SupplyAssetsUsd, orderDirection: Desc) {
    items {
      uniqueKey lltv oracleAddress irmAddress
      loanAsset { symbol address decimals }
      collateralAsset { symbol address decimals }
      state { supplyAssetsUsd }
      supplyingVaults { address name symbol asset { symbol address } }
    }
  }
}"""

def discover_markets_and_vaults():
    """Fetch current markets+vaults from Morpho API and upsert into DB."""
    log.info("Discovering markets and vaults from Morpho API...")
    req = Request(MORPHO_API_URL,
                  data=json.dumps({"query": MARKETS_QUERY}).encode(),
                  headers={"Content-Type": "application/json"})
    resp = json.loads(urlopen(req, timeout=30).read())
    items = resp["data"]["markets"]["items"]
    now = int(time.time())

    markets = []
    vaults = {}

    for m in items:
        if m["state"]["supplyAssetsUsd"] < MIN_MARKET_SUPPLY_USD:
            continue
        col = m.get("collateralAsset") or {}
        markets.append({
            "market_id": m["uniqueKey"],
            "loan_token": m["loanAsset"]["address"],
            "loan_symbol": m["loanAsset"]["symbol"],
            "loan_decimals": m["loanAsset"].get("decimals"),
            "collateral_token": col.get("address"),
            "collateral_symbol": col.get("symbol"),
            "collateral_decimals": col.get("decimals"),
            "oracle": m.get("oracleAddress"),
            "irm": m.get("irmAddress"),
            "lltv": float(m["lltv"]) / 1e18 if m.get("lltv") else None,
            "discovered_at": now,
        })
        for v in m.get("supplyingVaults", []):
            if v["address"] not in vaults:
                asset = v.get("asset") or {}
                vaults[v["address"]] = {
                    "vault_address": v["address"],
                    "name": v.get("name"),
                    "symbol": v.get("symbol"),
                    "asset_address": asset.get("address"),
                    "asset_symbol": asset.get("symbol"),
                    "discovered_at": now,
                }

    with get_conn() as conn:
        for mp in markets:
            conn.execute("""
                INSERT OR IGNORE INTO market_params
                (market_id, loan_token, loan_symbol, loan_decimals,
                 collateral_token, collateral_symbol, collateral_decimals,
                 oracle, irm, lltv, discovered_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (mp["market_id"], mp["loan_token"], mp["loan_symbol"],
                  mp["loan_decimals"], mp["collateral_token"], mp["collateral_symbol"],
                  mp["collateral_decimals"], mp["oracle"], mp["irm"],
                  mp["lltv"], mp["discovered_at"]))
        for va in vaults.values():
            conn.execute("""
                INSERT OR IGNORE INTO vault_meta
                (vault_address, name, symbol, asset_address, asset_symbol, discovered_at)
                VALUES (?,?,?,?,?,?)
            """, (va["vault_address"], va["name"], va["symbol"],
                  va["asset_address"], va["asset_symbol"], va["discovered_at"]))

    log.info(f"Discovered {len(markets)} markets, {len(vaults)} vaults")
    return markets, list(vaults.values())
