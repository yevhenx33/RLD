FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start_prod.sh
ENV PYTHONUNBUFFERED=1

# Expose port 8080 (Cloud Run default)
EXPOSE 8080

CMD ["./start_prod.sh"]
