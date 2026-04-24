from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping


FALSE_VALUES = {"0", "false", "no", "off"}


def _log(logger: Any, level: str, message: str, *args: Any) -> None:
    if logger is None:
        return
    method = getattr(logger, level, None)
    if callable(method):
        method(message, *args)


def env_decimal(
    name: str,
    default: str,
    *,
    env: Mapping[str, str] | None = None,
    logger: Any = None,
) -> Decimal:
    env_map = env or os.environ
    raw = str(env_map.get(name, default)).strip()
    try:
        return Decimal(raw)
    except Exception:
        _log(logger, "warning", "Invalid %s=%r; defaulting to %s", name, raw, default)
        return Decimal(default)


def env_int(
    name: str,
    default: int,
    *,
    env: Mapping[str, str] | None = None,
    logger: Any = None,
) -> int:
    env_map = env or os.environ
    raw = str(env_map.get(name, str(default))).strip()
    try:
        return int(raw)
    except Exception:
        _log(logger, "warning", "Invalid %s=%r; defaulting to %d", name, raw, default)
        return default


def env_bool(
    name: str,
    default: bool,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    env_map = env or os.environ
    raw = str(env_map.get(name, "true" if default else "false")).strip().lower()
    return raw not in FALSE_VALUES


def parse_rate_timestamp(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(float(raw))
        except ValueError:
            pass
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def normalize_rate_fraction(raw_rate: Decimal) -> Decimal | None:
    if raw_rate < 0:
        return None
    if raw_rate > 1:
        return raw_rate / Decimal(100)
    return raw_rate


def normalize_api_base(url: str | None) -> str:
    if not url:
        return ""
    return str(url).strip().rstrip("/")


def _rates_api_port(env: Mapping[str, str] | None = None) -> str:
    env_map = env or os.environ
    return (
        str(env_map.get("RATES_API_PORT") or "").strip()
        or str(env_map.get("ENVIO_API_PORT") or "").strip()
        or str(env_map.get("ENVIO_PORT") or "").strip()
        or "5000"
    )


def candidate_rate_api_bases(
    preferred_api_base: str | None,
    *,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    env_map = env or os.environ
    local_port = _rates_api_port(env_map)
    configured = [
        preferred_api_base,
        env_map.get("RATES_API_BASE_URL", ""),
        env_map.get("ENVIO_API_URL", ""),
        env_map.get("API_URL", ""),
        env_map.get("RATES_API_URL", ""),
        f"http://localhost:{local_port}",
        "http://127.0.0.1:5000",
        "http://rld_graphql_api:5000",
    ]
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in configured:
        base = normalize_api_base(candidate)
        if not base or base in seen:
            continue
        seen.add(base)
        ordered.append(base)
    return ordered


@dataclass(frozen=True)
class RateValidationPolicy:
    min_rate: Decimal = Decimal("0.0")
    max_rate: Decimal = Decimal("2.0")
    max_age_seconds: int = 900
    require_timestamp: bool = True


@dataclass(frozen=True)
class RateSample:
    endpoint: str
    raw_rate: Decimal
    rate_fraction: Decimal
    timestamp: int | None
    age_seconds: int | None
    payload: dict[str, Any]


def policy_from_env(
    *,
    env: Mapping[str, str] | None = None,
    logger: Any = None,
) -> RateValidationPolicy:
    env_map = env or os.environ
    return RateValidationPolicy(
        min_rate=env_decimal("MIN_BORROW_APY", "0.0", env=env_map, logger=logger),
        max_rate=env_decimal("MAX_BORROW_APY", "2.0", env=env_map, logger=logger),
        max_age_seconds=env_int("MAX_RATE_AGE_SECONDS", 900, env=env_map, logger=logger),
        require_timestamp=env_bool("REQUIRE_RATE_TIMESTAMP", True, env=env_map),
    )


def fetch_valid_rate_sample(
    preferred_api_base: str | None,
    *,
    endpoint_path: str = "/api/v1/oracle/usdc-borrow-apy",
    timeout_seconds: float = 4.0,
    policy: RateValidationPolicy | None = None,
    env: Mapping[str, str] | None = None,
    logger: Any = None,
) -> RateSample | None:
    env_map = env or os.environ
    active_policy = policy or policy_from_env(env=env_map, logger=logger)
    path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"

    for base in candidate_rate_api_bases(preferred_api_base, env=env_map):
        endpoint = f"{base}{path}"
        try:
            req = urllib.request.Request(endpoint, method="GET")
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            _log(logger, "debug", "Rate fetch failed at %s: %s", endpoint, exc)
            continue

        if "borrow_apy" not in payload:
            _log(logger, "warning", "Rate payload missing borrow_apy from %s", endpoint)
            continue

        try:
            raw_rate = Decimal(str(payload["borrow_apy"]))
        except Exception:
            _log(
                logger,
                "warning",
                "Ignoring invalid APY payload %r from %s",
                payload.get("borrow_apy"),
                endpoint,
            )
            continue

        rate_fraction = normalize_rate_fraction(raw_rate)
        if rate_fraction is None:
            _log(logger, "warning", "Ignoring negative APY %s from %s", raw_rate, endpoint)
            continue

        if not (active_policy.min_rate <= rate_fraction <= active_policy.max_rate):
            _log(
                logger,
                "warning",
                "Ignoring out-of-range APY %s (normalized %s) from %s (expected %s..%s)",
                raw_rate,
                rate_fraction,
                endpoint,
                active_policy.min_rate,
                active_policy.max_rate,
            )
            continue

        timestamp = parse_rate_timestamp(
            payload.get("timestamp")
            or payload.get("updated_at")
            or payload.get("updatedAt")
            or payload.get("ts")
        )
        age_seconds: int | None = None
        if active_policy.max_age_seconds > 0:
            if timestamp is None:
                if active_policy.require_timestamp:
                    _log(logger, "warning", "Ignoring rate payload without timestamp from %s", endpoint)
                    continue
            else:
                age_seconds = max(0, int(time.time()) - int(timestamp))
                if age_seconds > active_policy.max_age_seconds:
                    _log(
                        logger,
                        "warning",
                        "Ignoring stale APY %s from %s (age=%ss > %ss)",
                        rate_fraction,
                        endpoint,
                        age_seconds,
                        active_policy.max_age_seconds,
                    )
                    continue

        return RateSample(
            endpoint=endpoint,
            raw_rate=raw_rate,
            rate_fraction=rate_fraction,
            timestamp=timestamp,
            age_seconds=age_seconds,
            payload=payload if isinstance(payload, dict) else {},
        )

    return None
