import os
import sys
import asyncio
import hypersync

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from indexer.tokens import TOKENS

AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
RESERVE_INITIALIZED = "0x3a0ca721fc364424566385a1aa271ed508cc2c0949c2272575fb3013a163a45f"

async def main():
    token = os.getenv("ENVIO_API_TOKEN", "7a850568-160d-4cd5-bf06-2961bd383cc6")
    client = hypersync.HypersyncClient(hypersync.ClientConfig(
        url="https://eth.hypersync.xyz",
        bearer_token=token,
    ))

    # Get all ReserveInitialized events
    query = hypersync.Query(
        from_block=16291127,
        logs=[hypersync.LogSelection(
            address=[AAVE_POOL],
            topics=[[RESERVE_INITIALIZED]]
        )],
        field_selection=hypersync.FieldSelection(
            log=[hypersync.LogField.TOPIC1]
        )
    )

    print("Fetching ReserveInitialized events...")
    res = await client.get(query)
    
    active_assets = set()
    for log in res.data.logs:
        if log.topics and len(log.topics) > 1:
            asset = log.topics[1]
            if asset:
                clean_asset = asset[26:].lower()
                active_assets.add(clean_asset)
    
    print(f"Found {len(active_assets)} active Aave V3 reserves on-chain.")
    
    missing = []
    for asset in active_assets:
        if asset not in TOKENS:
            missing.append(asset)
            
    if missing:
        print(f"MISMATCH! Missing {len(missing)} assets in tokens.py:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)
    else:
        print("✅ tokens.py has 100% representation of all Aave V3 markets.")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
