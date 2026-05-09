import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.getenv("MAINNET_RPC_URL"), reason="MAINNET_RPC_URL is required for live Euler RPC tests")
def test_live_governed_perspective_and_verified_vault_getters():
    from analytics.scripts.euler_ops import (
        RpcClient,
        _asset,
        _call_uint,
        _is_verified,
        _verified_array,
    )

    rpc = RpcClient(os.environ["MAINNET_RPC_URL"], retries=1)
    block_number = max(0, rpc.block_number() - 12)
    verified = _verified_array(rpc, block_number)
    assert verified, "governedPerspective returned no verified vaults"

    vault = verified[0]
    assert _is_verified(rpc, vault, block_number) is True
    asset = _asset(rpc, vault, block_number)
    assert asset.startswith("0x") and len(asset) == 42
    assert _call_uint(rpc, vault, "totalAssets", block_number) >= 0
    assert _call_uint(rpc, vault, "totalSupply", block_number) >= 0
    assert _call_uint(rpc, vault, "interestRate", block_number) >= 0
