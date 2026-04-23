import asyncio


async def main():
    from db import pool
    from indexer import build_address_market_map
    import bootstrap

    await bootstrap.init_db()
    async with pool.acquire() as conn:
        mapping = await build_address_market_map(conn)
        print("Market Map has basis_trade_factory?", "0x503b47b13a02da3eb3f592f6c4af312d1962c6d7" in mapping)
        print("Value:", mapping.get("0x503b47b13a02da3eb3f592f6c4af312d1962c6d7"))


if __name__ == "__main__":
    asyncio.run(main())
