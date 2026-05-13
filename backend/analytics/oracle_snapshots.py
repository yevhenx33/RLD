"""Shared on-chain oracle snapshot support for analytics sources.

The table is intentionally generic: Morpho and Fluid can both store historical
non-event oracle/share-rate values with the exact contract/method provenance
needed to prove how a USD row was priced.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

from analytics.tokens import TOKENS


@dataclass(frozen=True)
class OracleSnapshot:
    chain_id: int
    source: str
    oracle_type: str
    subject: str
    oracle: str
    method: str
    block_number: int
    timestamp: dt.datetime
    value_raw: str
    value_scale: str
    price_usd: float
    status: str
    error: str = ""


def normalize_address(value: str | None) -> str:
    if not value:
        return ""
    value = str(value).lower()
    return value if value.startswith("0x") else "0x" + value


def ensure_oracle_snapshot_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS oracle_snapshots (
            chain_id UInt32,
            source LowCardinality(String),
            oracle_type LowCardinality(String),
            subject String,
            oracle String,
            method String,
            block_number UInt64,
            timestamp DateTime,
            value_raw String,
            value_scale String,
            price_usd Float64,
            status LowCardinality(String),
            error String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (chain_id, source, subject, oracle_type, timestamp, block_number)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )


def _token_symbol(address: str) -> str:
    return TOKENS.get(normalize_address(address).removeprefix("0x"), ("", 18))[0]


ASSET_PRICE_FEED_MAP: tuple[tuple[str, str, str, str], ...] = (
    ("AAVE / USD", "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9", "AAVE", "DIRECT"),
    ("BTC / USD", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "BTC", "DIRECT"),
    ("cbBTC / USD", "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf", "cbBTC", "DIRECT"),
    ("CRVUSD / USD", "0xf939e0a03fb07f59a73314e73794be0e57ac1b4e", "crvUSD", "DIRECT"),
    ("DAI / USD", "0x6b175474e89094c44da98b954eedeac495271d0f", "DAI", "DIRECT"),
    ("EIGEN / USD", "0xec53bf9167f50cdeb3ae105f56099aaab9061f83", "EIGEN", "DIRECT"),
    ("ETH / USD", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", "WETH", "DIRECT"),
    ("EUR / USD", "0x5f7827fdeb7c20b443265fc2f40845b715385ff2", "EURCV", "FIAT_PROXY"),
    ("frxUSD / USD", "0xcacd6fd266af91b8aed52accc382b4e165586e29", "frxUSD", "DIRECT"),
    ("LINK / USD", "0x514910771af9ca656af840dff83e8264ecf986ca", "LINK", "DIRECT"),
    ("MKR / USD", "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2", "MKR", "DIRECT"),
    ("PAXG / USD", "0x45804880de22913dafe09f4980848ece6ecbaf78", "PAXG", "DIRECT"),
    ("PYUSD / USD", "0x6c3ea9036406852006290770bedfcaba0e23a0e8", "PYUSD", "DIRECT"),
    ("RLUSD / USD", "0x8292bb45bf1ee4d140127049757c2e0ff06317ed", "RLUSD", "DIRECT"),
    ("SKY / USD", "0x56072c95faa701256059aa122697b133aded9279", "SKY", "DIRECT"),
    ("STETH / USD", "0xae7ab96520de3a18e5e111b5eaab095312d7fe84", "stETH", "DIRECT"),
    ("TBTC / USD", "0x18084fba666a33d37592fa2633fd49a74dd93a88", "tBTC", "DIRECT"),
    ("UNI / USD", "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", "UNI", "DIRECT"),
    ("USD0 / USD", "0x73a15fed60bf67631dc6cd7bc5b6e8da8190acf5", "USD0", "DIRECT"),
    ("USD0++ / USD", "0xb40b6608b2743e691c9b54ddbdee7bf03cd79f1c", "USD0pp", "DIRECT"),
    ("USDC / USD", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "USDC", "DIRECT"),
    ("USDe / USD", "0x4c9edd5852cd905f086c759e8383e09bff1e68b3", "USDe", "DIRECT"),
    ("USDS / USD", "0xdc035d45d973e3ec169d2276ddab16f1e407384f", "USDS", "DIRECT"),
    ("USDT / USD", "0xdac17f958d2ee523a2206206994597c13d831ec7", "USDT", "DIRECT"),
    ("XAU / USD", "0x68749665ff8d2d112fa859aa293f07a622782f38", "XAUt", "DIRECT"),
)


def ensure_asset_price_tables(ch) -> None:
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS asset_price_observations (
            chain_id UInt32,
            asset_address String,
            symbol LowCardinality(String),
            quote_symbol LowCardinality(String),
            price_usd Float64,
            price_quote Float64,
            source LowCardinality(String),
            source_type LowCardinality(String),
            source_id String,
            method String,
            block_number UInt64,
            timestamp DateTime,
            status LowCardinality(String),
            confidence LowCardinality(String),
            freshness_seconds UInt64 DEFAULT 0,
            value_raw String,
            value_scale String,
            provenance_json String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        PARTITION BY toStartOfMonth(timestamp)
        ORDER BY (chain_id, asset_address, source, source_type, source_id, timestamp, block_number)
        TTL timestamp + INTERVAL 72 MONTH DELETE
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS asset_price_latest (
            chain_id UInt32,
            asset_address String,
            symbol LowCardinality(String),
            quote_symbol LowCardinality(String),
            price_usd Float64,
            price_quote Float64,
            source LowCardinality(String),
            source_type LowCardinality(String),
            source_id String,
            method String,
            block_number UInt64,
            timestamp DateTime,
            status LowCardinality(String),
            confidence LowCardinality(String),
            freshness_seconds UInt64 DEFAULT 0,
            value_raw String,
            value_scale String,
            provenance_json String,
            inserted_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(inserted_at)
        ORDER BY (chain_id, asset_address, source, source_type, source_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS oracle_dependency_edges (
            chain_id UInt32,
            protocol LowCardinality(String),
            entity_type LowCardinality(String),
            entity_id String,
            role LowCardinality(String),
            asset_address String,
            symbol LowCardinality(String),
            source LowCardinality(String),
            source_type LowCardinality(String),
            source_id String,
            method String,
            dependency_path String,
            required UInt8,
            risk_tier LowCardinality(String),
            first_seen_block UInt64,
            last_seen_block UInt64,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (chain_id, protocol, entity_type, entity_id, role, asset_address, source, source_type, source_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS oracle_dependency_exposure_latest (
            chain_id UInt32,
            protocol LowCardinality(String),
            entity_type LowCardinality(String),
            entity_id String,
            role LowCardinality(String),
            asset_address String,
            symbol LowCardinality(String),
            source LowCardinality(String),
            source_type LowCardinality(String),
            source_id String,
            method String,
            exposure_kind LowCardinality(String),
            exposure_usd Float64,
            timestamp DateTime,
            block_number UInt64,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (chain_id, protocol, entity_type, entity_id, role, exposure_kind, source, source_type, source_id)
        """
    )


