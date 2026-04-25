#!/usr/bin/env python3
"""Smoke checks for the simulation indexer API.

The checks intentionally use HTTP only so the script can run from the host,
inside a container, or from simctl without requiring direct Postgres access.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def fetch_json(base_url: str, path: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}{path}", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
            return int(resp.status), payload
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except json.JSONDecodeError:
            payload = {"error": str(exc)}
        return int(exc.code), payload


def main() -> int:
    base_url = os.getenv("INDEXER_URL", "http://localhost:8080")
    required_types = {
        value.strip()
        for value in os.getenv("SIM_REQUIRED_MARKET_TYPES", "").split(",")
        if value.strip()
    }
    failures: list[str] = []
    results: dict[str, Any] = {"baseUrl": base_url, "checks": {}}

    for path in ["/healthz", "/readyz", "/api/status", "/api/latest"]:
        code, payload = fetch_json(base_url, path)
        results["checks"][path] = {"status": code, "payload": payload}
        if code != 200:
            failures.append(f"{path} returned HTTP {code}")

    status = results["checks"].get("/api/status", {}).get("payload", {})
    markets = status.get("markets") if isinstance(status.get("markets"), list) else []
    if not markets:
        failures.append("/api/status returned no markets")

    present_types = {str(m.get("marketType") or m.get("market_type")) for m in markets if isinstance(m, dict)}
    missing_types = sorted(required_types - present_types)
    if missing_types:
        failures.append(f"missing required market types: {', '.join(missing_types)}")

    for market in markets:
        if not isinstance(market, dict):
            continue
        market_id = str(market.get("marketId") or market.get("market_id") or "")
        market_type = str(market.get("marketType") or market.get("market_type") or "")
        if int(market.get("routeAnomalies") or 0) > 0:
            failures.append(f"{market_id} has route anomalies: {market.get('routeAnomalies')}")
        if int(market.get("indexerLagBlocks") or 0) > int(os.getenv("SIM_SMOKE_MAX_LAG_BLOCKS", "25")):
            failures.append(f"{market_id} is lagging by {market.get('indexerLagBlocks')} blocks")
        if market_type == "perp" and int(market.get("totalEvents") or 0) > 0:
            if int(market.get("swapCount") or 0) <= 0:
                failures.append(f"{market_id} perp has events but no swaps")
            if int(market.get("candleRows") or 0) <= 0:
                failures.append(f"{market_id} perp has events but no candle rows")
        if market_type == "cds":
            source_status = market.get("sourceStatus") if isinstance(market.get("sourceStatus"), list) else []
            if not source_status:
                failures.append(f"{market_id} cds has no source status")

    results["ok"] = not failures
    results["failures"] = failures
    print(json.dumps(results, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
