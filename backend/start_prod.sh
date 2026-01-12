#!/bin/bash

#!/bin/bash

# --- DB SEEDING (Seed persistent disk from image) ---
if [ -n "$DB_DIR" ] && [ -d "$DB_DIR" ]; then
    echo "💾 Checking Persistent Storage at $DB_DIR..."
    for FILE in "aave_rates.db" "clean_rates.db"; do
        TARGET="$DB_DIR/$FILE"
        SOURCE="./$FILE"
        
        if [ ! -f "$TARGET" ]; then
            if [ -f "$SOURCE" ]; then
                echo "   👉 Seeding $FILE from image to persistent disk..."
                cp "$SOURCE" "$TARGET"
            else
                echo "   ⚠️ $SOURCE not found in image. Skipping seed."
            fi
        else
            echo "   ✅ $FILE exists in storage."
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

# Start API in foreground
echo "🚀 Starting API Service..."
exec uvicorn api:app --host 0.0.0.0 --port 8080
