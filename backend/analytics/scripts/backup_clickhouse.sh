#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${CLICKHOUSE_BACKUP_DIR:-/mnt/data/clickhouse-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${BACKUP_DIR}/clickhouse-${STAMP}.tar.zst"

mkdir -p "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx 'rld_clickhouse'; then
  echo "rld_clickhouse container is not running" >&2
  exit 1
fi

docker exec rld_clickhouse clickhouse-client --query "SYSTEM FLUSH LOGS" >/dev/null
docker run --rm \
  --volumes-from rld_clickhouse \
  -v "${BACKUP_DIR}:${BACKUP_DIR}" \
  alpine:3.20 \
  sh -c "apk add --no-cache zstd >/dev/null && tar -C /var/lib/clickhouse -I 'zstd -T0 -19' -cf '${OUT}' ."

echo "$OUT"
