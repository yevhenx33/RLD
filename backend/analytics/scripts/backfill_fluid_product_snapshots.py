"""Deprecated Fluid direct-RPC product snapshot entrypoint.

Fluid product state is now expected to come from Envio/event replay tables.
This module keeps only small feed-registry helpers used by tests and shared
classification code; invoking it as a job exits with an explicit deprecation
message.
"""

from __future__ import annotations

import argparse

from analytics.sources.morpho import resolve_symbol_price

CHAINLINK_PROXY_FEEDS = {
    "GHO": {"proxy": "0x3f12643d3f6f874d39c2a4c9f2cd6f2dbac877fc", "feed": "GHO / USD", "quote": "USD"},
    "USDTB": {"proxy": "0x66704dad467a7ca508b3be15865d9b9f3e186c90", "feed": "USDtb / USD", "quote": "USD"},
    "USR": {"proxy": "0x34ad75691e25a8e9b681aaa85dbeb7ef6561b42c", "feed": "USR / USD", "quote": "USD"},
    "METH": {"proxy": "0x5b563107c8666d2142c216114228443b94152362", "feed": "mETH / ETH", "quote": "ETH"},
    "EZETH": {"proxy": "0x636a000262f6aa9e1f094abf0ad8f645c44f641c", "feed": "ezETH / ETH", "quote": "ETH"},
    "RSETH": {"proxy": "0x9d2f2f96b24c444ee32e57c04f7d944bcb8c8549", "feed": "rsETH / ETH Exchange Rate", "quote": "ETH"},
    "DEUSD": {"proxy": "0x471a6299c027bd81ed4d66069dc510bd0569f4f8", "feed": "deUSD / USD", "quote": "USD", "method": "latestRoundData"},
    "EBTC": {"proxy": "0x577c217cb5b1691a500d48aa7f69346409cfd668", "feed": "Aave eBTC / USD CAPO Oracle", "quote": "USD", "method": "latestAnswer"},
    "OSETH": {"proxy": "0x8023518b2192fb5384dadc596765b3dd1cdfe471", "feed": "StakeWise osETH / ETH Rate", "quote": "ETH", "method": "latestRoundData"},
}

RATE_PROVIDER_FEEDS = {
    "WEETHS": {"contract": "0xbe16605b22a7facef247363312121670dfe5afbe", "feed": "Ether.fi weETHs Accountant / ETH", "quote": "ETH", "method": "getRate"},
}


def resolve_fluid_feed_price(symbol: str, feed_prices: dict[str, float]) -> tuple[float, str]:
    """Resolve explicit Fluid token feed aliases without synthetic peg fallbacks."""
    symbol = str(symbol or "").strip()
    if not symbol:
        return 0.0, ""

    upper = symbol.upper()
    alias_direct = {
        "XAUT": "XAU / USD",
        "TBTC": "TBTC / USD",
    }
    feed = alias_direct.get(upper)
    if feed and float(feed_prices.get(feed, 0.0) or 0.0) > 0:
        return float(feed_prices[feed]), f"CHAINLINK:{feed}"

    alias_btc = {
        "LBTC": "LBTC / BTC",
    }
    btc_feed = alias_btc.get(upper)
    btc_usd = float(feed_prices.get("BTC / USD", 0.0) or 0.0)
    if btc_feed and btc_usd > 0:
        btc_rate = float(feed_prices.get(btc_feed, 0.0) or 0.0)
        if btc_rate > 0:
            return btc_rate * btc_usd, f"CHAINLINK:{btc_feed}*BTC / USD"

    direct = resolve_symbol_price(symbol, feed_prices)
    if direct and direct > 0:
        return float(direct), "CHAINLINK"

    return 0.0, ""


def run(args: argparse.Namespace | None = None) -> int:
    raise SystemExit("Fluid direct-RPC product snapshots have been removed; use event replay pipelines instead.")


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(func=run)


if __name__ == "__main__":
    run()
