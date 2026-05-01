#!/usr/bin/env python3
"""
scrape_events.py — Simple one-shot event scraper for RLD contracts.

Reads deployment.json, builds topic0 → name map for every event our
contracts emit, then does a single eth_getLogs from block 0 → latest
across all deployed addresses. Prints what it finds. No DB, no loop.

Usage:
    python3 scripts/scrape_events.py [--rpc http://127.0.0.1:8545] [--config docker/deployment.json]
"""
import argparse
import json
import sys
from web3 import Web3

# ── Every event our contracts emit ────────────────────────────────────────

EVENTS = {
    # IRLDCore
    "MarketCreated(bytes32,address,address,address)":           "MarketCreated",
    "PositionModified(bytes32,address,int256,int256)":          "PositionModified",
    "MarketStateUpdated(bytes32,uint128,uint128)":              "MarketStateUpdated",
    "FundingApplied(bytes32,uint256,uint256,int256,uint256)":   "FundingApplied",
    "Liquidation(bytes32,address,address,uint256,uint256,uint256)": "Liquidation",
    "BadDebtRegistered(bytes32,uint128,uint128)":               "BadDebtRegistered",
    "BadDebtSocialized(bytes32,uint128,uint128,uint128)":       "BadDebtSocialized",
    "SecurityUpdate(bytes32,string,address)":                   "SecurityUpdate",
    "AccountStateHash(bytes32,address,bytes32)":                "AccountStateHash",

    # PrimeBrokerFactory
    "BrokerCreated(address,address,uint256)":                   "BrokerCreated",

    # PrimeBroker
    "BrokerInitialized(bytes32,address,address)":               "BrokerInitialized",
    "AccountBalanceChanged(address,address,int256,uint256,bytes32)": "AccountBalanceChanged",
    "Execute(address,bytes)":                                   "Execute",
    "OperatorUpdated(address,bool)":                            "OperatorUpdated",
    "LiquidityAdded(uint256,uint128)":                          "LiquidityAdded",
    "LiquidityRemoved(uint256,uint128,bool)":                   "LiquidityRemoved",
    "ActivePositionChanged(uint256,uint256)":                   "ActivePositionChanged",
    "TwammOrderSubmitted(bytes32,bool,uint256,uint256)":        "TwammOrderSubmitted",
    "TwammOrderCancelled(bytes32,uint256,uint256)":             "TwammOrderCancelled",
    "TwammOrderClaimed(bytes32,uint256,uint256)":               "TwammOrderClaimed",
    "ActiveTwammOrderChanged(bytes32,bytes32)":                 "ActiveTwammOrderChanged",
    "StateAudit(address,uint256,uint256,uint128,uint256,uint256)": "StateAudit",
    "BrokerFrozen(address)":                                    "BrokerFrozen",
    "BrokerUnfrozen(address)":                                  "BrokerUnfrozen",

    # IJTM (TWAMM Hook)
    "SubmitOrder(int24,bytes32,address,uint256,uint160,bool,uint256,uint256,uint256)": "TWAMM_SubmitOrder",
    "CancelOrder(int24,bytes32,address,uint256)":               "TWAMM_CancelOrder",
    "InternalMatch(bytes32,uint256,uint256)":                   "TWAMM_InternalMatch",
    "JITFill(bytes32,uint256,bool)":                            "TWAMM_JITFill",
    "AuctionClear(bytes32,address,uint256,uint256)":            "TWAMM_AuctionClear",
    "AutoSettle(bytes32,uint256,uint256,bool)":                 "TWAMM_AutoSettle",
    "ForceSettle(bytes32,uint256,uint256,bool)":                "TWAMM_ForceSettle",

    # V4 PoolManager
    "Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)": "V4_Swap",
    "ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)": "V4_ModifyLiquidity",

    # BrokerRouter
    "SwapExecuted(address,uint8,uint256,uint256)":              "Router_SwapExecuted",
    "ShortPositionUpdated(address,uint256,uint256)":            "Router_ShortPositionUpdated",
    "ShortPositionClosed(address,uint256,uint256)":             "Router_ShortPositionClosed",
    "Deposited(address,uint256,uint256)":                       "Router_Deposited",

    # BondFactory
    "BondMinted(address,address,uint256,uint256,uint256,uint256,uint256)": "BondMinted",
    "BondClosed(address,address,uint256,uint256,int256,uint256)": "BondClosed",
    "BondClaimed(address,address)":                             "BondClaimed",
    "BondReturned(address,address)":                            "BondReturned",

    # ERC20
    "Transfer(address,address,uint256)":                        "ERC20_Transfer",
    "Approval(address,address,uint256)":                        "ERC20_Approval",

    # Oracle
    "RateUpdated(uint256,uint256)":                             "RateUpdated",

    # RLDMarketFactory
    "MarketDeployed(bytes32,address,address,address,address)":  "MarketDeployed",

    # ── EXTERNAL PROTOCOLS ──────────────────────────────────────────────

    # Aave V3 Pool
    "Supply(address,address,address,uint256,uint16)":           "Aave_Supply",
    "Withdraw(address,address,address,uint256)":                "Aave_Withdraw",
    "ReserveDataUpdated(address,uint256,uint256,uint256,uint256,uint256)": "Aave_ReserveDataUpdated",
    "ReserveUsedAsCollateralEnabled(address,address)":          "Aave_CollateralEnabled",

    # Aave aToken (ScaledBalanceToken)
    "Mint(address,address,uint256,uint256,uint256)":            "aToken_Mint",
    "Burn(address,address,uint256,uint256,uint256)":            "aToken_Burn",
    "BalanceTransfer(address,address,uint256,uint256)":         "aToken_BalanceTransfer",

    # ERC4626 Vault (waUSDC wrapper, sUSDe)
    "Deposit(address,address,uint256,uint256)":                 "ERC4626_Deposit",
    "Withdraw(address,address,address,uint256,uint256)":        "ERC4626_Withdraw",

    # Permit2
    "Approval(address,address,address,uint160,uint48)":         "Permit2_Approval",

    # OpenZeppelin
    "OwnershipTransferred(address,address)":                    "OZ_OwnershipTransferred",

    # USDC (FiatTokenV2)
    "AuthorizationUsed(address,bytes32)":                       "USDC_AuthorizationUsed",

    # V4 PoolManager extras
    "Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)": "V4_Initialize",

    # V4 ERC6909 Claims (PoolManager internal accounting)
    "Transfer(address,address,address,uint256,uint256)":        "ERC6909_Transfer",
}

