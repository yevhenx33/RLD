"""
db.py — asyncpg connection pool singleton.

Usage:
    await db.init(dsn)
    async with db.pool.acquire() as conn:
        ...
    await db.close()
"""
import asyncpg
import logging

log = logging.getLogger(__name__)

pool: asyncpg.Pool | None = None


async def init(dsn: str, min_size: int = 2, max_size: int = 10) -> None:
    global pool
    if pool is not None:
        return
    pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
    log.info("DB pool up (min=%d max=%d)", min_size, max_size)


async def get_pool(
    dsn: str | None = None,
    min_size: int = 2,
    max_size: int = 10,
) -> asyncpg.Pool:
    global pool
    if pool is None:
        if not dsn:
            raise RuntimeError("DATABASE_URL is required before DB pool initialization")
        await init(dsn, min_size=min_size, max_size=max_size)
    if pool is None:
        raise RuntimeError("DB pool failed to initialize")
    return pool


async def close() -> None:
    global pool
    if pool:
        await pool.close()
        pool = None
