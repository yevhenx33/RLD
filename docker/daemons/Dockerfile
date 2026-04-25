# ═══════════════════════════════════════════════════════════════
# RLD Daemons — Python runtime for MM daemon + Chaos trader
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim

WORKDIR /app

# Install deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/services/ services/
COPY backend/tools/ tools/
COPY backend/db/ db/
COPY backend/api/ api/
COPY backend/rates_client.py rates_client.py

ENV PYTHONUNBUFFERED=1

# Default: run combined daemon (override with command in compose)
CMD ["python", "services/combined_daemon.py"]
