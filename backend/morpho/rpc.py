"""Morpho Blue Indexer — RPC & Multicall helpers."""
import json, time, logging
from urllib.request import Request, urlopen
from morpho.config import (
    MAINNET_RPC_URL, MULTICALL3, MORPHO_BLUE,
    SEL_MARKET, SEL_POSITION, SEL_TOTAL_ASSETS, SEL_TOTAL_SUPPLY,
    SEL_PRICE, SEL_RATE_AT_TARGET, ADAPTIVE_CURVE_IRM,
    MULTICALL_BATCH_SIZE,
)

log = logging.getLogger(__name__)

def eth_call(to, data, block="latest"):
    return rpc_request("eth_call", [{"to": to, "data": data}, block])

def eth_block_number():
    return int(rpc_request("eth_blockNumber", []), 16)

def eth_get_block(block_num):
    hex_block = hex(block_num) if isinstance(block_num, int) else block_num
    return rpc_request("eth_getBlockByNumber", [hex_block, False])

def eth_get_logs(from_block, to_block, address, topics):
    return rpc_request("eth_getLogs", [{
        "fromBlock": hex(from_block), "toBlock": hex(to_block),
        "address": address, "topics": topics,
    }])

def rpc_request(method, params, retries=3):
    payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    for attempt in range(retries):
        try:
            req = Request(MAINNET_RPC_URL, data=payload.encode(),
                          headers={"Content-Type": "application/json"})
            resp = json.loads(urlopen(req, timeout=30).read())
            if "error" in resp:
                raise RuntimeError(f"RPC error: {resp['error']}")
            return resp["result"]
        except Exception as e:
            if attempt == retries - 1:
                raise
            log.warning(f"RPC retry {attempt+1}: {e}")
            time.sleep(2 ** attempt)

def rpc_batch(calls, retries=3):
    """Send a batch of RPC calls. Each call is (method, params)."""
    payload = json.dumps([
        {"jsonrpc": "2.0", "method": m, "params": p, "id": i}
        for i, (m, p) in enumerate(calls)
    ])
    for attempt in range(retries):
        try:
            req = Request(MAINNET_RPC_URL, data=payload.encode(),
                          headers={"Content-Type": "application/json"})
            results = json.loads(urlopen(req, timeout=60).read())
            results.sort(key=lambda r: r["id"])
            return [r.get("result") for r in results]
        except Exception as e:
            if attempt == retries - 1:
                raise
            log.warning(f"Batch RPC retry {attempt+1}: {e}")
            time.sleep(2 ** attempt)

# ─── ABI Encoding Helpers ────────────────────────────────────

def encode_bytes32(hex_str):
    """Pad a bytes32 value to 32 bytes."""
    clean = hex_str.replace("0x", "").lower()
    return clean.zfill(64)

def encode_address(addr):
    """Pad an address to 32 bytes."""
    return addr.replace("0x", "").lower().zfill(64)

def decode_uint(hex_str, offset=0):
    """Decode a uint256 from hex at given 32-byte word offset."""
    clean = hex_str.replace("0x", "")
    start = offset * 64
    return int(clean[start:start+64], 16)

def decode_int(hex_str, offset=0):
    """Decode a int256 from hex at given 32-byte word offset."""
    val = decode_uint(hex_str, offset)
    if val >= 2**255:
        val -= 2**256
    return val

def decode_address(hex_str, offset=0):
    """Decode an address from hex at given 32-byte word offset."""
    clean = hex_str.replace("0x", "")
    start = offset * 64
    return "0x" + clean[start+24:start+64]

# ─── High-Level Multicall ────────────────────────────────────

def multicall_market_states(market_ids, block="latest"):
    """Batch-fetch market(id) for multiple markets. Returns dict[market_id -> state]."""
    block_hex = hex(block) if isinstance(block, int) else block
    calls = []
    for mid in market_ids:
        data = SEL_MARKET + encode_bytes32(mid)
        calls.append(("eth_call", [{"to": MORPHO_BLUE, "data": data}, block_hex]))

    results = {}
    for batch_start in range(0, len(calls), MULTICALL_BATCH_SIZE):
        batch = calls[batch_start:batch_start+MULTICALL_BATCH_SIZE]
        batch_results = rpc_batch(batch)
        for i, res in enumerate(batch_results):
            mid = market_ids[batch_start + i]
            if res and res != "0x":
                try:
                    results[mid] = {
                        "totalSupplyAssets": decode_uint(res, 0),
                        "totalSupplyShares": decode_uint(res, 1),
                        "totalBorrowAssets": decode_uint(res, 2),
                        "totalBorrowShares": decode_uint(res, 3),
                        "lastUpdate":       decode_uint(res, 4),
                        "fee":              decode_uint(res, 5),
                    }
                except Exception as e:
                    log.warning(f"Failed to decode market {mid[:18]}: {e}")
    return results

