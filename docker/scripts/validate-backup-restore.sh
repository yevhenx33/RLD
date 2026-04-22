#!/usr/bin/env bash
set -euo pipefail

BACKUP_ROOT="/home/ubuntu/RLD/backups"
STATUS_FILE="$BACKUP_ROOT/last_restore_check.json"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

latest_dir="$(ls -1dt "$BACKUP_ROOT"/20* 2>/dev/null | head -1 || true)"
if [ -z "${latest_dir:-}" ]; then
  cat > "$STATUS_FILE" <<EOF
{"timestamp":"$TIMESTAMP","status":"failed","reason":"no_backup_directory"}
EOF
  exit 1
fi

pg_backup="$latest_dir/rld_indexer.sql.gz"
schema_backup="$latest_dir/clickhouse_schema.sql.gz"
if [ ! -f "$pg_backup" ]; then
  cat > "$STATUS_FILE" <<EOF
{"timestamp":"$TIMESTAMP","status":"failed","reason":"missing_postgres_backup","backup_dir":"$latest_dir"}
EOF
  exit 1
fi

schema_ok=false
if [ -f "$schema_backup" ] && gzip -dc "$schema_backup" | grep -q "CREATE TABLE"; then
  schema_ok=true
fi

container="rld_restore_check_$$"
port=$((55000 + RANDOM % 500))
tables_count=0
status="failed"
reason="unknown"

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker run -d --rm \
  --name "$container" \
  -e POSTGRES_PASSWORD=restore \
  -e POSTGRES_USER=restore \
  -e POSTGRES_DB=restore \
  -p "127.0.0.1:${port}:5432" \
  postgres:15-alpine >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$container" pg_isready -U restore -d restore >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec "$container" pg_isready -U restore -d restore >/dev/null 2>&1; then
  reason="restore_container_not_ready"
else
  if gunzip -c "$pg_backup" | docker exec -i "$container" psql -U restore -d restore >/dev/null 2>&1; then
    tables_count="$(docker exec "$container" psql -U restore -d restore -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null | tr -d '[:space:]' || echo 0)"
    if [ "${tables_count:-0}" -gt 0 ]; then
      status="success"
      reason="ok"
    else
      reason="no_public_tables_after_restore"
    fi
  else
    reason="psql_restore_failed"
  fi
fi

cat > "$STATUS_FILE" <<EOF
{
  "timestamp": "$TIMESTAMP",
  "backup_dir": "$latest_dir",
  "status": "$status",
  "reason": "$reason",
  "tables_restored": ${tables_count:-0},
  "clickhouse_schema_backup_ok": $schema_ok
}
EOF

if [ "$status" != "success" ]; then
  exit 1
fi
