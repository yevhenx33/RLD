#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATUS_PATH = Path(os.getenv("STATUS_PATH", "/home/ubuntu/RLD/docker/dashboard/status.json"))
STATE_PATH = Path(os.getenv("ALERT_STATE_PATH", "/home/ubuntu/RLD/docker/dashboard/alerts-state.json"))

WEBHOOK_URL = os.getenv("ALERT_WEBHOOK_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("ALERT_TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("ALERT_TELEGRAM_CHAT_ID", "").strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect_issues(snapshot: dict) -> list[str]:
    issues: list[str] = []
    stacks = snapshot.get("stacks", {}) if isinstance(snapshot.get("stacks"), dict) else {}
    gates = stacks.get("gates", {}) if isinstance(stacks.get("gates"), dict) else {}

    if gates and not gates.get("production_ready", True):
        issues.append("gate.production_ready=false")

    for stack_key in ("protocol_rates", "simulation", "execution", "frontend_edge"):
        stack = stacks.get(stack_key, {})
        if isinstance(stack, dict):
            status = str(stack.get("status", "")).lower()
            if status in {"critical", "degraded"}:
                issues.append(f"stack.{stack_key}={status}")

    backups = snapshot.get("backups", {}) if isinstance(snapshot.get("backups"), dict) else {}
    backup_status = str(backups.get("status", "")).lower()
    if backup_status in {"failed", "partial"}:
        issues.append(f"backup.status={backup_status}")

    restore = snapshot.get("restore_checks", {}) if isinstance(snapshot.get("restore_checks"), dict) else {}
    restore_status = str(restore.get("status", "")).lower()
    if restore_status == "failed":
        issues.append("restore_check=failed")

    services = snapshot.get("services", {}) if isinstance(snapshot.get("services"), dict) else {}
    for svc in ("indexer", "envio_indexer", "anvil", "monitor_bot"):
        payload = services.get(svc, {})
        if isinstance(payload, dict) and payload.get("healthy") is False:
            issues.append(f"service.{svc}=unhealthy")
    return sorted(set(issues))


def fingerprint(issues: list[str]) -> str:
    joined = "\n".join(issues)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def post_webhook(message: str, issues: list[str], severity: str) -> None:
    if not WEBHOOK_URL:
        return
    payload = json.dumps(
        {
            "text": message,
            "severity": severity,
            "issues": issues,
            "timestamp": utc_now(),
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=5).read()


def send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    body = urllib.parse.urlencode(
        {"chat_id": TELEGRAM_CHAT_ID, "text": message, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    urllib.request.urlopen(req, timeout=5).read()


def notify(message: str, issues: list[str], severity: str) -> None:
    send_telegram(message)
    post_webhook(message, issues, severity)


def main() -> int:
    snapshot = load_json(STATUS_PATH, {})
    state = load_json(STATE_PATH, {"last_fingerprint": "", "open_issue": False})
    issues = collect_issues(snapshot)
    host = socket.gethostname()
    ts = snapshot.get("timestamp", utc_now())

    current_fingerprint = fingerprint(issues)
    previous_fingerprint = str(state.get("last_fingerprint", ""))
    had_open_issue = bool(state.get("open_issue", False))

    should_notify = False
    severity = "info"
    if issues and current_fingerprint != previous_fingerprint:
        should_notify = True
        severity = "critical"
        message = (
            f"[RLD ALERT] {host} at {ts}\n"
            f"Issues detected ({len(issues)}):\n- " + "\n- ".join(issues)
        )
    elif not issues and had_open_issue:
        should_notify = True
        severity = "resolved"
        message = f"[RLD RECOVERY] {host} at {ts}\nAll monitored gates are healthy again."
    else:
        message = "No alert changes."

    if should_notify:
        try:
            notify(message, issues, severity)
        except Exception as exc:  # pragma: no cover
            print(f"alert_send_failed: {exc}", file=sys.stderr)
            return 1
        print(message)
    else:
        print(message)

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": utc_now(),
                "last_fingerprint": current_fingerprint,
                "open_issue": bool(issues),
                "issue_count": len(issues),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.URLError as exc:  # pragma: no cover
        print(f"alert_network_error: {exc}", file=sys.stderr)
        raise SystemExit(1)
