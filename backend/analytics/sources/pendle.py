"""Ethereum-only Pendle PT/YT price source.

Polls Pendle's official /core API for mainnet PT/YT asset metadata, latest USD
prices, and historical OHLCV rows. The source is intentionally Ethereum-only:
chain_id is hard-coded to 1 and non-mainnet API rows are ignored.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import os
import time
from typing import Any, Optional
from urllib.parse import urlencode

import clickhouse_connect
import requests

from analytics.base import BaseSource, insert_rows_batched
from analytics.protocols import PENDLE_ETHEREUM_PT_YT_PRICES


log = logging.getLogger("indexer.pendle")

PENDLE_API_BASE = "https://api-v2.pendle.finance/core"
ETHEREUM_CHAIN_ID = 1
ASSET_TYPES = {"PT", "YT"}
DEFAULT_BACKFILL_START = "2023-01-01T00:00:00Z"


def _utc_now_naive() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _parse_datetime(value: Any) -> Optional[dt.datetime]:
    if value in (None, "", "None"):
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min)
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return dt.datetime.fromtimestamp(raw, tz=dt.timezone.utc).replace(tzinfo=None)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _parse_datetime(int(text))
        try:
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _normalize_address(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "-" in text and text.split("-", 1)[0].isdigit():
        text = text.split("-", 1)[1]
    if text and not text.startswith("0x") and len(text) == 40:
        text = f"0x{text}"
    return text


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "None"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = json.loads(str(asset.get("raw_metadata_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    market = raw.get("market")
    return market if isinstance(market, dict) else {}


def _market_implied_apy(asset: dict[str, Any]) -> Optional[float]:
    details = _market_metadata(asset).get("details")
    if not isinstance(details, dict):
        return None
    implied_apy = _safe_float(details.get("impliedApy"), -1.0)
    return implied_apy if implied_apy >= 0 else None


def _derive_yt_price_from_pt(asset: dict[str, Any], pt_price: float, now: dt.datetime) -> Optional[float]:
    if pt_price <= 0:
        return None
    expiry = asset.get("expiry")
    if not isinstance(expiry, dt.datetime):
        expiry = _parse_datetime(expiry)
    if expiry is None:
        return None
    seconds_to_expiry = (expiry - now).total_seconds()
    if seconds_to_expiry <= 0:
        return 0.0
    implied_apy = _market_implied_apy(asset)
    if implied_apy is None:
        return None
    year_fraction = seconds_to_expiry / (365 * 24 * 60 * 60)
    return max(0.0, pt_price * implied_apy * year_fraction)


def _time_frame_step_seconds(time_frame: str) -> int:
    if time_frame == "day":
        return 86_400
    if time_frame == "week":
        return 604_800
    return 3_600


def _history_window_seconds(time_frame: str) -> int:
    # Pendle caps OHLCV responses at 1440 points.
    return 1_440 * _time_frame_step_seconds(time_frame)


def _coerce_timestamp(value: Any) -> Optional[dt.datetime]:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return parsed
    return None


def parse_ohlcv_csv(
    csv_text: str,
    *,
    asset_address: str,
    asset_type: str,
    symbol: str,
    time_frame: str,
) -> list[list[Any]]:
    if not csv_text or not csv_text.strip():
        return []
    rows: list[list[Any]] = []
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    for item in reader:
        timestamp = _coerce_timestamp(item.get("time"))
        if timestamp is None:
            continue
        rows.append(
            [
                asset_address,
                ETHEREUM_CHAIN_ID,
                asset_type,
                symbol,
                time_frame,
                timestamp,
                _safe_float(item.get("open")),
                _safe_float(item.get("high")),
                _safe_float(item.get("low")),
                _safe_float(item.get("close")),
                _safe_float(item.get("volume")),
            ]
        )
    return rows


class PendleEthereumPtYtSource(BaseSource):
    name = PENDLE_ETHEREUM_PT_YT_PRICES
    raw_table = "pendle_eth_price_ohlcv"
    is_offchain = True
    chain_id = ETHEREUM_CHAIN_ID

    def __init__(self):
        super().__init__()
        self.base_url = os.getenv("PENDLE_API_BASE_URL", PENDLE_API_BASE).rstrip("/")
        self.timeout = int(os.getenv("PENDLE_HTTP_TIMEOUT_SEC", "120"))
        self.backfill_start = _parse_datetime(
            os.getenv("PENDLE_BACKFILL_START", DEFAULT_BACKFILL_START)
        ) or _parse_datetime(DEFAULT_BACKFILL_START)
        self.time_frame = self._normalize_time_frame(os.getenv("PENDLE_BACKFILL_TIME_FRAME", "hour"))
        self.max_backfill_calls = max(0, int(os.getenv("PENDLE_BACKFILL_MAX_CALLS_PER_CYCLE", "8")))
        self.market_page_limit = 100
        self.max_market_pages = 100

    @staticmethod
    def _normalize_time_frame(value: str) -> str:
        normalized = (value or "hour").strip().lower()
        return normalized if normalized in {"hour", "day", "week"} else "hour"

    def _ensure_tables(self, ch: clickhouse_connect.driver.Client) -> None:
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS pendle_eth_assets (
                asset_address String,
                chain_id UInt32,
                asset_type LowCardinality(String),
                symbol String,
                market_address String,
                expiry DateTime,
                active UInt8,
                matured UInt8,
                raw_metadata_json String,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY asset_address
            """
        )
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS pendle_eth_price_latest (
                asset_address String,
                chain_id UInt32,
                asset_type LowCardinality(String),
                symbol String,
                price_usd Float64,
                source_timestamp DateTime,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY asset_address
            """
        )
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS pendle_eth_price_ohlcv (
                asset_address String,
                chain_id UInt32,
                asset_type LowCardinality(String),
                symbol String,
                time_frame LowCardinality(String),
                timestamp DateTime,
                open Float64,
                high Float64,
                low Float64,
                close Float64,
                volume Float64,
                inserted_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(inserted_at)
            PARTITION BY toStartOfMonth(timestamp)
            ORDER BY (asset_address, time_frame, timestamp)
            """
        )
        ch.command(
            """
            CREATE TABLE IF NOT EXISTS pendle_eth_backfill_progress (
                asset_address String,
                chain_id UInt32,
                time_frame LowCardinality(String),
                cursor_timestamp DateTime,
                updated_at DateTime DEFAULT now()
            ) ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY (asset_address, time_frame)
            """
        )

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params, doseq=False)}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = requests.get(url, timeout=self.timeout)
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(min(30, 2 ** attempt))
                    continue
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
                time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"Pendle API request failed: {path}") from last_error

    @staticmethod
    def _markets_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
        for key in ("markets", "results", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def _chain_id_of(market: dict[str, Any]) -> int:
        return int(market.get("chainId") or market.get("chain_id") or 0)

    @staticmethod
    def _asset_from_market(market: dict[str, Any], asset_key: str) -> Optional[dict[str, Any]]:
        asset = market.get(asset_key.lower()) or market.get(asset_key.upper())
        if isinstance(asset, str):
            address = _normalize_address(asset)
            asset_payload: dict[str, Any] = {"id": asset, "address": address}
        elif isinstance(asset, dict):
            address = _normalize_address(asset.get("address") or asset.get("tokenAddress") or asset.get("id"))
            asset_payload = asset
        else:
            return None
        if not address:
            return None
        expiry = _parse_datetime(asset_payload.get("expiry") or market.get("expiry") or market.get("maturity"))
        now = _utc_now_naive()
        matured = bool(expiry and expiry <= now)
        market_name = str(market.get("name") or market.get("symbol") or "").strip()
        fallback_symbol = f"{asset_key.upper()}-{market_name}" if market_name else asset_key.upper()
        market_timestamp = _parse_datetime(market.get("timestamp"))
        return {
            "asset_address": address,
            "chain_id": ETHEREUM_CHAIN_ID,
            "asset_type": asset_key.upper(),
            "symbol": str(asset_payload.get("symbol") or asset_payload.get("name") or fallback_symbol),
            "market_address": _normalize_address(market.get("address") or market.get("marketAddress")),
            "expiry": expiry or dt.datetime(1970, 1, 1),
            "market_timestamp": market_timestamp,
            "active": 0 if matured else 1,
            "matured": 1 if matured else 0,
            "raw_metadata_json": json.dumps(
                {"market": market, "asset": asset_payload},
                separators=(",", ":"),
                default=str,
            ),
        }

    def _discover_assets(self) -> list[dict[str, Any]]:
        discovered: dict[str, dict[str, Any]] = {}
        skip = 0
        for _ in range(self.max_market_pages):
            payload = self._get_json(
                "/v2/markets/all",
                {"limit": self.market_page_limit, "skip": skip},
            )
            markets = self._markets_from_payload(payload)
            if not markets:
                break
            for market in markets:
                if self._chain_id_of(market) != ETHEREUM_CHAIN_ID:
                    continue
                for asset_key in ("PT", "YT"):
                    asset = self._asset_from_market(market, asset_key)
                    if asset and asset["asset_type"] in ASSET_TYPES:
                        discovered[asset["asset_address"]] = asset
            if len(markets) < self.market_page_limit:
                break
            skip += self.market_page_limit
        return list(discovered.values())

    def _upsert_assets(self, ch, assets: list[dict[str, Any]]) -> int:
        if not assets:
            return 0
        rows = [
            [
                a["asset_address"],
                ETHEREUM_CHAIN_ID,
                a["asset_type"],
                a["symbol"],
                a["market_address"],
                a["expiry"],
                int(a["active"]),
                int(a["matured"]),
                a["raw_metadata_json"],
            ]
            for a in assets
        ]
        return insert_rows_batched(
            ch,
            "pendle_eth_assets",
            rows,
            [
                "asset_address",
                "chain_id",
                "asset_type",
                "symbol",
                "market_address",
                "expiry",
                "active",
                "matured",
                "raw_metadata_json",
            ],
        )

    @staticmethod
    def _price_from_value(value: Any) -> Optional[float]:
        if isinstance(value, (int, float, str)):
            price = _safe_float(value, -1.0)
            return price if price >= 0 else None
        if not isinstance(value, dict):
            return None
        for key in ("price", "priceUsd", "usd", "value"):
            if key in value:
                price = _safe_float(value.get(key), -1.0)
                return price if price >= 0 else None
        return None

    def _price_map_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key in ("priceMap", "prices"):
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return {}

    def _sync_latest_prices(self, ch, assets: list[dict[str, Any]]) -> int:
        if not assets:
            return 0
        by_id = {f"{ETHEREUM_CHAIN_ID}-{asset['asset_address']}": asset for asset in assets}
        by_market: dict[str, dict[str, dict[str, Any]]] = {}
        for asset in assets:
            market_address = str(asset.get("market_address") or "")
            if market_address:
                by_market.setdefault(market_address, {})[str(asset.get("asset_type"))] = asset
        prices_by_address: dict[str, float] = {}
        rows: list[list[Any]] = []
        now = _utc_now_naive()
        ids = list(by_id.keys())
        for start in range(0, len(ids), 20):
            chunk = ids[start:start + 20]
            payload = self._get_json("/v1/prices/assets", {"ids": ",".join(chunk)})
            price_map = self._price_map_from_payload(payload)
            for asset_id, raw_value in price_map.items():
                normalized_id = asset_id.lower()
                asset = by_id.get(normalized_id)
                if asset is None:
                    asset = by_id.get(f"{ETHEREUM_CHAIN_ID}-{_normalize_address(asset_id)}")
                if asset is None:
                    continue
                price = self._price_from_value(raw_value)
                if price is None:
                    continue
                prices_by_address[asset["asset_address"]] = price
        derived_yt_prices = 0
        for asset in assets:
            price = prices_by_address.get(asset["asset_address"])
            if asset.get("asset_type") == "YT" and (
                price is None or (price <= 0 and int(asset.get("active") or 0) == 1)
            ):
                pair = by_market.get(str(asset.get("market_address") or ""), {})
                pt_asset = pair.get("PT")
                pt_price = prices_by_address.get(pt_asset["asset_address"]) if pt_asset else None
                if pt_price is not None:
                    derived_price = _derive_yt_price_from_pt(asset, pt_price, now)
                    if derived_price is not None:
                        price = derived_price
                        derived_yt_prices += 1
            if price is None:
                continue
            rows.append(
                [
                    asset["asset_address"],
                    ETHEREUM_CHAIN_ID,
                    asset["asset_type"],
                    asset["symbol"],
                    price,
                    now,
                ]
            )
        if derived_yt_prices:
            log.info("[%s] derived %d YT latest prices from paired PT prices", self.name, derived_yt_prices)
        if not rows:
            return 0
        return insert_rows_batched(
            ch,
            "pendle_eth_price_latest",
            rows,
            ["asset_address", "chain_id", "asset_type", "symbol", "price_usd", "source_timestamp"],
        )

    def _existing_history_cursor(self, ch, asset_address: str, time_frame: str) -> Optional[dt.datetime]:
        result = ch.command(
            "SELECT max(timestamp) FROM pendle_eth_price_ohlcv "
            f"WHERE asset_address = '{asset_address}' AND time_frame = '{time_frame}'"
        )
        cursor = _parse_datetime(result)
        if cursor is not None and cursor > dt.datetime(1971, 1, 1):
            return cursor
        result = ch.command(
            "SELECT cursor_timestamp FROM pendle_eth_backfill_progress FINAL "
            f"WHERE asset_address = '{asset_address}' AND time_frame = '{time_frame}' "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        cursor = _parse_datetime(result)
        if cursor is not None and cursor > dt.datetime(1971, 1, 1):
            return cursor
        return None

    def _initial_history_start(self, asset: dict[str, Any]) -> dt.datetime:
        market_timestamp = asset.get("market_timestamp")
        if isinstance(market_timestamp, dt.datetime) and market_timestamp > self.backfill_start:
            return market_timestamp.replace(minute=0, second=0, microsecond=0)
        return self.backfill_start

    def _update_history_progress(
        self,
        ch,
        asset_address: str,
        time_frame: str,
        cursor_timestamp: dt.datetime,
    ) -> None:
        ch.insert(
            "pendle_eth_backfill_progress",
            [[asset_address, ETHEREUM_CHAIN_ID, time_frame, cursor_timestamp]],
            column_names=["asset_address", "chain_id", "time_frame", "cursor_timestamp"],
        )

    def _history_end_for_asset(self, asset: dict[str, Any]) -> dt.datetime:
        now = _utc_now_naive()
        expiry = asset.get("expiry")
        if isinstance(expiry, dt.datetime) and expiry > dt.datetime(1971, 1, 1):
            return min(now, expiry + dt.timedelta(days=1))
        return now

    def _sync_historical_prices(self, ch, assets: list[dict[str, Any]]) -> int:
        if not assets or self.max_backfill_calls <= 0:
            return 0
        inserted = 0
        calls = 0
        step_seconds = _time_frame_step_seconds(self.time_frame)
        window_seconds = _history_window_seconds(self.time_frame)
        for asset in sorted(assets, key=lambda item: (item["active"] == 0, item["symbol"], item["asset_address"])):
            if calls >= self.max_backfill_calls:
                break
            cursor = self._existing_history_cursor(ch, asset["asset_address"], self.time_frame)
            start_ts = (
                cursor + dt.timedelta(seconds=step_seconds)
                if cursor
                else self._initial_history_start(asset)
            )
            end_cap = self._history_end_for_asset(asset)
            if start_ts is None or start_ts >= end_cap:
                continue
            end_ts = min(end_cap, start_ts + dt.timedelta(seconds=window_seconds - step_seconds))
            calls += 1
            try:
                payload = self._get_json(
                    f"/v4/{ETHEREUM_CHAIN_ID}/prices/{asset['asset_address']}/ohlcv",
                    {
                        "time_frame": self.time_frame,
                        "timestamp_start": start_ts.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                        "timestamp_end": end_ts.replace(tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    },
                )
            except Exception as exc:
                log.warning(
                    "[%s] skipping failed OHLCV window asset=%s frame=%s start=%s end=%s error=%s",
                    self.name,
                    asset["asset_address"],
                    self.time_frame,
                    start_ts,
                    end_ts,
                    exc,
                )
                continue
            rows = parse_ohlcv_csv(
                str(payload.get("results") or ""),
                asset_address=asset["asset_address"],
                asset_type=asset["asset_type"],
                symbol=asset["symbol"],
                time_frame=self.time_frame,
            )
            if not rows:
                self._update_history_progress(ch, asset["asset_address"], self.time_frame, end_ts)
                continue
            inserted += insert_rows_batched(
                ch,
                "pendle_eth_price_ohlcv",
                rows,
                [
                    "asset_address",
                    "chain_id",
                    "asset_type",
                    "symbol",
                    "time_frame",
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ],
            )
            self._update_history_progress(ch, asset["asset_address"], self.time_frame, rows[-1][5])
        return inserted

    async def poll_and_insert(self, ch: clickhouse_connect.driver.Client) -> int:
        self._ensure_tables(ch)
        assets = self._discover_assets()
        asset_rows = self._upsert_assets(ch, assets)
        latest_rows = self._sync_latest_prices(ch, assets)
        historical_rows = self._sync_historical_prices(ch, assets)
        log.info(
            "[%s] assets=%d latest=%d historical=%d",
            self.name,
            asset_rows,
            latest_rows,
            historical_rows,
        )
        return asset_rows + latest_rows + historical_rows

    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        return None

    def merge(self, ch: clickhouse_connect.driver.Client, items: list):
        return 0
