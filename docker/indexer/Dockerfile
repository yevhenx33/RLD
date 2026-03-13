# ═══════════════════════════════════════════════════════════════
# RLD On-Chain Indexer
# One container: asyncio indexer loop + Strawberry GraphQL API
# ═══════════════════════════════════════════════════════════════
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy indexer source
COPY backend/indexers/ /app/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["python3", "main.py"]