def multicall_positions(market_ids, vault_addresses, block="latest"):
    """Batch-fetch position(id, vault) for all vault-market pairs.
    Returns dict[(market_id, vault_address) -> supplyShares]."""
    block_hex = hex(block) if isinstance(block, int) else block
    pairs = [(mid, va) for mid in market_ids for va in vault_addresses]

    calls = []
    for mid, va in pairs:
        data = SEL_POSITION + encode_bytes32(mid) + encode_address(va)
        calls.append(("eth_call", [{"to": MORPHO_BLUE, "data": data}, block_hex]))

    results = {}
    for batch_start in range(0, len(calls), MULTICALL_BATCH_SIZE):
        batch = calls[batch_start:batch_start+MULTICALL_BATCH_SIZE]
        batch_results = rpc_batch(batch)
        for i, res in enumerate(batch_results):
            mid, va = pairs[batch_start + i]
            if res and res != "0x":
                try:
                    supply_shares = decode_uint(res, 0)
                    if supply_shares > 0:
                        results[(mid, va)] = supply_shares
                except Exception:
                    pass
    return results

def multicall_vault_states(vault_addresses, block="latest"):
    """Batch-fetch totalAssets() and totalSupply() for vaults."""
    block_hex = hex(block) if isinstance(block, int) else block
    calls = []
    for va in vault_addresses:
        calls.append(("eth_call", [{"to": va, "data": SEL_TOTAL_ASSETS}, block_hex]))
        calls.append(("eth_call", [{"to": va, "data": SEL_TOTAL_SUPPLY}, block_hex]))

    results = {}
    for batch_start in range(0, len(calls), MULTICALL_BATCH_SIZE):
        batch = calls[batch_start:batch_start+MULTICALL_BATCH_SIZE]
        batch_results = rpc_batch(batch)
        for i in range(0, len(batch_results), 2):
            idx = (batch_start + i) // 2
            va = vault_addresses[idx]
            r_assets = batch_results[i]
            r_supply = batch_results[i+1] if i+1 < len(batch_results) else None
            try:
                ta = decode_uint(r_assets, 0) if r_assets and r_assets != "0x" else 0
                ts = decode_uint(r_supply, 0) if r_supply and r_supply != "0x" else 0
                results[va] = {"totalAssets": ta, "totalSupply": ts}
            except Exception:
                pass
    return results

def multicall_oracle_prices(oracles, block="latest"):
    """Batch-fetch price() for oracle contracts. Returns dict[oracle_address -> price]."""
    block_hex = hex(block) if isinstance(block, int) else block
    calls = [("eth_call", [{"to": o, "data": SEL_PRICE}, block_hex]) for o in oracles]

    results = {}
    for batch_start in range(0, len(calls), MULTICALL_BATCH_SIZE):
        batch = calls[batch_start:batch_start+MULTICALL_BATCH_SIZE]
        batch_results = rpc_batch(batch)
        for i, res in enumerate(batch_results):
            oracle = oracles[batch_start + i]
            if res and res != "0x" and len(res.replace("0x","")) >= 64:
                try:
                    results[oracle] = decode_uint(res, 0)
                except Exception:
                    pass
    return results

def multicall_irm_rates(market_ids, block="latest"):
    """Batch-fetch rateAtUTarget(id) from AdaptiveCurveIRM."""
    block_hex = hex(block) if isinstance(block, int) else block
    calls = []
    for mid in market_ids:
        data = SEL_RATE_AT_TARGET + encode_bytes32(mid)
        calls.append(("eth_call", [{"to": ADAPTIVE_CURVE_IRM, "data": data}, block_hex]))

    results = {}
    for batch_start in range(0, len(calls), MULTICALL_BATCH_SIZE):
        batch = calls[batch_start:batch_start+MULTICALL_BATCH_SIZE]
        batch_results = rpc_batch(batch)
        for i, res in enumerate(batch_results):
            mid = market_ids[batch_start + i]
            if res and res != "0x" and len(res.replace("0x","")) >= 64:
                try:
                    results[mid] = decode_int(res, 0)
                except Exception:
                    pass
    return results
