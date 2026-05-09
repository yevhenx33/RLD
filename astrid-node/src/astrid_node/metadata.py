"""Local Astrid metadata schema."""

from __future__ import annotations


DATABASES = ("astrid_meta", "astrid_aave", "astrid_aave_raw", "astrid_chainlink", "astrid_user")


def ensure_metadata(ch) -> None:
    for database in DATABASES:
        ch.command(f"CREATE DATABASE IF NOT EXISTS {database}")
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.stream_registry (
            stream_id String,
            subject String,
            schema_version String,
            schema_hash String,
            manifest_json String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY stream_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.installed_streams (
            stream_id String,
            local_table String,
            installed_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(installed_at)
        ORDER BY stream_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.stream_cursors (
            stream_id String,
            last_cursor String,
            last_block UInt64 DEFAULT 0,
            last_timestamp DateTime DEFAULT toDateTime(0),
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY stream_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.chunk_manifest (
            stream_id String,
            chunk_id String,
            uri String,
            sha256 String,
            row_count UInt64 DEFAULT 0,
            installed_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(installed_at)
        ORDER BY (stream_id, chunk_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.chunk_sync_state (
            stream_id String,
            chunk_id String,
            status LowCardinality(String),
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY (stream_id, chunk_id)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.consumer_bindings (
            stream_id String,
            durable String,
            subject String,
            updated_at DateTime DEFAULT now()
        ) ENGINE = ReplacingMergeTree(updated_at)
        ORDER BY stream_id
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.processor_runs (
            processor String,
            status LowCardinality(String),
            started_at DateTime,
            finished_at DateTime,
            error String DEFAULT ''
        ) ENGINE = MergeTree()
        ORDER BY (processor, started_at)
        """
    )
    ch.command(
        """
        CREATE TABLE IF NOT EXISTS astrid_meta.export_jobs (
            table_name String,
            format LowCardinality(String),
            output_path String,
            created_at DateTime DEFAULT now()
        ) ENGINE = MergeTree()
        ORDER BY (table_name, created_at)
        """
    )
