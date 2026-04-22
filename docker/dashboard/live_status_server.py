#!/usr/bin/env python3
"""
live_status_server.py

Real-time dashboard data service:
- GET /live-status           -> latest merged status snapshot (JSON)
- GET /live-status/stream    -> Server-Sent Events stream (1s cadence)
- GET /healthz               -> liveness signal for this service

The service uses docker/dashboard/status.json as a baseline and overlays
high-frequency probes every second so the dashboard reflects live behavior
without waiting for cron-driven status generation.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib import error, parse, request

LOG = logging.getLogger("dashboard.live")

DEFAULT_STATUS_PATH = Path("/home/ubuntu/RLD/docker/dashboard/status.json")
DEFAULT_INTERVAL_SEC = 1.0
DEFAULT_ALLOWED_ORIGIN = "https://rld.fi"


def _allowed_origin() -> str:
    value = os.getenv("DASHBOARD_CORS_ORIGIN", DEFAULT_ALLOWED_ORIGIN).strip()
    return value or DEFAULT_ALLOWED_ORIGIN


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_int(value: Any) -> Optional[int]:
    try:
        if value in (None, "", "None"):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _http_json(
    url: str,
    *,
    timeout: float = 0.9,
    method: str = "GET",
    body: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    start = time.monotonic()
    req = request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    status_code = 0
    raw_text = ""
    payload: Any = {}
    error_text = ""
    ok = False

    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status_code = resp.getcode()
            raw_text = resp.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        status_code = exc.code
        raw_text = exc.read().decode("utf-8", errors="ignore")
        error_text = f"http_{exc.code}"
    except Exception as exc:  # pragma: no cover - network failures are environment-dependent
        error_text = str(exc)
    else:
        ok = 200 <= status_code < 300

    if raw_text.strip():
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            payload = {}

    latency_ms = int((time.monotonic() - start) * 1000)
    return {
        "ok": ok,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "json": payload,
        "text": raw_text[:4000],
        "error": error_text,
    }


def _probe_reth(timeout: float = 0.9) -> Dict[str, Any]:
    payload = json.dumps(
        [
            {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
            {"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": ["latest", False], "id": 2},
            {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 3},
            {"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 4},
            {"jsonrpc": "2.0", "method": "txpool_status", "params": [], "id": 5},
        ]
    ).encode("utf-8")
    probe = _http_json(
        "http://localhost:8545",
        timeout=timeout,
        method="POST",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    out = {
        "ok": False,
        "latency_ms": probe["latency_ms"],
        "block": None,
        "block_ts": None,
        "chain_id": None,
        "gas_gwei": None,
        "txpool_pending": None,
        "txpool_queued": None,
    }
    rows = probe.get("json")
    if not isinstance(rows, list):
        return out

    by_id = {item.get("id"): item.get("result") for item in rows if isinstance(item, dict)}
    try:
        block_hex = by_id.get(1)
        if isinstance(block_hex, str):
            out["block"] = int(block_hex, 16)
        block_obj = by_id.get(2) if isinstance(by_id.get(2), dict) else {}
        if isinstance(block_obj.get("timestamp"), str):
            out["block_ts"] = int(block_obj["timestamp"], 16)
        chain_hex = by_id.get(3)
        if isinstance(chain_hex, str):
            out["chain_id"] = int(chain_hex, 16)
        gas_hex = by_id.get(4)
        if isinstance(gas_hex, str):
            out["gas_gwei"] = round(int(gas_hex, 16) / 1e9, 2)
        txpool = by_id.get(5) if isinstance(by_id.get(5), dict) else {}
        if isinstance(txpool.get("pending"), str):
            out["txpool_pending"] = int(txpool["pending"], 16)
        if isinstance(txpool.get("queued"), str):
            out["txpool_queued"] = int(txpool["queued"], 16)
        out["ok"] = out["block"] is not None
    except Exception:
        return out
    return out


def _probe_envio_latest_timestamp(timeout: float = 0.9) -> Dict[str, Any]:
    body = json.dumps({"query": "{ latestRates { timestamp } }"}).encode("utf-8")
    probe = _http_json(
        "http://localhost:5000/graphql",
        timeout=timeout,
        method="POST",
        body=body,
        headers={"Content-Type": "application/json"},
    )
    timestamp = None
    payload = probe.get("json", {})
    if isinstance(payload, dict):
        timestamp = (
            payload.get("data", {})
            .get("latestRates", {})
            .get("timestamp")
        )
    return {
        "ok": bool(probe.get("ok")),
        "latency_ms": probe.get("latency_ms", -1),
        "timestamp": timestamp,
    }


def _container_group(containers: Iterable[Dict[str, Any]], needles: Iterable[str]) -> Dict[str, Any]:
    needles_l = [n.lower() for n in needles]
    matched = [
        c for c in containers
        if any(n in str(c.get("name", "")).lower() for n in needles_l)
    ]
    running = [c for c in matched if str(c.get("status", "")).lower() in ("running", "healthy")]
    healthy = [c for c in matched if str(c.get("status", "")).lower() == "healthy"]
    return {
        "matched": len(matched),
        "running": len(running),
        "healthy": len(healthy),
        "names": [str(c.get("name", "")) for c in matched][:8],
    }


def _derive_stacks(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    services = snapshot.get("services", {}) if isinstance(snapshot.get("services"), dict) else {}
    databases = snapshot.get("databases", {}) if isinstance(snapshot.get("databases"), dict) else {}
    containers = snapshot.get("containers", []) if isinstance(snapshot.get("containers"), list) else []

    envio_service = services.get("envio_indexer", {}) if isinstance(services.get("envio_indexer"), dict) else {}
    envio_db = databases.get("envio_indexer", {}) if isinstance(databases.get("envio_indexer"), dict) else {}
    pool_state = databases.get("pool_state", {}) if isinstance(databases.get("pool_state"), dict) else {}

    legacy = _container_group(containers, ["rates-indexer"])
    frontend_container = _container_group(containers, ["frontend"])
    mm_container = _container_group(containers, ["mm-daemon"])
    chaos_container = _container_group(containers, ["chaos-trader"])
    faucet_container = _container_group(containers, ["faucet"])

    processing_lag = envio_db.get("processingLag", {}) if isinstance(envio_db.get("processingLag"), dict) else {}
    readyz = envio_db.get("readyz", {}) if isinstance(envio_db.get("readyz"), dict) else {}
    lag_threshold = _to_int(readyz.get("maxLagBlocks"))
    if lag_threshold is None:
        lag_threshold = 250_000
    failing_protocols = readyz.get("failingProtocols", [])
    if not isinstance(failing_protocols, list):
        failing_protocols = []

    protocol_components = {
        "graphql_api": bool(envio_service.get("healthy")),
        "clickhouse": str(envio_db.get("clickhouse", "")).lower() == "ok",
        "readiness": bool(envio_service.get("ready")),
    }
    if all(protocol_components.values()):
        protocol_status = "healthy"
    elif protocol_components["graphql_api"] and protocol_components["clickhouse"]:
        protocol_status = "degraded"
    else:
        protocol_status = "critical"

    chain_block = _to_int((services.get("anvil", {}) if isinstance(services.get("anvil"), dict) else {}).get("block"))
    indexer_block = _to_int(pool_state.get("last_indexed_block"))
    block_gap = (chain_block - indexer_block) if (chain_block is not None and indexer_block is not None) else None
    simulation_components = {
        "sim_indexer_api": bool((services.get("indexer", {}) if isinstance(services.get("indexer"), dict) else {}).get("healthy")),
        "postgres_state": bool(pool_state.get("healthy")),
        "reth_rpc": bool((services.get("anvil", {}) if isinstance(services.get("anvil"), dict) else {}).get("healthy")),
    }
    if all(simulation_components.values()) and (block_gap is None or block_gap <= 200):
        simulation_status = "healthy"
    elif all(simulation_components.values()):
        simulation_status = "degraded"
    else:
        simulation_status = "critical"

    execution_components = {
        "monitor_bot_api": bool((services.get("monitor_bot", {}) if isinstance(services.get("monitor_bot"), dict) else {}).get("healthy")),
        "mm_daemon": mm_container["running"] > 0,
        "chaos_trader": chaos_container["running"] > 0,
        "faucet": faucet_container["running"] > 0,
    }
    if all(execution_components.values()):
        execution_status = "healthy"
    elif execution_components["monitor_bot_api"] and (execution_components["mm_daemon"] or execution_components["chaos_trader"]):
        execution_status = "degraded"
    else:
        execution_status = "critical"

    frontend_components = {
        "edge_nginx": bool((services.get("nginx", {}) if isinstance(services.get("nginx"), dict) else {}).get("healthy")),
        "frontend_container": frontend_container["running"] > 0,
    }
    frontend_status = "healthy" if all(frontend_components.values()) else "degraded"

    legacy_running = legacy["running"] > 0
    legacy_status = "deprecated-running" if legacy_running else "removed"

    gates = {
        "protocol_rates_ready": protocol_status == "healthy",
        "simulation_ready": simulation_status == "healthy",
        "execution_ready": execution_status != "critical",
        "frontend_ready": frontend_status == "healthy",
        "legacy_removed": not legacy_running,
    }
    gates["production_ready"] = all(gates.values())

    return {
        "protocol_rates": {
            "label": "Protocol Rates",
            "status": protocol_status,
            "components": protocol_components,
            "latestTimestamp": envio_service.get("last_block"),
            "processingLag": processing_lag,
            "lagThreshold": lag_threshold,
            "failingProtocols": failing_protocols,
            "readyReason": envio_service.get("ready_reason") or envio_db.get("readyReason") or "",
        },
        "simulation": {
            "label": "Simulation",
            "status": simulation_status,
            "components": simulation_components,
            "chainBlock": chain_block,
            "indexerBlock": indexer_block,
            "blockGap": block_gap,
        },
        "execution": {
            "label": "Execution/Bots",
            "status": execution_status,
            "components": execution_components,
            "containers": {
                "mmDaemon": mm_container,
                "chaosTrader": chaos_container,
                "faucet": faucet_container,
            },
        },
        "frontend_edge": {
            "label": "Frontend/Edge",
            "status": frontend_status,
            "components": frontend_components,
            "containers": frontend_container,
        },
        "legacy_rates": {
            "label": "Legacy Rates Indexer",
            "status": legacy_status,
            "running": legacy_running,
            "containers": legacy,
        },
        "gates": gates,
    }


class LiveStatusCache:
    def __init__(self, status_path: Path, interval_sec: float):
        self.status_path = status_path
        self.interval_sec = max(0.5, float(interval_sec))

        self._baseline: Dict[str, Any] = {}
        self._baseline_mtime = 0.0

        self._snapshot: Dict[str, Any] = {"timestamp": utc_now_iso()}
        self._snapshot_bytes = json.dumps(self._snapshot).encode("utf-8")
        self._seq = 0

        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="dash-live")

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, name="live-status-loop", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def get_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def get_snapshot_bytes(self) -> Tuple[int, bytes]:
        with self._lock:
            return self._seq, self._snapshot_bytes

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                snapshot = self._build_snapshot()
                encoded = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
                with self._lock:
                    self._snapshot = snapshot
                    self._snapshot_bytes = encoded
                    self._seq += 1
            except Exception as exc:  # pragma: no cover - protects long-running loop
                if self._stop.is_set():
                    break
                LOG.exception("live snapshot update failed: %s", exc)
            elapsed = time.monotonic() - started
            sleep_for = max(0.0, self.interval_sec - elapsed)
            self._stop.wait(sleep_for)

    def _read_baseline(self) -> Dict[str, Any]:
        if not self.status_path.exists():
            return copy.deepcopy(self._baseline)

        try:
            stat = self.status_path.stat()
            if stat.st_mtime > self._baseline_mtime:
                with self.status_path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self._baseline = loaded
                    self._baseline_mtime = stat.st_mtime
        except Exception as exc:
            LOG.warning("failed reading baseline status file: %s", exc)
        return copy.deepcopy(self._baseline)

    def _build_snapshot(self) -> Dict[str, Any]:
        snapshot = self._read_baseline()
        if not isinstance(snapshot, dict):
            snapshot = {}
        snapshot.setdefault("services", {})
        snapshot.setdefault("databases", {})
        snapshot.setdefault("containers", [])

        try:
            future_to_key = {
                self._executor.submit(_http_json, "http://localhost:5000/healthz", timeout=0.8): "envio_health",
                self._executor.submit(_http_json, "http://localhost:5000/readyz", timeout=0.8): "envio_ready",
                self._executor.submit(_probe_envio_latest_timestamp, timeout=0.8): "envio_latest",
                self._executor.submit(_http_json, "http://localhost:8080/api/status", timeout=0.8): "sim_status",
                self._executor.submit(_http_json, "http://localhost:8083/", timeout=0.8): "monitor",
                self._executor.submit(_probe_reth, timeout=0.8): "reth",
            }
        except RuntimeError:
            # Happens during shutdown when executor is already tearing down.
            return snapshot
        done, not_done = wait(future_to_key.keys(), timeout=0.95)
        for future in not_done:
            future.cancel()

        results: Dict[str, Any] = {}
        for future, key in future_to_key.items():
            if future in done:
                try:
                    results[key] = future.result()
                except Exception as exc:
                    results[key] = {"ok": False, "error": str(exc)}
            else:
                results[key] = {"ok": False, "error": "timeout"}

        services = snapshot["services"] if isinstance(snapshot["services"], dict) else {}
        databases = snapshot["databases"] if isinstance(snapshot["databases"], dict) else {}

        # Envio probes
        envio_health = results.get("envio_health", {})
        envio_ready = results.get("envio_ready", {})
        envio_latest = results.get("envio_latest", {})
        envio_health_payload = envio_health.get("json", {}) if isinstance(envio_health.get("json"), dict) else {}
        envio_ready_payload = envio_ready.get("json", {}) if isinstance(envio_ready.get("json"), dict) else {}

        ready_http = _to_int(envio_ready.get("status_code")) or 0
        ready_reason = str(envio_ready_payload.get("reason", "") or "")
        processing_lag = envio_ready_payload.get("processingLag", {})
        if not isinstance(processing_lag, dict):
            processing_lag = {}
        failing = envio_ready_payload.get("failingProtocols", [])
        if not isinstance(failing, list):
            failing = []
        max_lag = -1
        for value in processing_lag.values():
            parsed = _to_int(value)
            if parsed is not None:
                max_lag = max(max_lag, parsed)

        envio_ok = bool(envio_health.get("ok")) and str(envio_health_payload.get("status", "")).lower() == "ok"
        envio_ready_ok = bool(ready_http == 200 and str(envio_ready_payload.get("status", "")).lower() in ("ready", "ok"))
        envio_service = services.get("envio_indexer", {}) if isinstance(services.get("envio_indexer"), dict) else {}
        envio_service.update(
            {
                "healthy": envio_ok,
                "response_ms": int(envio_health.get("latency_ms", -1)),
                "ready": envio_ready_ok,
                "ready_http": ready_http,
                "ready_reason": ready_reason,
                "ready_max_lag": max_lag,
                "failing_protocols": ",".join(str(x) for x in failing),
            }
        )
        latest_ts = envio_latest.get("timestamp")
        if latest_ts:
            envio_service["last_block"] = str(latest_ts)
        services["envio_indexer"] = envio_service

        envio_db = databases.get("envio_indexer", {}) if isinstance(databases.get("envio_indexer"), dict) else {}
        if isinstance(envio_health_payload, dict) and envio_health_payload:
            envio_db.update(envio_health_payload)
        envio_db["ready"] = envio_ready_ok
        envio_db["readyHttp"] = ready_http
        envio_db["readyReason"] = ready_reason
        envio_db["processingLag"] = processing_lag
        if isinstance(envio_ready_payload, dict) and envio_ready_payload:
            envio_db["readyz"] = envio_ready_payload
        databases["envio_indexer"] = envio_db

        # Simulation indexer status
        sim_status = results.get("sim_status", {})
        sim_payload = sim_status.get("json", {}) if isinstance(sim_status.get("json"), dict) else {}
        sim_ok = bool(sim_status.get("ok")) and str(sim_payload.get("status", "")).lower() == "ok"
        sim_service = services.get("indexer", {}) if isinstance(services.get("indexer"), dict) else {}
        sim_service["healthy"] = sim_ok
        sim_service["response_ms"] = int(sim_status.get("latency_ms", -1))
        services["indexer"] = sim_service

        pool_state = databases.get("pool_state", {}) if isinstance(databases.get("pool_state"), dict) else {}
        if sim_ok:
            pool_state["healthy"] = True
            if sim_payload.get("last_indexed_block") is not None:
                pool_state["last_indexed_block"] = sim_payload.get("last_indexed_block")
            if sim_payload.get("total_events") is not None:
                pool_state["total_events"] = sim_payload.get("total_events")
            if sim_payload.get("total_block_states") is not None:
                pool_state["block_states_rows"] = sim_payload.get("total_block_states")
            if sim_payload.get("mark_price") is not None:
                pool_state["mark_price"] = sim_payload.get("mark_price")
            if sim_payload.get("index_price") is not None:
                pool_state["index_price"] = sim_payload.get("index_price")
            pool_state["price_source"] = "indexer_api"
        elif pool_state:
            pool_state["healthy"] = False
        databases["pool_state"] = pool_state

        # Monitor bot probe
        monitor = results.get("monitor", {})
        monitor_service = services.get("monitor_bot", {}) if isinstance(services.get("monitor_bot"), dict) else {}
        monitor_service["healthy"] = bool(monitor.get("ok"))
        monitor_service["response_ms"] = int(monitor.get("latency_ms", -1))
        services["monitor_bot"] = monitor_service

        # Reth probe (update both `reth` and `anvil` aliases)
        reth = results.get("reth", {})
        for alias in ("anvil", "reth"):
            reth_service = services.get(alias, {}) if isinstance(services.get(alias), dict) else {}
            reth_service["healthy"] = bool(reth.get("ok"))
            if reth.get("block") is not None:
                reth_service["block"] = str(reth.get("block"))
            if reth.get("block_ts") is not None:
                reth_service["block_ts"] = int(reth.get("block_ts"))
            if reth.get("chain_id") is not None:
                reth_service["chain_id"] = str(reth.get("chain_id"))
            if reth.get("gas_gwei") is not None:
                reth_service["gas_gwei"] = str(reth.get("gas_gwei"))
            if reth.get("txpool_pending") is not None:
                reth_service["txpool_pending"] = int(reth.get("txpool_pending"))
            if reth.get("txpool_queued") is not None:
                reth_service["txpool_queued"] = int(reth.get("txpool_queued"))
            services[alias] = reth_service

        snapshot["services"] = services
        snapshot["databases"] = databases
        snapshot["stacks"] = _derive_stacks(snapshot)
        snapshot["timestamp"] = utc_now_iso()
        snapshot["live"] = {
            "interval_sec": self.interval_sec,
            "updated_at": snapshot["timestamp"],
        }
        return snapshot


CACHE: Optional[LiveStatusCache] = None


class DashboardLiveHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:  # noqa: N802
        if CACHE is None:
            self._send_json({"status": "starting"}, status=503)
            return

        parsed = parse.urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            self._send_json({"status": "ok", "updated_at": CACHE.get_snapshot().get("timestamp")})
            return
        if path == "/live-status":
            _, payload = CACHE.get_snapshot_bytes()
            self._send_json_bytes(payload)
            return
        if path == "/live-status/stream":
            self._stream_sse()
            return
        self._send_json({"status": "not_found", "path": path}, status=404)

    def _send_json(self, data: Dict[str, Any], status: int = 200) -> None:
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self._send_json_bytes(payload, status=status)

    def _send_json_bytes(self, payload: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Access-Control-Allow-Origin", _allowed_origin())
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _stream_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", _allowed_origin())
        self.end_headers()

        try:
            while True:
                seq, payload = CACHE.get_snapshot_bytes() if CACHE is not None else (0, b"{}")
                self.wfile.write(f"id: {seq}\n".encode("utf-8"))
                self.wfile.write(b"event: status\n")
                self.wfile.write(b"data: ")
                self.wfile.write(payload)
                self.wfile.write(b"\n\n")
                self.wfile.flush()
                time.sleep(CACHE.interval_sec if CACHE is not None else 1.0)
        except (BrokenPipeError, ConnectionResetError):
            return


class DashboardLiveHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live dashboard status API.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8091, help="Bind port (default: 8091)")
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=DEFAULT_INTERVAL_SEC,
        help="Live snapshot interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--status-path",
        default=str(DEFAULT_STATUS_PATH),
        help="Path to baseline status.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    status_path = Path(args.status_path)
    global CACHE
    CACHE = LiveStatusCache(status_path=status_path, interval_sec=args.interval_sec)
    CACHE.start()

    server = DashboardLiveHTTPServer((args.host, args.port), DashboardLiveHandler)
    LOG.info("dashboard live status API listening on http://%s:%d", args.host, args.port)
    LOG.info("baseline status file: %s", status_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("received interrupt, shutting down")
    finally:
        server.server_close()
        CACHE.stop()


if __name__ == "__main__":
    main()
