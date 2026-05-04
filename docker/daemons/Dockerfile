# RLD Python runtime for MM daemon, chaos trader, and arb bot.
FROM python:3.11.15-slim

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/services/ services/
COPY backend/tools/ tools/
COPY backend/db/ db/
COPY backend/rates_client.py rates_client.py

RUN chown -R app:app /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER app

CMD ["python", "services/combined_daemon.py"]
