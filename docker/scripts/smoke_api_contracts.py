#!/usr/bin/env python3
"""
Smoke-check public/internal API contracts after deployment.

Usage:
  python3 docker/scripts/smoke_api_contracts.py
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _join(base: str, path: str) -> str:
    clean_base = base.rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    return f"{clean_base}{clean_path}"


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 3.0,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    body: bytes | None = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            status = int(resp.status)
            parsed = json.loads(raw) if raw else {}
            resp_headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
            return status, parsed if isinstance(parsed, dict) else {"raw": parsed}, resp_headers
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
            if not isinstance(parsed, dict):
                parsed = {"raw": parsed}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:500]}
        err_headers = {str(k).lower(): str(v) for k, v in exc.headers.items()}
        return int(exc.code), parsed, err_headers


def _post_graphql(url: str, query: str, timeout: float) -> tuple[int, dict[str, Any], dict[str, str]]:
    return _http_json("POST", url, payload={"query": query}, timeout=timeout)


def _mark(results: list[CheckResult], name: str, ok: bool, detail: str) -> None:
    results.append(CheckResult(name=name, ok=ok, detail=detail))
    symbol = "OK" if ok else "FAIL"
    print(f"[{symbol}] {name}: {detail}")


def _random_address() -> str:
    return "0x" + secrets.token_hex(20)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check API contracts")
    parser.add_argument("--sim-base", default="http://localhost:8080")
    parser.add_argument("--analytics-base", default="http://localhost:5000")
    parser.add_argument("--faucet-base", default="http://localhost:8088")
    parser.add_argument(
        "--public-base",
        default="",
        help="Optional public edge base URL, e.g. https://rld.fi",
    )
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--allow-not-ready", action="store_true")
    parser.add_argument(
        "--faucet-attempts",
        type=int,
        default=0,
        help="Optional destructive faucet checks (each attempt funds one random address).",
    )
    parser.add_argument("--min-faucet-success-rate", type=float, default=0.90)
    args = parser.parse_args()

    results: list[CheckResult] = []

    # Simulation API gates
    status, payload, _ = _http_json("GET", _join(args.sim_base, "/healthz"), timeout=args.timeout)
    _mark(results, "sim.healthz", status == 200 and payload.get("status") == "ok", f"http={status}")

    status, payload, _ = _http_json("GET", _join(args.sim_base, "/config"), timeout=args.timeout)
    has_market = bool(payload.get("market_id") or payload.get("marketId"))
    _mark(results, "sim.config", status == 200 and has_market, f"http={status}")

    status, payload, _ = _http_json("GET", _join(args.sim_base, "/api/latest"), timeout=args.timeout)
    _mark(
        results,
        "sim.api.latest",
        status == 200 and "snapshot" in payload and "block_number" in payload,
        f"http={status}",
    )

    status, payload, _ = _http_json("GET", _join(args.sim_base, "/api/status"), timeout=args.timeout)
    _mark(
        results,
        "sim.api.status",
        status == 200 and payload.get("status") == "ok",
        f"http={status}",
    )

    gql_status, gql_payload, _ = _post_graphql(
        _join(args.sim_base, "/graphql"),
        "query { __typename }",
        args.timeout,
    )
    _mark(
        results,
        "sim.graphql",
        gql_status == 200 and isinstance(gql_payload.get("data"), dict),
        f"http={gql_status}",
    )

    # Analytics API gates
    status, payload, _ = _http_json("GET", _join(args.analytics_base, "/livez"), timeout=args.timeout)
    _mark(results, "analytics.livez", status == 200 and payload.get("status") == "alive", f"http={status}")

    status, payload, _ = _http_json("GET", _join(args.analytics_base, "/readyz"), timeout=args.timeout)
    ready_ok = status == 200 and payload.get("status") in {"ready", "ok"}
    if args.allow_not_ready:
        _mark(results, "analytics.readyz", status in {200, 503}, f"http={status} status={payload.get('status')}")
    else:
        _mark(results, "analytics.readyz", ready_ok, f"http={status} status={payload.get('status')}")

    status, payload, _ = _http_json(
        "GET",
        _join(args.analytics_base, "/api/v1/oracle/usdc-borrow-apy"),
        timeout=args.timeout,
    )
    oracle_ok = status == 200 and payload.get("borrow_apy") is not None
    _mark(results, "analytics.oracle.usdc-borrow-apy", oracle_ok, f"http={status}")

    gql_status, gql_payload, _ = _post_graphql(
        _join(args.analytics_base, "/graphql"),
        "query { __typename }",
        args.timeout,
    )
    _mark(
        results,
        "analytics.graphql.canonical",
        gql_status == 200 and isinstance(gql_payload.get("data"), dict),
        f"http={gql_status}",
    )

    alias_status, alias_payload, alias_headers = _post_graphql(
        _join(args.analytics_base, "/envio-graphql"),
        "query { __typename }",
        args.timeout,
    )
    alias_ok = alias_status == 200 and isinstance(alias_payload.get("data"), dict)
    _mark(results, "analytics.graphql.alias", alias_ok, f"http={alias_status}")
    _mark(
        results,
        "analytics.graphql.alias.deprecation-header",
        bool(alias_headers.get("deprecation")),
        f"deprecation={alias_headers.get('deprecation', '')!r}",
    )
    _mark(
        results,
        "analytics.graphql.alias.sunset-header",
        bool(alias_headers.get("sunset")),
        f"sunset={alias_headers.get('sunset', '')!r}",
    )
    _mark(
        results,
        "analytics.graphql.alias.warning-header",
        bool(alias_headers.get("warning")),
        f"warning={alias_headers.get('warning', '')!r}",
    )

    # Faucet API gates
    status, payload, _ = _http_json("GET", _join(args.faucet_base, "/health"), timeout=args.timeout)
    faucet_health_ok = status == 200 and (
        payload.get("status") in {"healthy", "ok"} or bool(payload.get("ok"))
    )
    _mark(results, "faucet.health", faucet_health_ok, f"http={status}")

    if args.faucet_attempts > 0:
        successes = 0
        for _ in range(args.faucet_attempts):
            addr = _random_address()
            status, payload, _ = _http_json(
                "POST",
                _join(args.faucet_base, "/faucet"),
                payload={"address": addr},
                timeout=max(args.timeout, 8.0),
            )
            success = status == 200 and bool(payload.get("success"))
            successes += int(success)
        success_rate = successes / float(args.faucet_attempts)
        _mark(
            results,
            "faucet.success-rate",
            success_rate >= args.min_faucet_success_rate,
            f"rate={success_rate:.2%} threshold={args.min_faucet_success_rate:.2%}",
        )

    if args.public_base.strip():
        # Public edge contract checks (path allowlist + control-plane deny)
        status, payload, _ = _http_json("GET", _join(args.public_base, "/api/status"), timeout=args.timeout)
        _mark(results, "public.api.status", status == 200 and payload.get("status") == "ok", f"http={status}")

        status, payload, _ = _http_json("GET", _join(args.public_base, "/api/latest"), timeout=args.timeout)
        _mark(
            results,
            "public.api.latest",
            status == 200 and "snapshot" in payload and "block_number" in payload,
            f"http={status}",
        )

        gql_status, gql_payload, _ = _post_graphql(
            _join(args.public_base, "/graphql"),
            "query { __typename }",
            args.timeout,
        )
        _mark(
            results,
            "public.graphql.simulation",
            gql_status == 200 and isinstance(gql_payload.get("data"), dict),
            f"http={gql_status}",
        )

        gql_status, gql_payload, _ = _post_graphql(
            _join(args.public_base, "/analytics/graphql"),
            "query { __typename }",
            args.timeout,
        )
        _mark(
            results,
            "public.graphql.analytics",
            gql_status == 200 and isinstance(gql_payload.get("data"), dict),
            f"http={gql_status}",
        )

        alias_status, alias_payload, alias_headers = _post_graphql(
            _join(args.public_base, "/envio-graphql"),
            "query { __typename }",
            args.timeout,
        )
        alias_ok = alias_status == 200 and isinstance(alias_payload.get("data"), dict)
        _mark(results, "public.graphql.analytics.alias", alias_ok, f"http={alias_status}")
        _mark(
            results,
            "public.graphql.analytics.alias.deprecation-header",
            bool(alias_headers.get("deprecation")),
            f"deprecation={alias_headers.get('deprecation', '')!r}",
        )
        _mark(
            results,
            "public.graphql.analytics.alias.sunset-header",
            bool(alias_headers.get("sunset")),
            f"sunset={alias_headers.get('sunset', '')!r}",
        )
        _mark(
            results,
            "public.graphql.analytics.alias.warning-header",
            bool(alias_headers.get("warning")),
            f"warning={alias_headers.get('warning', '')!r}",
        )

        status, _, _ = _http_json("GET", _join(args.public_base, "/healthz"), timeout=args.timeout)
        _mark(results, "public.blocked.healthz", status == 404, f"http={status}")

        status, _, _ = _http_json(
            "POST",
            _join(args.public_base, "/admin/reset"),
            payload={},
            timeout=args.timeout,
        )
        _mark(results, "public.blocked.admin-reset", status == 404, f"http={status}")

    failures = [res for res in results if not res.ok]
    print(
        f"\nSmoke summary: {len(results) - len(failures)}/{len(results)} checks passed"
    )
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
