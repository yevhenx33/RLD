#!/bin/bash

# Start Indexer in background
echo "🚀 Starting Indexer Service..."
python3 indexer.py > indexer.log 2>&1 &

# Start API in foreground
echo "🚀 Starting API Service..."
exec uvicorn api:app --host 0.0.0.0 --port 8080
