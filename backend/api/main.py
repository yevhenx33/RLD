"""
Rate Dashboard API — FastAPI Application Entrypoint.

Rates-only service: exposes rate data and GraphQL endpoints.
No chain indexing logic — that belongs in the new indexer.
"""

import asyncio
import logging
import os
import time
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from api.deps import get_db_connection
from api.routes.rates import router as rates_router, broadcast_rates

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# --- App ---
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# --- Rate Limiter ---
RATE_LIMIT_WINDOW = 10
RATE_LIMIT_MAX_REQUESTS = 20
request_history = defaultdict(list)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = time.time()
    request_history[client_ip] = [t for t in request_history[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(request_history[client_ip]) >= RATE_LIMIT_MAX_REQUESTS:
        return JSONResponse(status_code=429, content={"error": "Too Many Requests. Please slow down."})
    request_history[client_ip].append(now)
    if len(request_history) > 5000:
        request_history.clear()
    response = await call_next(request)
    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if "server" in response.headers:
        del response.headers["server"]
    return response


# --- Middleware ---
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        "localhost", "127.0.0.1", "0.0.0.0", "testserver",
        "rate-dashboard.onrender.com", "rld.fi", "www.rld.fi",
        "envio-graphql", "host.docker.internal",
    ],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://rate-dashboard.netlify.app",
        "https://rate-dashboard.onrender.com",
        "https://www.rate-dashboard.onrender.com",
        "https://rld.fi",
        "https://www.rld.fi",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Health Check ---
@app.get("/")
def health_check():
    last_block = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM sync_state WHERE key='last_block_number'")
        row = cursor.fetchone()
        if row:
            last_block = int(row['value'])
        conn.close()
    except Exception as e:
        logger.error(f"Health check db error: {e}")

    return {"status": "ok", "message": "Rate Dashboard API is running", "last_indexed_block": last_block}


# --- Include Routers ---
app.include_router(rates_router)

# GraphQL endpoint
from api.routes.graphql_rates import graphql_router
app.include_router(graphql_router)


# --- Lifecycle ---
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcast_rates())


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("🛑 Shutdown complete")