#!/usr/bin/env python3
"""
Anvil State Dump → Reth Genesis Converter
==========================================
Converts Anvil's `anvil_dumpState` output into a Reth-compatible genesis.json.

Usage:
    python3 convert_state.py --input dump.json --output genesis.json
    python3 convert_state.py --input dump.json --output genesis.json --fund-keys 0xac0974...
"""

import argparse
import json
import sys
import time

# 10,000 ETH in wei
PREFUND_BALANCE = hex(10_000 * 10**18)

# Default Anvil/Hardhat mnemonic
DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"


def derive_mnemonic_addresses(mnemonic: str, count: int = 10) -> list[str]:
    """Derive addresses from BIP-39 mnemonic (Anvil defaults)."""
    try:
        from eth_account import Account
        Account.enable_unaudited_hdwallet_features()
        return [
            Account.from_mnemonic(mnemonic, account_path=f"m/44'/60'/0'/0/{i}").address.lower()
            for i in range(count)
        ]
    except ImportError:
        # Fallback: hardcode the 10 default Anvil addresses
        return [
            "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
            "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",
            "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",
            "0x90f79bf6eb2c4f870365e785982e1f101e93b906",
            "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65",
            "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc",
            "0x976ea74026e726554db657fa54763abd0c3a0aa9",
            "0x14dc79964da2c08da15fd353d30d9cba8c7c3f04",
            "0x23618e81e3f5cdf7f54c3d65f7fbc0abf5b21e8f",
            "0xa0ee7a142d267c1f36714e4a8f75612f20a79720",
        ]


def convert(anvil_dump: dict, fund_keys: list[str] | None = None, chain_id: int = 31337) -> dict:
    """Convert Anvil state dump to Reth genesis.json."""

    accounts = anvil_dump.get("accounts", {})
    if not accounts and any(k.startswith("0x") for k in anvil_dump.keys()):
        accounts = anvil_dump

    alloc = {}

    for addr, acct_data in accounts.items():
        addr_key = addr.lower().removeprefix("0x")
        entry = {}

        # Balance
        balance = acct_data.get("balance", "0x0")
        entry["balance"] = hex(balance) if isinstance(balance, int) else (balance or "0x0")

        # Nonce
        nonce = acct_data.get("nonce", 0)
        if isinstance(nonce, str):
            nonce = int(nonce, 16) if nonce.startswith("0x") else int(nonce)
        entry["nonce"] = hex(nonce)

        # Code
        code = acct_data.get("code", "0x")
        if code and code not in ("0x", "0x0") and len(code) > 2:
            entry["code"] = code

        # Storage
        storage = acct_data.get("storage", {})
        if storage:
            clean = {s: v for s, v in storage.items() if v and v != "0x" + "0" * 64 and v != "0x0"}
            if clean:
                entry["storage"] = clean

        alloc[addr_key] = entry

    # Pre-fund accounts
    fund_addrs = set()

    for addr in derive_mnemonic_addresses(DEFAULT_MNEMONIC, 10):
        fund_addrs.add(addr.lower())

    if fund_keys:
        try:
            from eth_account import Account
            for key in fund_keys:
                try:
                    fund_addrs.add(Account.from_key(key).address.lower())
                except Exception:
                    print(f"  ⚠️  Bad key: {key[:10]}...", file=sys.stderr)
        except ImportError:
            print("  ⚠️  eth_account not installed — --fund-keys ignored", file=sys.stderr)

    for addr in fund_addrs:
        addr_key = addr.removeprefix("0x")
        if addr_key in alloc:
            alloc[addr_key]["balance"] = PREFUND_BALANCE
            alloc[addr_key].pop("code", None)
            alloc[addr_key].pop("storage", None)
            alloc[addr_key]["nonce"] = "0x0"
        else:
            alloc[addr_key] = {"balance": PREFUND_BALANCE, "nonce": "0x0"}

    # Genesis
    return {
        "config": {
            "chainId": chain_id,
            "homesteadBlock": 0, "eip150Block": 0, "eip155Block": 0,
            "eip158Block": 0, "byzantiumBlock": 0, "constantinopleBlock": 0,
            "petersburgBlock": 0, "istanbulBlock": 0, "muirGlacierBlock": 0,
            "berlinBlock": 0, "londonBlock": 0, "arrowGlacierBlock": 0,
            "grayGlacierBlock": 0,
            "terminalTotalDifficulty": 0,
            "terminalTotalDifficultyPassed": True,
            "shanghaiTime": 0,
            "cancunTime": 0,
        },
        "nonce": "0x0",
        "timestamp": hex(int(time.time())),
        "extraData": "0x",
        "gasLimit": "0x1c9c380",
        "difficulty": "0x0",
        "mixHash": "0x" + "0" * 64,
        "coinbase": "0x" + "0" * 40,
        "baseFeePerGas": "0x3B9ACA00",
        "alloc": alloc,
    }


def main():
    parser = argparse.ArgumentParser(description="Anvil dump → Reth genesis")
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--fund-keys", nargs="*", default=[])
    parser.add_argument("--chain-id", type=int, default=31337)
    args = parser.parse_args()

    with open(args.input) as f:
        dump = json.load(f)

    genesis = convert(dump, fund_keys=args.fund_keys, chain_id=args.chain_id)

    acct_count = len(genesis["alloc"])
    code_count = sum(1 for v in genesis["alloc"].values() if "code" in v)
    print(f"  {acct_count} accounts ({code_count} contracts)")

    with open(args.output, "w") as f:
        json.dump(genesis, f, indent=2)

    size_mb = len(json.dumps(genesis)) / 1024 / 1024
    print(f"  Genesis: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
