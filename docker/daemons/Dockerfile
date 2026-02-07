# ═══════════════════════════════════════════════════════════════
# RLD Daemons — Python runtime for MM daemon + Chaos trader
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim

WORKDIR /app

# Install jq for wait-for-config.sh
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*

# Install deps
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/services/ services/
COPY backend/tools/ tools/
COPY backend/indexers/ indexers/
COPY backend/db/ db/
COPY backend/api/ api/

# Copy wait-for-config
COPY docker/daemons/wait-for-config.sh /usr/local/bin/wait-for-config.sh
RUN chmod +x /usr/local/bin/wait-for-config.sh

ENV PYTHONUNBUFFERED=1

# Default: run combined daemon (override with command in compose)
ENTRYPOINT ["wait-for-config.sh"]
CMD ["python", "services/combined_daemon.py"]