def _feed_map_sql() -> str:
    rows = []
    for feed, asset, symbol, confidence in ASSET_PRICE_FEED_MAP:
        rows.append(
            "tuple('{feed}', '{asset}', '{symbol}', '{confidence}')".format(
                feed=feed.replace("'", "''"),
                asset=normalize_address(asset),
                symbol=symbol.replace("'", "''"),
                confidence=confidence,
            )
        )
    return ", ".join(rows)


def _asset_map_sql() -> str:
    rows = []
    seen: set[str] = set()
    for address, (symbol, _decimals) in TOKENS.items():
        asset = normalize_address(address)
        if asset in seen:
            continue
        seen.add(asset)
        rows.append(
            "tuple('{asset}', '{symbol}')".format(
                asset=asset,
                symbol=str(symbol).replace("'", "''"),
            )
        )
    return ", ".join(rows)


def sync_asset_price_observations(ch) -> dict[str, int]:
    ensure_asset_price_tables(ch)
    feed_map = _feed_map_sql()
    asset_map = _asset_map_sql()
    chainlink_before = int(ch.command("SELECT count() FROM asset_price_observations WHERE source = 'CHAINLINK'") or 0)
    snapshot_before = int(ch.command("SELECT count() FROM asset_price_observations WHERE source != 'CHAINLINK'") or 0)
    ch.command(
        f"""
        INSERT INTO asset_price_observations (
            chain_id, asset_address, symbol, quote_symbol, price_usd, price_quote,
            source, source_type, source_id, method, block_number, timestamp, status,
            confidence, freshness_seconds, value_raw, value_scale, provenance_json
        )
        SELECT
            1 AS chain_id,
            m.asset_address,
            m.symbol,
            'USD' AS quote_symbol,
            c.price AS price_usd,
            c.price AS price_quote,
            'CHAINLINK' AS source,
            'PRICE_FEED' AS source_type,
            c.feed AS source_id,
            'AnswerUpdated' AS method,
            c.block_number,
            c.timestamp,
            'OK' AS status,
            m.confidence,
            0 AS freshness_seconds,
            toString(c.price) AS value_raw,
            '1' AS value_scale,
            concat('{{"feed":"', replaceAll(c.feed, '"', '\\\\"'), '"}}') AS provenance_json
        FROM chainlink_prices AS c
        INNER JOIN (
            SELECT
                tupleElement(item, 1) AS feed,
                tupleElement(item, 2) AS asset_address,
                tupleElement(item, 3) AS symbol,
                tupleElement(item, 4) AS confidence
            FROM (SELECT arrayJoin([{feed_map}]) AS item)
        ) AS m USING feed
        WHERE c.price > 0
        """
    )
    ch.command(
        f"""
        INSERT INTO asset_price_observations (
            chain_id, asset_address, symbol, quote_symbol, price_usd, price_quote,
            source, source_type, source_id, method, block_number, timestamp, status,
            confidence, freshness_seconds, value_raw, value_scale, provenance_json
        )
        SELECT
            s.chain_id,
            lower(s.subject) AS asset_address,
            if(m.symbol = '', s.subject, m.symbol) AS symbol,
            'USD' AS quote_symbol,
            s.price_usd,
            s.price_usd AS price_quote,
            s.source,
            s.oracle_type AS source_type,
            s.oracle AS source_id,
            s.method,
            s.block_number,
            s.timestamp,
            s.status,
            if(s.oracle_type = 'CHAINLINK_PROXY', 'DIRECT', 'SNAPSHOT') AS confidence,
            0 AS freshness_seconds,
            s.value_raw,
            s.value_scale,
            concat(
                '{{"source":"', replaceAll(s.source, '"', '\\\\"'),
                '","oracleType":"', replaceAll(s.oracle_type, '"', '\\\\"'),
                '","method":"', replaceAll(s.method, '"', '\\\\"'),
                '"}}'
            ) AS provenance_json
        FROM oracle_snapshots AS s
        LEFT JOIN (
            SELECT tupleElement(item, 1) AS asset_address, tupleElement(item, 2) AS symbol
            FROM (SELECT arrayJoin([{asset_map}]) AS item)
        ) AS m ON lower(s.subject) = m.asset_address
        WHERE s.status = 'OK'
          AND s.price_usd > 0
        """
    )
    refresh_asset_price_latest(ch)
    chainlink_after = int(ch.command("SELECT count() FROM asset_price_observations WHERE source = 'CHAINLINK'") or 0)
    snapshot_after = int(ch.command("SELECT count() FROM asset_price_observations WHERE source != 'CHAINLINK'") or 0)
    latest_rows = int(ch.command("SELECT count() FROM asset_price_latest") or 0)
    return {
        "chainlink_rows_inserted": max(0, chainlink_after - chainlink_before),
        "snapshot_rows_inserted": max(0, snapshot_after - snapshot_before),
        "latest_rows": latest_rows,
    }


