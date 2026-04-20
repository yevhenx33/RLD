#!/usr/bin/env python3
"""
main.py — Indexer process entrypoint.

Starts two things in the same process:
  1. The async indexer polling loop (indexer.py)
  2. The Uvicorn GraphQL API server (api/graphql.py)

Both share the same asyncpg pool via db.py.
This keeps the stack to a single container, single process.
"""
import asyncio
import logging
import os
import sys

import uvicorn

log = logging.getLogger("main")


async def run_all() -> None:
    import db
    import bootstrap

    rpc_url = os.environ["RPC_URL"]
    dsn = os.environ["DATABASE_URL"]
    admin_token = os.getenv("INDEXER_ADMIN_TOKEN", "").strip()

    # Bootstrap: run migrations, load global config
    await db.init(dsn)
    global_cfg = await bootstrap.bootstrap(db.pool)
    if admin_token:
        log.info("Admin reset token protection enabled")
    else:
        log.warning("INDEXER_ADMIN_TOKEN is unset; /admin/reset accepts unauthenticated local calls")

    # Start indexer loop as background task
    import indexer
    indexer_task = asyncio.create_task(
        indexer.run(rpc_url, dsn)
    )

    # Start API server (uvicorn with asyncio loop)
    port = int(os.getenv("API_PORT", "8080"))
    config = uvicorn.Config(
        "api.graphql:app",
        host="0.0.0.0",
        port=port,
        log_level="info",
        loop="none",  # use existing event loop
    )
    server = uvicorn.Server(config)

    log.info("Starting GraphQL API on port %d", port)
    log.info("Starting indexer loop (rpc=%s)", rpc_url)

    # Run both; if indexer crashes, shut down API too
    api_task = asyncio.create_task(server.serve())

    try:
        done, pending = await asyncio.wait(
            [indexer_task, api_task],
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            exc = task.exception()
            if exc:
                log.critical("Fatal error in task %s: %s", task.get_name(), exc, exc_info=exc)
    finally:
        for task in [indexer_task, api_task]:
            task.cancel()
        await db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    asyncio.run(run_all())
