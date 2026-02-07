#!/bin/bash
set -e

echo "══════════════════════════════════════════════"
echo "  🚀 Rate Indexer + API Starting"
echo "══════════════════════════════════════════════"

# Ensure data directory exists
DATA_DIR="${DB_DIR:-/app/data}"
mkdir -p "$DATA_DIR"

# Initialize clean DB tables
echo "📦 Initializing clean database..."
python3 rates/init_clean_db.py

# Run full sync on first start (if clean_rates.db is empty/new)
python3 rates/sync_clean_db.py --full

# Start the rate indexer daemon in background
echo "📡 Starting rate indexer daemon..."
python3 rates/daemon.py &
DAEMON_PID=$!
echo "   Daemon PID: $DAEMON_PID"

# Trap signals for graceful shutdown
cleanup() {
    echo ""
    echo "🛑 Shutting down..."
    kill $DAEMON_PID 2>/dev/null
    wait $DAEMON_PID 2>/dev/null
    echo "👋 Goodbye"
    exit 0
}
trap cleanup SIGTERM SIGINT

# Start API in foreground
echo "🌐 Starting API on port ${PORT:-8080}..."
echo "══════════════════════════════════════════════"
exec python3 -m uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8080}" &
API_PID=$!

# Wait for either process to exit
wait -n $DAEMON_PID $API_PID
EXIT_CODE=$?
echo "⚠️ Process exited with code $EXIT_CODE"
cleanup