def refresh_asset_price_latest(ch) -> int:
    ensure_asset_price_tables(ch)
    ch.command("TRUNCATE TABLE asset_price_latest")
    ch.command(
        """
        INSERT INTO asset_price_latest (
            chain_id, asset_address, symbol, quote_symbol, price_usd, price_quote,
            source, source_type, source_id, method, block_number, timestamp, status,
            confidence, freshness_seconds, value_raw, value_scale, provenance_json
        )
        SELECT
            p.chain_id,
            p.asset_address,
            argMax(p.symbol, (p.timestamp, p.block_number, p.inserted_at)) AS symbol,
            argMax(p.quote_symbol, (p.timestamp, p.block_number, p.inserted_at)) AS quote_symbol,
            argMax(p.price_usd, (p.timestamp, p.block_number, p.inserted_at)) AS price_usd,
            argMax(p.price_quote, (p.timestamp, p.block_number, p.inserted_at)) AS price_quote,
            p.source,
            p.source_type,
            p.source_id,
            argMax(p.method, (p.timestamp, p.block_number, p.inserted_at)) AS method,
            argMax(p.block_number, (p.timestamp, p.block_number, p.inserted_at)) AS block_number,
            max(p.timestamp) AS timestamp,
            argMax(p.status, (p.timestamp, p.block_number, p.inserted_at)) AS status,
            argMax(p.confidence, (p.timestamp, p.block_number, p.inserted_at)) AS confidence,
            argMax(p.freshness_seconds, (p.timestamp, p.block_number, p.inserted_at)) AS freshness_seconds,
            argMax(p.value_raw, (p.timestamp, p.block_number, p.inserted_at)) AS value_raw,
            argMax(p.value_scale, (p.timestamp, p.block_number, p.inserted_at)) AS value_scale,
            argMax(p.provenance_json, (p.timestamp, p.block_number, p.inserted_at)) AS provenance_json
        FROM (
            SELECT *
            FROM asset_price_observations
            WHERE status = 'OK' AND price_usd > 0
        ) AS p
        GROUP BY p.chain_id, p.asset_address, p.source, p.source_type, p.source_id
        """
    )
    return int(ch.command("SELECT count() FROM asset_price_latest") or 0)


def dependency_json(source: str, source_type: str, source_id: str, method: str) -> str:
    return json.dumps(
        {"source": source, "sourceType": source_type, "sourceId": source_id, "method": method},
        sort_keys=True,
    )


def insert_oracle_snapshots(ch, snapshots: list[OracleSnapshot]) -> int:
    if not snapshots:
        return 0
    rows = [
        [
            s.chain_id,
            s.source,
            s.oracle_type,
            normalize_address(s.subject),
            normalize_address(s.oracle),
            s.method,
            s.block_number,
            s.timestamp,
            s.value_raw,
            s.value_scale,
            s.price_usd,
            s.status,
            s.error,
        ]
        for s in snapshots
    ]
    ch.insert(
        "oracle_snapshots",
        rows,
        column_names=[
            "chain_id",
            "source",
            "oracle_type",
            "subject",
            "oracle",
            "method",
            "block_number",
            "timestamp",
            "value_raw",
            "value_scale",
            "price_usd",
            "status",
            "error",
        ],
    )
    return len(rows)
