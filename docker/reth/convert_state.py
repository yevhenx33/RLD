#!/usr/bin/env python3
"""
Anvil State Dump → Reth Genesis Converter
==========================================
Converts Anvil's `anvil_dumpState` JSON output into a Reth-compatible
genesis.json with all accounts, code, storage, and balances.

Usage:
    python3 convert_state.py --input /tmp/anvil-dump.json --output genesis.json

    # With extra pre-funded accounts (hex private keys → 10000 ETH each):
    python3 convert_state.py --input /tmp/anvil-dump.json --output genesis.json \
        --fund-keys 0xac0974...  0x59c69...

    # Patch contracts not captured by anvil_dumpState (read-only mainnet contracts):
    python3 convert_state.py --input /tmp/anvil-dump.json --output genesis.json \
        --anvil-rpc http://localhost:8545 \
        --patch-contracts 0x52f0e24d... 0x7ffe42c4...

The generated genesis.json can be used with:
    reth node --dev --chain genesis.json
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from eth_account import Account


# 10,000 ETH in wei (hex)
PREFUND_BALANCE = hex(10_000 * 10**18)

# Default Anvil/Hardhat mnemonic accounts (first 10)
DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"


def derive_addresses_from_mnemonic(mnemonic: str, count: int = 10) -> list[str]:
    """Derive addresses from BIP-39 mnemonic (same as Anvil defaults)."""
    Account.enable_unaudited_hdwallet_features()
    addresses = []
    for i in range(count):
        acct = Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{i}")
        addresses.append(acct.address.lower())
    return addresses


# ── RPC helpers for patching contracts ─────────────────────────

def _rpc_call(rpc_url: str, method: str, params: list) -> dict:
    """Make a JSON-RPC call to the Anvil fork."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1,
    }).encode()
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def fetch_contract_state(rpc_url: str, address: str) -> dict | None:
    """
    Fetch full state for a contract from a running Anvil fork.
    Returns {balance, nonce, code, storage} or None on failure.
    """
    addr = address if address.startswith("0x") else f"0x{address}"

    # 1. Code
    try:
        r = _rpc_call(rpc_url, "eth_getCode", [addr, "latest"])
        code = r.get("result", "0x")
        if not code or code == "0x":
            return None  # Not a contract
    except Exception as e:
        print(f"    ⚠️  Failed to fetch code for {addr}: {e}", file=sys.stderr)
        return None

    # 2. Balance
    try:
        r = _rpc_call(rpc_url, "eth_getBalance", [addr, "latest"])
        balance = r.get("result", "0x0")
    except Exception:
        balance = "0x0"

    # 3. Nonce
    try:
        r = _rpc_call(rpc_url, "eth_getTransactionCount", [addr, "latest"])
        nonce_hex = r.get("result", "0x0")
        nonce = int(nonce_hex, 16)
    except Exception:
        nonce = 0

    # 4. Storage — use debug_storageRangeAt to enumerate all slots
    storage = {}
    try:
        next_key = "0x" + "00" * 32
        page = 0
        while next_key is not None and page < 500:  # safety limit
            r = _rpc_call(rpc_url, "debug_storageRangeAt", [
                "latest", 0, addr, next_key, 256
            ])
            result = r.get("result", {})
            if "error" in r:
                break
            for _hash, entry in result.get("storage", {}).items():
                slot = entry.get("key", "")
                value = entry.get("value", "")
                if slot and value and value != "0x" + "00" * 32:
                    # Ensure proper 32-byte hex formatting
                    if not slot.startswith("0x"):
                        slot = "0x" + slot
                    if not value.startswith("0x"):
                        value = "0x" + value
                    # Pad to 32 bytes
                    slot = "0x" + slot[2:].zfill(64)
                    value = "0x" + value[2:].zfill(64)
                    storage[slot] = value
            next_key = result.get("nextKey")
            page += 1
    except Exception as e:
        print(f"    ⚠️  Storage fetch incomplete for {addr}: {e}", file=sys.stderr)

    return {
        "balance": balance,
        "nonce": hex(nonce),
        "code": code,
        "storage": storage,
    }


def patch_contracts_from_rpc(
    alloc: dict,
    rpc_url: str,
    addresses: list[str],
) -> int:
    """
    Fetch full state for each address from the Anvil fork RPC and
    inject into the genesis alloc. Only patches addresses NOT already
    present in the dump (to avoid overwriting modified state).

    Returns count of patched contracts.
    """
    patched = 0
    for addr in addresses:
        addr_lower = addr.lower()
        addr_key = addr_lower[2:] if addr_lower.startswith("0x") else addr_lower

        if addr_key in alloc and "code" in alloc[addr_key]:
            print(f"    ⏭️  {addr_lower} already in dump, skipping")
            continue

        state = fetch_contract_state(rpc_url, addr_lower)
        if state is None:
            print(f"    ⚠️  {addr_lower} has no code on fork, skipping")
            continue

        slot_count = len(state.get("storage", {}))
        code_len = len(state.get("code", "")) // 2  # hex chars → bytes

        # Build alloc entry
        entry = {
            "balance": state["balance"],
            "nonce": state["nonce"],
            "code": state["code"],
        }
        if state["storage"]:
            entry["storage"] = state["storage"]

        alloc[addr_key] = entry
        patched += 1
        print(f"    ✅  {addr_lower} patched (code={code_len}B, slots={slot_count})")

    return patched


