#!/bin/bash

#!/bin/bash

# --- DB SEEDING (Seed persistent disk from image) ---
if [ -n "$DB_DIR" ] && [ -d "$DB_DIR" ]; then
    echo "💾 Checking Persistent Storage at $DB_DIR..."
    for FILE in "aave_rates.db" "clean_rates.db"; do
        TARGET="$DB_DIR/$FILE"
        SOURCE="./$FILE"
        
        # Auto-Repair: Overwrite if target is tiny (<500KB)
        if [ -f "$TARGET" ]; then
            TSIZE=$(wc -c < "$TARGET")
            # 500KB = 512000 bytes. 24KB is 24576. 1.2MB is ~1200000.
            if [ "$TSIZE" -lt 512000 ]; then
                echo "   ⚠️ Target $FILE is tiny ($TSIZE bytes). Overwriting with seed..."
                rm "$TARGET"
            else
                echo "   ✅ $FILE exists in storage ($TSIZE bytes). Keeping it."
            fi
        fi

        if [ ! -f "$TARGET" ]; then
            if [ -f "$SOURCE" ]; then
                echo "   👉 Seeding $FILE from image to persistent disk..."
                cp "$SOURCE" "$TARGET"
            else
                echo "   ⚠️ $SOURCE not found in image. Skipping seed."
            fi
        fi
    done
fi

# Ensure Clean DB exists
echo "📦 Initializing Database..."
python3 scripts/init_clean_db.py

# Start Indexer in background
echo "🚀 Starting Indexer Service..."
python3 indexer.py > indexer.log 2>&1 &

# Start Backfill Service (Recent History)
echo "⏳ Starting History Backfill..."
python3 fill_gaps_startup.py > backfill.log 2>&1 &

# Start Monitor Service
echo "🤖 Starting Telegram Monitor..."
python3 scripts/monitor_service.py > monitor.log 2>&1 &

# Start API in foreground
echo "🚀 Starting API Service on port ${PORT:-10000}..."
exec uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}
