# Astrid Manifest v3

Astrid v3 publishes immutable Parquet data to Cloudflare R2 and updates one mutable pointer:

- Public manifest: `https://astrid.rld.fi/v2/manifest.json`
- Hourly bases: `v2/base/<stream_id>/<YYYYMMDDTHH0000Z>/data.parquet`
- 15-second deltas: `v2/deltas/<stream_id>/YYYY/MM/DD/HH/MM/SS/data.parquet`

Each file entry includes:

- `object_key` and `url`
- `sha256`, `rows`, and `bytes`
- `min_timestamp` and `max_timestamp`
- `min_cursor`, `max_cursor`, and `last_cursor`
- `schema_hash`

Pull behavior:

- Download the latest hourly base for the selected stream.
- Download deltas for the same base whose `max_timestamp` overlaps the requested range.
- Store files under `~/.astrid/data/v2/objects/`.
- Verify SHA-256 before making files visible.
- Query base and deltas together with DuckDB.

Freshness reporting:

- Use manifest `generated_at` for publish-time freshness.
- Use manifest `stats.max_timestamp` for data-time freshness.
- If cache status reports missing files, pull before analysis.