# Build topic0 → name
TOPIC_MAP = {}
for sig, name in EVENTS.items():
    topic0 = Web3.keccak(text=sig).hex()
    if topic0 in TOPIC_MAP:
        # Collision — append for display
        TOPIC_MAP[topic0] = TOPIC_MAP[topic0] + " / " + name
    else:
        TOPIC_MAP[topic0] = name


def addr(hex_topic):
    """Extract address from a 32-byte topic."""
    if isinstance(hex_topic, bytes):
        hex_topic = hex_topic.hex()
    return "0x" + hex_topic[-40:]


def main():
    parser = argparse.ArgumentParser(description="Scrape all RLD events")
    parser.add_argument("--rpc", default="http://127.0.0.1:8545")
    parser.add_argument("--config", default="docker/deployment.json")
    parser.add_argument("--from-block", type=int, default=0, help="Start block (0 = deployment)")
    args = parser.parse_args()

    w3 = Web3(Web3.HTTPProvider(args.rpc))
    if not w3.is_connected():
        print("ERROR: Cannot connect to", args.rpc)
        sys.exit(1)

    latest = w3.eth.block_number
    print(f"Connected to chain {w3.eth.chain_id} — latest block: {latest}")

    # Load deployment config
    with open(args.config) as f:
        cfg = json.load(f)

    # Build address watch list from deployment.json
    watched = set()
    address_labels = {}
    for key, val in cfg.items():
        if isinstance(val, str) and val.startswith("0x") and len(val) == 42:
            cs = Web3.to_checksum_address(val)
            watched.add(cs)
            address_labels[val.lower()] = key
        elif isinstance(val, dict):
            for k2, v2 in val.items():
                if isinstance(v2, str) and v2.startswith("0x") and len(v2) == 42:
                    cs = Web3.to_checksum_address(v2)
                    watched.add(cs)
                    address_labels[v2.lower()] = k2

    print(f"Watching {len(watched)} addresses")
    print()

    # Fetch ALL logs in one call
    from_block = args.from_block
    logs = w3.eth.get_logs({
        "fromBlock": from_block,
        "toBlock": latest,
        "address": list(watched),
    })

    print(f"Total logs found: {len(logs)}")
    print("=" * 100)

    # Tally
    event_counts = {}
    for log_entry in logs:
        topics = log_entry.get("topics", [])
        if not topics:
            continue
        t0 = topics[0].hex() if isinstance(topics[0], bytes) else topics[0]
        name = TOPIC_MAP.get(t0, f"UNKNOWN({t0[:18]}...)")
        contract = log_entry["address"].lower()
        label = address_labels.get(contract, contract[:10])
        block = log_entry["blockNumber"]

        key = name
        event_counts[key] = event_counts.get(key, 0) + 1

        # Print each event
        detail = ""
        if name == "BrokerCreated" and len(topics) >= 3:
            detail = f"  broker={addr(topics[1])} owner={addr(topics[2])}"
        elif name == "ERC20_Transfer" and len(topics) >= 3:
            detail = f"  from={addr(topics[1])} to={addr(topics[2])}"
        elif name == "AccountBalanceChanged" and len(topics) >= 3:
            detail = f"  account={addr(topics[1])} token={addr(topics[2])}"
        elif name == "OperatorUpdated" and len(topics) >= 2:
            detail = f"  operator={addr(topics[1])}"

        print(f"  block={block:<8} {name:<30} contract={label:<20}{detail}")

    print()
    print("=" * 100)
    print("EVENT SUMMARY:")
    print("-" * 50)
    for name, count in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"  {name:<35} {count:>5}")
    print(f"  {'TOTAL':<35} {sum(event_counts.values()):>5}")


if __name__ == "__main__":
    main()