def convert_anvil_dump_to_genesis(
    anvil_dump: dict,
    extra_fund_keys: list[str] | None = None,
    chain_id: int = 31337,
    anvil_rpc: str | None = None,
    patch_contracts: list[str] | None = None,
) -> dict:
    """
    Convert Anvil state dump to Reth genesis.json format.

    Anvil dump format (from anvil_dumpState):
        {
            "accounts": {
                "0x...": {
                    "balance": "0x...",
                    "nonce": 0,
                    "code": "0x...",
                    "storage": { "0x...": "0x...", ... }
                },
                ...
            }
        }

    Reth genesis.json format:
        {
            "config": { ... chain config ... },
            "alloc": {
                "0x...": {
                    "balance": "0x...",
                    "nonce": "0x...",
                    "code": "0x...",
                    "storage": { "0x...": "0x...", ... }
                }
            }
        }
    """
    # Extract accounts from dump
    accounts = anvil_dump.get("accounts", {})

    # If dump is flat (address → account), use directly
    # Some Anvil versions return the dump without the "accounts" wrapper
    if not accounts and any(k.startswith("0x") for k in anvil_dump.keys()):
        accounts = anvil_dump

    alloc = {}

    for addr, acct_data in accounts.items():
        addr_lower = addr.lower()
        # Remove 0x prefix for genesis alloc keys (Reth convention)
        addr_key = addr_lower[2:] if addr_lower.startswith("0x") else addr_lower

        entry = {}

        # Balance — ALWAYS include (Reth requires it)
        balance = acct_data.get("balance", "0x0")
        if isinstance(balance, int):
            balance = hex(balance)
        entry["balance"] = balance if balance else "0x0"

        # Nonce — ALWAYS include
        nonce = acct_data.get("nonce", 0)
        if isinstance(nonce, str):
            nonce = int(nonce, 16) if nonce.startswith("0x") else int(nonce)
        entry["nonce"] = hex(nonce)

        # Code (contract bytecode)
        code = acct_data.get("code", "0x")
        if code and code != "0x" and code != "0x0" and len(code) > 2:
            entry["code"] = code

        # Storage
        storage = acct_data.get("storage", {})
        if storage:
            clean_storage = {}
            for slot, value in storage.items():
                # Skip zero-value storage slots
                if value and value != "0x" + "0" * 64 and value != "0x0":
                    clean_storage[slot] = value
            if clean_storage:
                entry["storage"] = clean_storage

        alloc[addr_key] = entry

    # ── Patch contracts from Anvil RPC ─────────────────────────
    if anvil_rpc and patch_contracts:
        print(f"  🔧 Patching {len(patch_contracts)} contracts from Anvil RPC...")
        patched = patch_contracts_from_rpc(alloc, anvil_rpc, patch_contracts)
        print(f"  ✅ Patched {patched}/{len(patch_contracts)} contracts")

    # Pre-fund extra accounts
    fund_addresses = set()

    # Always fund default mnemonic addresses
    try:
        for addr in derive_addresses_from_mnemonic(DEFAULT_MNEMONIC, 20):
            fund_addresses.add(addr)
    except Exception:
        pass  # eth_account not available, skip mnemonic derivation

    # Fund extra keys if provided
    if extra_fund_keys:
        for key in extra_fund_keys:
            try:
                acct = Account.from_key(key)
                fund_addresses.add(acct.address.lower())
            except Exception:
                print(f"  ⚠️  Invalid key, skipping: {key[:10]}...", file=sys.stderr)

    for addr in fund_addresses:
        addr_key = addr[2:] if addr.startswith("0x") else addr
        if addr_key in alloc:
            alloc[addr_key]["balance"] = PREFUND_BALANCE
            # EIP-3607: Reth rejects tx from senders with deployed code.
            # Anvil may attach EOF bytecode artifacts to deployer addresses
            # during forge script execution. Strip code+storage to ensure 
            # these accounts remain pure EOAs on Reth.
            alloc[addr_key].pop("code", None)
            alloc[addr_key].pop("storage", None)
            # Reset nonce so daemon get_transaction_count() starts from 0.
            # Anvil accounts carry high nonces (e.g. 317, 6113) from 
            # deployment which cause nonce mismatches on fresh Reth.
            alloc[addr_key]["nonce"] = "0x0"
        else:
            alloc[addr_key] = {"balance": PREFUND_BALANCE, "nonce": "0x0"}

    # ── USDC faucet: give Anvil account #9 $10B USDC ──────────
    # USDC (FiatTokenV2) stores balances in mapping at slot 9.
    # Slot = keccak256(abi.encode(address, 9))
    # We use Anvil account #9 (key 0x2a871d...) as the faucet because
    # we control its private key, unlike the mainnet whale.
    USDC_ADDR = "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    USDC_FAUCET = "a0ee7a142d267c1f36714e4a8f75612f20a79720"  # Anvil account #9
    # Pre-computed: keccak256(abi.encode(0xa0Ee7A...faucet, 9))
    FAUCET_BAL_SLOT = "0x6e59c0c2a71b8dec446745641cfac75f1ce117acb4ebfb165098fb1c124ff183"
    TEN_BILLION_USDC = hex(10_000_000_000 * 10**6)  # $10B, 6 decimals

    if USDC_ADDR in alloc and "storage" in alloc[USDC_ADDR]:
        alloc[USDC_ADDR]["storage"][FAUCET_BAL_SLOT] = "0x" + f"{int(TEN_BILLION_USDC, 16):064x}"
        print(f"  💰 USDC faucet: 0x{USDC_FAUCET} (Anvil #9) gets $10B USDC")
    else:
        print("  ⚠️  USDC contract not in dump — faucet not configured", file=sys.stderr)

    # Build genesis config
    genesis = {
        "config": {
            "chainId": chain_id,
            "homesteadBlock": 0,
            "eip150Block": 0,
            "eip155Block": 0,
            "eip158Block": 0,
            "byzantiumBlock": 0,
            "constantinopleBlock": 0,
            "petersburgBlock": 0,
            "istanbulBlock": 0,
            "muirGlacierBlock": 0,
            "berlinBlock": 0,
            "londonBlock": 0,
            "arrowGlacierBlock": 0,
            "grayGlacierBlock": 0,
            "terminalTotalDifficulty": 0,
            "terminalTotalDifficultyPassed": True,
            "shanghaiTime": 0,
            "cancunTime": 0,
        },
        "nonce": "0x0",
        "timestamp": hex(int(time.time())),  # current real-world time
        "extraData": "0x",
        "gasLimit": "0x1c9c380",  # 30M gas limit
        "difficulty": "0x0",
        "mixHash": "0x0000000000000000000000000000000000000000000000000000000000000000",
        "coinbase": "0x0000000000000000000000000000000000000000",
        "baseFeePerGas": "0x3B9ACA00",  # 1 gwei — keeps daemon hardcoded gas prices working
        "alloc": alloc,
    }

    return genesis


