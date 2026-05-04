FROM python:3.12.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app/analytics
RUN chown -R app:app /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app

USER app

CMD ["python", "-m", "analytics.scripts.run_indexer", "--source", "AAVE_MARKET", "--role", "collector"]