def main():
    parser = argparse.ArgumentParser(
        description="Convert Anvil state dump to Reth genesis.json"
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Path to Anvil dump JSON"
    )
    parser.add_argument(
        "--output", "-o", required=True, help="Output path for genesis.json"
    )
    parser.add_argument(
        "--fund-keys",
        nargs="*",
        default=[],
        help="Extra private keys to pre-fund with 10000 ETH",
    )
    parser.add_argument(
        "--chain-id", type=int, default=31337, help="Chain ID (default: 31337)"
    )
    parser.add_argument(
        "--anvil-rpc",
        default=None,
        help="Anvil fork RPC URL (for --patch-contracts)",
    )
    parser.add_argument(
        "--patch-contracts",
        nargs="*",
        default=[],
        help="Extra contract addresses to fetch from Anvil RPC and include in genesis",
    )
    args = parser.parse_args()

    # Load Anvil dump
    print(f"📥 Loading Anvil dump from {args.input}...")
    with open(args.input, "r") as f:
        anvil_dump = json.load(f)

    # Convert
    print("🔄 Converting to Reth genesis format...")
    genesis = convert_anvil_dump_to_genesis(
        anvil_dump,
        extra_fund_keys=args.fund_keys,
        chain_id=args.chain_id,
        anvil_rpc=args.anvil_rpc,
        patch_contracts=args.patch_contracts,
    )

    account_count = len(genesis["alloc"])
    code_count = sum(1 for v in genesis["alloc"].values() if "code" in v)
    storage_count = sum(1 for v in genesis["alloc"].values() if "storage" in v)

    print(f"   Accounts: {account_count}")
    print(f"   Contracts (with code): {code_count}")
    print(f"   Accounts with storage: {storage_count}")

    # Write output
    print(f"📤 Writing genesis to {args.output}...")
    with open(args.output, "w") as f:
        json.dump(genesis, f, indent=2)

    size_mb = len(json.dumps(genesis)) / 1024 / 1024
    print(f"✅ Done! Genesis size: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
