"""Backfill Fluid product discovery and latest resolver snapshots.

This job populates the full-coverage Fluid product tables without treating product
exposures as canonical TVL. It uses direct Ethereum RPC calls against factories
and ERC4626-compatible fTokens, with explicit provenance on every row.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import clickhouse_connect
import requests
from eth_abi import decode as abi_decode
from eth_utils import keccak

from analytics.config import apply_env_from_config
from analytics.fluid_full_coverage import (
    ETHEREUM_CHAIN_ID,
    FLUID_DEX_FACTORY,
    FLUID_LENDING_FACTORY,
    FLUID_LIQUIDITY,
    FLUID_REVENUE_RESOLVER,
    FLUID_STETH_RESOLVER,
    FLUID_VAULT_FACTORY,
    FLUID_VAULT_RESOLVER,
    ensure_fluid_full_coverage_tables,
    normalize_address,
    seed_core_fluid_contracts,
)
from analytics.oracle_snapshots import OracleSnapshot, ensure_oracle_snapshot_tables, insert_oracle_snapshots
from analytics.schema import ensure_schema
from analytics.tokens import TOKENS
from analytics.sources.morpho import resolve_symbol_price

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def selector(signature: str) -> str:
    return "0x" + keccak(text=signature)[:4].hex()


def word_uint(value: int) -> str:
    return f"{int(value):064x}"


def word_address(value: str) -> str:
    return normalize_address(value).removeprefix("0x").rjust(64, "0")


SELECTORS = {
    "allTokens": selector("allTokens()"),
    "totalVaults": selector("totalVaults()"),
    "getVaultAddress": selector("getVaultAddress(uint256)"),
    "totalDexes": selector("totalDexes()"),
    "getDexAddress": selector("getDexAddress(uint256)"),
    "asset": selector("asset()"),
    "underlyingAsset": selector("UNDERLYING_ASSET_ADDRESS()"),
    "symbol": selector("symbol()"),
    "name": selector("name()"),
    "decimals": selector("decimals()"),
    "totalAssets": selector("totalAssets()"),
    "totalSupply": selector("totalSupply()"),
    "convertToAssets": selector("convertToAssets(uint256)"),
    "constantsView": selector("constantsView()"),
    "constantsView2": selector("constantsView2()"),
    "TYPE": selector("TYPE()"),
    "getPricesAndExchangePrices": selector("getPricesAndExchangePrices()"),
    "getCollateralReserves": selector("getCollateralReserves(uint256,uint256,uint256,uint256,uint256)"),
    "getDebtReserves": selector("getDebtReserves(uint256,uint256,uint256,uint256,uint256)"),
    "getVaultEntireData": selector("getVaultEntireData(address)"),
    "getRevenues": selector("getRevenues()"),
    "readFromStorage": selector("readFromStorage(bytes32)"),
    "latestRoundData": selector("latestRoundData()"),
    "latestAnswer": selector("latestAnswer()"),
    "getRate": selector("getRate()"),
}

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

VAULT_CONSTANTS_TYPE = "(address,address,address,address,address,address,address,address,(address,address),(address,address),uint256,uint256,bytes32,bytes32,bytes32,bytes32)"
VAULT_CONFIGS_TYPE = "(uint16,uint16,uint16,uint16,uint16,uint16,uint16,uint16,address,uint256,uint256,address,uint256)"
VAULT_EXCHANGE_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,int256,int256,int256,int256)"
VAULT_TOTALS_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256)"
VAULT_LIMITS_TYPE = "(uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
VAULT_BRANCH_TYPE = "(uint256,int256,uint256,uint256,uint256,uint256,int256)"
VAULT_STATE_TYPE = f"(uint256,int256,uint256,uint256,uint256,uint256,{VAULT_BRANCH_TYPE})"
LIQ_USER_SUPPLY_TYPE = "(bool,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
LIQ_USER_BORROW_TYPE = "(bool,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256)"
VAULT_ENTIRE_TYPE = f"(address,bool,bool,{VAULT_CONSTANTS_TYPE},{VAULT_CONFIGS_TYPE},{VAULT_EXCHANGE_TYPE},{VAULT_TOTALS_TYPE},{VAULT_LIMITS_TYPE},{VAULT_STATE_TYPE},{LIQ_USER_SUPPLY_TYPE},{LIQ_USER_BORROW_TYPE})"
DEX_TOTAL_SUPPLY_SHARES_SLOT = 2
DEX_TOTAL_BORROW_SHARES_SLOT = 4
DEX_SHARE_DECIMALS = 18


@dataclass
class RpcResult:
    ok: bool
    result: str = "0x"
    error: str = ""
    error_data: str = ""


@dataclass(frozen=True)
class PriceResolution:
    price_usd: float
    pricing_status: str
    oracle_status: str
    reason: str = ""


@dataclass
class PriceContext:
    feed_prices: dict[str, float]
    oracle_prices: dict[str, float]
    reserve_prices: dict[str, float]


class RpcClient:
    def __init__(self, rpc_url: str, timeout_sec: int = 60, retries: int = 2):
        self.rpc_url = rpc_url
        self.timeout_sec = timeout_sec
        self.retries = retries
        self._id = 0

    def call(self, to: str, data: str, block: str | int = "latest") -> RpcResult:
        if isinstance(block, int):
            block_tag = hex(block)
        else:
            block_tag = block
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": "eth_call",
            "params": [{"to": normalize_address(to), "data": data}, block_tag],
        }
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                response = requests.post(self.rpc_url, json=payload, timeout=self.timeout_sec)
                response.raise_for_status()
                item = response.json()
                if item.get("error"):
                    err = item.get("error", {}) or {}
                    err_data = err.get("data", "") if isinstance(err, dict) else ""
                    return RpcResult(False, "0x", str(err.get("message") if isinstance(err, dict) else err)[:500], str(err_data or ""))
                return RpcResult(True, str(item.get("result") or "0x"), "", "")
            except Exception as exc:
                last_error = str(exc)[:500]
                if attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
        return RpcResult(False, "0x", last_error, "")

    def block_number(self) -> int:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": "eth_blockNumber", "params": []}
        response = requests.post(self.rpc_url, json=payload, timeout=self.timeout_sec)
        response.raise_for_status()
        return int(response.json()["result"], 16)


def decode_single(raw: str, abi_type: str) -> Any:
    if not raw or raw == "0x":
        raise ValueError("empty result")
    return abi_decode([abi_type], bytes.fromhex(raw[2:]))[0]


def call_uint(rpc: RpcClient, to: str, sig: str, block: str | int = "latest") -> tuple[int | None, str]:
    res = rpc.call(to, SELECTORS[sig], block)
    if not res.ok:
        return None, res.error
    try:
        return int(decode_single(res.result, "uint256")), ""
    except Exception as exc:
        return None, str(exc)[:500]


def call_address(rpc: RpcClient, to: str, sig: str, block: str | int = "latest") -> tuple[str, str]:
    res = rpc.call(to, SELECTORS[sig], block)
    if not res.ok:
        return "", res.error
    try:
        return normalize_address(decode_single(res.result, "address")), ""
    except Exception as exc:
        return "", str(exc)[:500]


def call_string(rpc: RpcClient, to: str, sig: str, block: str | int = "latest") -> tuple[str, str]:
    res = rpc.call(to, SELECTORS[sig], block)
    if not res.ok:
        return "", res.error
    try:
        return str(decode_single(res.result, "string")), ""
    except Exception as exc:
        return "", str(exc)[:500]


def call_uint_arg(rpc: RpcClient, to: str, sig: str, arg: int, block: str | int = "latest") -> tuple[int | None, str]:
    res = rpc.call(to, SELECTORS[sig] + word_uint(arg), block)
    if not res.ok:
        return None, res.error
    try:
        return int(decode_single(res.result, "uint256")), ""
    except Exception as exc:
        return None, str(exc)[:500]


def call_address_arg(rpc: RpcClient, to: str, sig: str, arg: int, block: str | int = "latest") -> tuple[str, str]:
    res = rpc.call(to, SELECTORS[sig] + word_uint(arg), block)
    if not res.ok:
        return "", res.error
    try:
        return normalize_address(decode_single(res.result, "address")), ""
    except Exception as exc:
        return "", str(exc)[:500]


def call_address_array(rpc: RpcClient, to: str, sig: str, block: str | int = "latest") -> tuple[list[str], str]:
    res = rpc.call(to, SELECTORS[sig], block)
    if not res.ok:
        return [], res.error
    try:
        values = abi_decode(["address[]"], bytes.fromhex(res.result[2:]))[0]
        return [normalize_address(v) for v in values], ""
    except Exception as exc:
        return [], str(exc)[:500]


def call_chainlink_latest_round(rpc: RpcClient, proxy: str, block: str | int = "latest") -> tuple[tuple | None, str]:
    res = rpc.call(proxy, SELECTORS["latestRoundData"], block)
    if not res.ok:
        return None, res.error
    try:
        return abi_decode(["(uint80,int256,uint256,uint256,uint80)"], bytes.fromhex(res.result[2:]))[0], ""
    except Exception as exc:
        return None, str(exc)[:500]


def call_chainlink_latest_answer(rpc: RpcClient, proxy: str, block: str | int = "latest") -> tuple[int | None, str]:
    res = rpc.call(proxy, SELECTORS["latestAnswer"], block)
    if not res.ok:
        return None, res.error
    try:
        return int(abi_decode(["int256"], bytes.fromhex(res.result[2:]))[0]), ""
    except Exception as exc:
        return None, str(exc)[:500]


def resolve_chainlink_proxy_price(rpc: RpcClient, symbol: str, prices: PriceContext, block: str | int = "latest") -> tuple[float, str, dict[str, Any]]:
    config = CHAINLINK_PROXY_FEEDS.get(str(symbol or "").upper())
    if not config:
        return 0.0, "", {}
    decimals, dec_err = call_uint(rpc, config["proxy"], "decimals", block)
    method = str(config.get("method") or "latestRoundData")
    round_data = None
    round_err = ""
    answer: int | None = None
    if method == "latestAnswer":
        answer, round_err = call_chainlink_latest_answer(rpc, config["proxy"], block)
    else:
        round_data, round_err = call_chainlink_latest_round(rpc, config["proxy"], block)
        if round_data:
            answer = int(round_data[1])
        else:
            answer, round_err = call_chainlink_latest_answer(rpc, config["proxy"], block)
            method = "latestAnswer"
    if decimals is None or answer is None:
        return 0.0, dec_err or round_err or "Chainlink proxy read failed", {"proxy": config["proxy"], "feed": config["feed"], "method": method}
    if answer <= 0:
        return 0.0, "non-positive Chainlink answer", {"proxy": config["proxy"], "feed": config["feed"], "answer": str(answer), "method": method}
    rate = float(answer) / float(10 ** int(decimals))
    price_usd = rate
    quote = str(config.get("quote") or "USD").upper()
    if quote == "ETH":
        eth_price, eth_status = resolve_fluid_feed_price("ETH", prices.feed_prices)
        if eth_price <= 0:
            return 0.0, "missing ETH / USD feed", {"proxy": config["proxy"], "feed": config["feed"], "ethStatus": eth_status}
        price_usd = rate * eth_price
    return price_usd, "", {
        "proxy": config["proxy"],
        "feed": config["feed"],
        "quote": quote,
        "answer": str(answer),
        "decimals": int(decimals),
        "roundId": str(round_data[0]) if round_data else "",
        "updatedAt": int(round_data[3] or 0) if round_data else 0,
        "method": method,
    }


def resolve_rate_provider_price(rpc: RpcClient, symbol: str, prices: PriceContext, block: str | int = "latest") -> tuple[float, str, dict[str, Any]]:
    config = RATE_PROVIDER_FEEDS.get(str(symbol or "").upper())
    if not config:
        return 0.0, "", {}
    contract = str(config["contract"])
    decimals, dec_err = call_uint(rpc, contract, "decimals", block)
    rate_raw, rate_err = call_uint(rpc, contract, str(config.get("method") or "getRate"), block)
    if decimals is None or rate_raw is None or int(rate_raw) <= 0:
        return 0.0, dec_err or rate_err or "rate provider read failed", {"contract": contract, "feed": config["feed"]}
    rate = float(rate_raw) / float(10 ** int(decimals))
    quote = str(config.get("quote") or "USD").upper()
    quote_price = 1.0
    quote_status = "USD"
    if quote == "ETH":
        quote_price, quote_status = resolve_fluid_feed_price("ETH", prices.feed_prices)
    elif quote == "BTC":
        quote_price, quote_status = resolve_fluid_feed_price("BTC", prices.feed_prices)
    if quote_price <= 0:
        return 0.0, f"missing {quote} / USD feed", {"contract": contract, "feed": config["feed"], "quoteStatus": quote_status}
    return rate * quote_price, "", {
        "contract": contract,
        "feed": config["feed"],
        "quote": quote,
        "rateRaw": str(rate_raw),
        "decimals": int(decimals),
        "method": str(config.get("method") or "getRate"),
    }


def token_decimals(ch, token: str) -> int:
    token = normalize_address(token)
    try:
        rows = ch.query(f"SELECT any(decimals) FROM fluid_reserve_state FINAL WHERE token = '{token}'").result_rows
        if rows and rows[0][0]:
            return int(rows[0][0])
    except Exception:
        pass
    meta = TOKENS.get(token.removeprefix("0x"))
    return int(meta[1]) if meta else 18


def token_symbol(token: str) -> str:
    meta = TOKENS.get(normalize_address(token).removeprefix("0x"))
    return str(meta[0]) if meta else "UNKNOWN"


def token_usd_amount(raw: int | float | None, decimals: int, price: float) -> float:
    try:
        return (float(raw or 0) / (10 ** int(decimals))) * float(price or 0.0)
    except Exception:
        return 0.0


def bigmath(packed: int) -> int:
    return (int(packed) >> 8) << (int(packed) & 0xFF)


def normalize_dex_amount(raw: int | float | None, numerator_precision: int, denominator_precision: int) -> int:
    if not raw or not numerator_precision:
        return 0
    return (int(raw) * int(denominator_precision)) // int(numerator_precision)


def call_tuple(rpc: RpcClient, to: str, sig: str, abi_type: str, block: str | int = "latest") -> tuple[Any | None, str]:
    res = rpc.call(to, SELECTORS[sig], block)
    if not res.ok:
        return None, res.error
    try:
        return abi_decode([abi_type], bytes.fromhex(res.result[2:]))[0], ""
    except Exception as exc:
        return None, str(exc)[:500]


def call_prices_and_exchange(rpc: RpcClient, dex: str, block: str | int = "latest") -> tuple[tuple | None, str]:
    res = rpc.call(dex, SELECTORS["getPricesAndExchangePrices"], block)
    raw = res.result if res.ok else res.error_data
    if not raw or raw == "0x":
        return None, res.error or "empty result"
    # FluidDexPricesAndExchangeRates(PricesAndExchangePrice) reverts with custom error data.
    if not res.ok and len(raw) >= 10:
        raw = "0x" + raw[10:]
    try:
        return abi_decode(["(uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256)"], bytes.fromhex(raw[2:]))[0], ""
    except Exception as exc:
        return None, (res.error + "; " if res.error else "") + str(exc)[:500]


def call_reserves(rpc: RpcClient, dex: str, sig: str, args: list[int], abi_type: str, block: str | int = "latest") -> tuple[tuple | None, str]:
    data = SELECTORS[sig] + "".join(word_uint(a) for a in args)
    res = rpc.call(dex, data, block)
    if not res.ok:
        return None, res.error
    try:
        return abi_decode([abi_type], bytes.fromhex(res.result[2:]))[0], ""
    except Exception as exc:
        return None, str(exc)[:500]


def call_tuple_address_arg(rpc: RpcClient, to: str, sig: str, address: str, abi_type: str, block: str | int = "latest") -> tuple[Any | None, str]:
    res = rpc.call(to, SELECTORS[sig] + word_address(address), block)
    if not res.ok:
        return None, res.error
    try:
        return abi_decode([abi_type], bytes.fromhex(res.result[2:]))[0], ""
    except Exception as exc:
        return None, str(exc)[:500]


def latest_chainlink_prices(ch) -> dict[str, float]:
    try:
        rows = ch.query(
            """
            SELECT feed, argMax(price, timestamp) AS price
            FROM chainlink_prices
            GROUP BY feed
            """
        ).result_rows
        return {str(feed): float(price or 0.0) for feed, price in rows if price}
    except Exception:
        return {}


def latest_oracle_prices(ch) -> dict[str, float]:
    try:
        rows = ch.query(
            """
            SELECT subject, argMax(price_usd, tuple(timestamp, block_number)) AS price
            FROM oracle_snapshots FINAL
            WHERE source = 'FLUID' AND status = 'OK'
            GROUP BY subject
            """
        ).result_rows
        return {normalize_address(subject): float(price or 0.0) for subject, price in rows if price}
    except Exception:
        return {}


def build_price_context(ch) -> PriceContext:
    return PriceContext(latest_chainlink_prices(ch), latest_oracle_prices(ch), price_map(ch))


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


def resolve_fluid_token_price(token: str, symbol: str, prices: PriceContext) -> PriceResolution:
    token = normalize_address(token)
    symbol = str(symbol or token_symbol(token))
    price, oracle_status = resolve_fluid_feed_price(symbol, prices.feed_prices)
    if price and price > 0:
        return PriceResolution(float(price), "PRICED", oracle_status or "CHAINLINK")
    snap = prices.oracle_prices.get(token, 0.0)
    if snap > 0:
        return PriceResolution(float(snap), "PRICED", "ORACLE_SNAPSHOT")
    reserve = prices.reserve_prices.get(token, 0.0)
    if reserve > 0 and not symbol:
        return PriceResolution(float(reserve), "PRICED", "FLUID_RESERVE_PRICE")
    reason = "missing Chainlink feed or Fluid oracle snapshot"
    return PriceResolution(0.0, "UNPRICED", "MISSING_PRICE", reason)


def component_row(product_type: str, product_id: str, timestamp: dt.datetime, block_number: int, component_type: str, token: str, raw_amount: int | float | str | None, decimals: int, price: PriceResolution, provenance: dict[str, Any]) -> list:
    token = normalize_address(token)
    raw_int = int(raw_amount or 0)
    amount_usd = token_usd_amount(raw_int, decimals, price.price_usd) if price.pricing_status == "PRICED" else 0.0
    return [
        ETHEREUM_CHAIN_ID,
        product_type,
        normalize_address(product_id),
        timestamp,
        block_number,
        component_type,
        token,
        token_symbol(token),
        str(raw_int),
        int(decimals),
        float(price.price_usd),
        float(amount_usd),
        price.pricing_status,
        price.oracle_status,
        json.dumps(provenance, sort_keys=True),
    ]


def insert_components(ch, rows: list[list]) -> int:
    if not rows:
        return 0
    ch.insert(
        "fluid_product_components",
        rows,
        column_names=[
            "chain_id", "product_type", "product_id", "timestamp", "block_number", "component_type",
            "token", "symbol", "raw_amount", "decimals", "price_usd", "amount_usd",
            "pricing_status", "oracle_status", "provenance",
        ],
    )
    return len(rows)


def component_totals(components: list[list]) -> tuple[float, str, str]:
    total = sum(float(row[11] or 0.0) for row in components)
    nonzero = [row for row in components if int(str(row[8] or "0")) != 0]
    unpriced = [row for row in nonzero if row[12] != "PRICED"]
    if unpriced:
        return 0.0, "UNPRICED", "MISSING_PRICE"
    return total, "PRICED" if nonzero and total > 0 else "UNPRICED", "COMPONENT_PRICES" if total > 0 else "MISSING_EXPOSURE"


def vault_pricing_status(components: list[list], collateral_usd: float, borrow_usd: float) -> tuple[str, str]:
    if not components:
        return "UNPRICED", "MISSING_PRICE"
    nonzero = [row for row in components if int(str(row[8] or "0")) != 0]
    if not nonzero:
        return "PRICED", "NO_EXPOSURE"
    unpriced = [row for row in nonzero if row[12] != "PRICED"]
    if not unpriced and (collateral_usd > 0 or borrow_usd > 0):
        return "PRICED", "COMPONENT_PRICES"
    if any(row[12] == "PRICED" for row in nonzero):
        return "PARTIAL", "PARTIAL_COMPONENT_PRICES"
    return "UNPRICED", "MISSING_PRICE"


def dex_pricing_status(components: list[list], supply_usd: float, borrow_usd: float) -> tuple[str, str]:
    if not components:
        return "UNPRICED", "MISSING_PRICE"
    nonzero = [row for row in components if int(str(row[8] or "0")) != 0]
    if not nonzero:
        return "PRICED", "NO_EXPOSURE"
    unpriced = [row for row in nonzero if row[12] != "PRICED"]
    if not unpriced and (supply_usd > 0 or borrow_usd > 0):
        return "PRICED", "COMPONENT_PRICES"
    return "UNPRICED", "MISSING_PRICE"


def resolve_smart_share_price(
    rpc: RpcClient,
    ch,
    smart_token: str,
    kind: str,
    block_number: int,
    timestamp: dt.datetime,
    prices: PriceContext,
    dex_share_prices: dict[tuple[str, str], PriceResolution],
) -> PriceResolution:
    smart_token = normalize_address(smart_token)
    cached = dex_share_prices.get((smart_token, kind))
    if cached:
        return cached
    snapshot, _components = snapshot_dex(rpc, ch, smart_token, block_number, timestamp, prices)
    derived = build_dex_share_prices(rpc, smart_token, block_number, snapshot)
    dex_share_prices.update(derived)
    return dex_share_prices.get((smart_token, kind), pending_smart_share_price(kind.lower()))



def call_storage_uint(rpc: RpcClient, contract: str, slot: int, block: str | int = "latest") -> tuple[int | None, str]:
    res = rpc.call(contract, SELECTORS["readFromStorage"] + word_uint(slot), block)
    if not res.ok:
        return None, res.error
    try:
        return int(decode_single(res.result, "uint256")), ""
    except Exception as exc:
        return None, str(exc)[:500]


def pending_smart_share_price(kind: str) -> PriceResolution:
    return PriceResolution(0.0, "UNPRICED", "SMART_SHARE_PRICE_PENDING", f"{kind} share valuation pending")


def build_dex_share_prices(rpc: RpcClient, dex: str, block_number: int, snapshot: list) -> dict[tuple[str, str], PriceResolution]:
    dex = normalize_address(dex)
    if str(snapshot[22]) != "PRICED":
        return {}
    supply_usd = float(snapshot[9] or 0.0)
    borrow_usd = float(snapshot[10] or 0.0)
    prices: dict[tuple[str, str], PriceResolution] = {}

    total_supply_shares, supply_err = call_storage_uint(rpc, dex, DEX_TOTAL_SUPPLY_SHARES_SLOT, block_number)
    if total_supply_shares and total_supply_shares > 0 and supply_usd > 0:
        price = supply_usd * (10 ** DEX_SHARE_DECIMALS) / float(total_supply_shares)
        prices[(dex, "COLLATERAL")] = PriceResolution(
            price,
            "PRICED",
            "DEX_COLLATERAL_SHARE",
            f"dex={dex};totalSupplyShares={total_supply_shares};supplyUsd={supply_usd}",
        )
    elif supply_err:
        prices[(dex, "COLLATERAL")] = PriceResolution(0.0, "UNPRICED", "DEX_SHARE_READ_ERROR", supply_err)

    total_borrow_shares, borrow_err = call_storage_uint(rpc, dex, DEX_TOTAL_BORROW_SHARES_SLOT, block_number)
    if total_borrow_shares and total_borrow_shares > 0 and borrow_usd > 0:
        price = borrow_usd * (10 ** DEX_SHARE_DECIMALS) / float(total_borrow_shares)
        prices[(dex, "DEBT")] = PriceResolution(
            price,
            "PRICED",
            "DEX_DEBT_SHARE",
            f"dex={dex};totalBorrowShares={total_borrow_shares};borrowUsd={borrow_usd}",
        )
    elif borrow_err:
        prices[(dex, "DEBT")] = PriceResolution(0.0, "UNPRICED", "DEX_SHARE_READ_ERROR", borrow_err)

    return prices


def decode_vault_packed_totals(vault_variables: int | None, rate_raw: int | None) -> tuple[int, int, int, int]:
    if vault_variables is None:
        return 0, 0, 0, 0
    raw_supply = bigmath((int(vault_variables) >> 82) & ((1 << 64) - 1))
    raw_borrow = bigmath((int(vault_variables) >> 146) & ((1 << 64) - 1))
    supply_ep = ((int(rate_raw or 0) >> 128) & ((1 << 64) - 1)) or int(1e12)
    borrow_ep = ((int(rate_raw or 0) >> 192) & ((1 << 64) - 1)) or int(1e12)
    supply = (raw_supply * supply_ep) // int(1e12)
    borrow = (raw_borrow * borrow_ep) // int(1e12)
    return supply, borrow, supply_ep, borrow_ep


def latest_block_timestamp(ch, block_number: int) -> dt.datetime:
    try:
        row = ch.query(
            f"""
            SELECT max(block_timestamp)
            FROM fluid_events
            WHERE block_number <= {int(block_number)}
            """
        ).result_rows[0]
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def price_map(ch) -> dict[str, float]:
    rows = ch.query(
        """
        SELECT entity_id, argMax(symbol, timestamp) AS symbol, argMax(price_usd, timestamp) AS price
        FROM api_market_latest FINAL
        WHERE protocol = 'FLUID_MARKET'
        GROUP BY entity_id
        """
    ).result_rows
    prices: dict[str, float] = {}
    by_symbol: dict[str, float] = {}
    for token, symbol, price in rows:
        value = float(price or 0.0)
        prices[normalize_address(token)] = value
        by_symbol[str(symbol or "").upper()] = value
    for address, meta in TOKENS.items():
        symbol = str(meta[0]).upper()
        if symbol in by_symbol:
            prices[normalize_address(address)] = by_symbol[symbol]
    if "ETH" in by_symbol:
        prices[normalize_address("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2")] = by_symbol["ETH"]
    return prices


def upsert_contracts(ch, rows: list[list]) -> int:
    if not rows:
        return 0
    ch.insert(
        "fluid_contract_registry",
        rows,
        column_names=["chain_id", "product_type", "contract", "factory", "name", "created_block", "active", "resolver", "metadata"],
    )
    return len(rows)


def insert_snapshots(ch, rows: list[list]) -> int:
    if not rows:
        return 0
    ch.insert(
        "fluid_product_snapshots",
        rows,
        column_names=[
            "chain_id", "product_type", "product_id", "timestamp", "block_number", "symbol", "underlying",
            "collateral_token", "debt_token", "supply_usd", "borrow_usd", "collateral_usd", "liquidity_usd",
            "volume_usd", "fees_usd", "supply_apy", "borrow_apy", "utilization", "ltv", "liquidation_threshold",
            "position_count", "is_canonical_tvl", "pricing_status", "oracle_status", "snapshot_status", "provenance", "error",
        ],
    )
    return len(rows)


def discover_ftokens(rpc: RpcClient, block: str | int) -> tuple[list[str], str]:
    return call_address_array(rpc, FLUID_LENDING_FACTORY, "allTokens", block)


def discover_indexed_contracts(rpc: RpcClient, factory: str, total_sig: str, getter_sig: str, block: str | int, max_count: int | None = None) -> tuple[list[str], str]:
    total, err = call_uint(rpc, factory, total_sig, block)
    if total is None:
        return [], err
    if max_count is not None:
        total = min(total, max_count)
    contracts = []
    errors = []
    for idx in range(1, int(total) + 1):
        contract, err = call_address_arg(rpc, factory, getter_sig, idx, block)
        if contract and contract != ZERO_ADDRESS:
            contracts.append(contract)
        elif err:
            errors.append(f"{idx}:{err}")
    return contracts, "; ".join(errors[:5])


def snapshot_ftoken(rpc: RpcClient, ch, token: str, block_number: int, timestamp: dt.datetime, prices: PriceContext) -> tuple[list, list[list]]:
    symbol, symbol_err = call_string(rpc, token, "symbol", block_number)
    if not symbol:
        symbol = "fToken"
    underlying, underlying_err = call_address(rpc, token, "asset", block_number)
    if not underlying:
        underlying, underlying_err = call_address(rpc, token, "underlyingAsset", block_number)
    decimals, decimals_err = call_uint(rpc, token, "decimals", block_number)
    total_assets, total_assets_err = call_uint(rpc, token, "totalAssets", block_number)
    total_supply, total_supply_err = call_uint(rpc, token, "totalSupply", block_number)
    share_assets, convert_err = (None, "")
    if decimals is not None:
        share_assets, convert_err = call_uint_arg(rpc, token, "convertToAssets", 10 ** int(decimals), block_number)
    underlying_decimals = token_decimals(ch, underlying) if underlying else 18
    price = resolve_fluid_token_price(underlying or "", token_symbol(underlying or ""), prices)
    components: list[list] = []
    if underlying and total_assets is not None:
        components.append(component_row("FTOKEN", token, timestamp, block_number, "UNDERLYING", underlying, total_assets, underlying_decimals, price, {"source": "fToken.totalAssets"}))
    supply_usd, pricing_status, oracle_status = component_totals(components)
    exchange_rate = 0.0
    if share_assets is not None and decimals is not None:
        exchange_rate = float(share_assets) / float(10 ** int(decimals))
    errors = [e for e in [symbol_err, underlying_err, decimals_err, total_assets_err, total_supply_err, convert_err] if e]
    provenance = {
        "source": "rpc",
        "factory": FLUID_LENDING_FACTORY,
        "methods": ["symbol", "asset", "decimals", "totalAssets", "totalSupply", "convertToAssets"],
        "exchangeRateAssetsPerShare": exchange_rate,
        "totalSupplyRaw": str(total_supply or 0),
        "totalAssetsRaw": str(total_assets or 0),
    }
    return [
        ETHEREUM_CHAIN_ID,
        "FTOKEN",
        token,
        timestamp,
        block_number,
        symbol,
        underlying or "",
        "",
        "",
        float(supply_usd),
        0.0,
        0.0,
        float(supply_usd),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0,
        0,
        pricing_status,
        oracle_status,
        "OK" if not errors else "PARTIAL",
        json.dumps(provenance, sort_keys=True),
        "; ".join(errors)[:1000],
    ], components



def snapshot_dex(rpc: RpcClient, ch, dex: str, block_number: int, timestamp: dt.datetime, prices: PriceContext) -> tuple[list, list[list]]:
    constant_type = "(uint256,address,address,(address,address,address,address,address),address,address,address,bytes32,bytes32,bytes32,bytes32,bytes32,bytes32,uint256)"
    constant2_type = "(uint256,uint256,uint256,uint256)"
    constants, constants_err = call_tuple(rpc, dex, "constantsView", constant_type, block_number)
    constants2, constants2_err = call_tuple(rpc, dex, "constantsView2", constant2_type, block_number)
    pex, pex_err = call_prices_and_exchange(rpc, dex, block_number)
    errors = [e for e in [constants_err, constants2_err, pex_err] if e]
    token0 = token1 = ""
    symbol = "DEX"
    supply_usd = borrow_usd = liquidity_usd = utilization = 0.0
    col_reserves = debt_reserves = None
    col_reserves_normalized = debt_reserves_normalized = None
    if constants:
        token0 = normalize_address(constants[5])
        token1 = normalize_address(constants[6])
        symbol = f"{token_symbol(token0)}-{token_symbol(token1)}"
    if pex:
        col_reserves, col_err = call_reserves(
            rpc,
            dex,
            "getCollateralReserves",
            [int(pex[4]), int(pex[2]), int(pex[3]), int(pex[5]), int(pex[7])],
            "(uint256,uint256,uint256,uint256)",
            block_number,
        )
        debt_reserves, debt_err = call_reserves(
            rpc,
            dex,
            "getDebtReserves",
            [int(pex[4]), int(pex[2]), int(pex[3]), int(pex[6]), int(pex[8])],
            "(uint256,uint256,uint256,uint256,uint256,uint256)",
            block_number,
        )
        errors.extend([e for e in [col_err, debt_err] if e])
    price0 = resolve_fluid_token_price(token0, token_symbol(token0), prices)
    price1 = resolve_fluid_token_price(token1, token_symbol(token1), prices)
    has_pair_prices = bool(price0.pricing_status == "PRICED" and price1.pricing_status == "PRICED")
    dec0 = token_decimals(ch, token0)
    dec1 = token_decimals(ch, token1)
    components: list[list] = []
    if constants2 and col_reserves:
        n0, d0, n1, d1 = [int(x) for x in constants2]
        col_reserves_normalized = (
            normalize_dex_amount(col_reserves[0], n0, d0),
            normalize_dex_amount(col_reserves[1], n1, d1),
            normalize_dex_amount(col_reserves[2], n0, d0),
            normalize_dex_amount(col_reserves[3], n1, d1),
        )
        components.append(component_row("DEX", dex, timestamp, block_number, "COLLATERAL_TOKEN0", token0, col_reserves_normalized[0], dec0, price0, {"source": "getCollateralReserves", "leg": "token0"}))
        components.append(component_row("DEX", dex, timestamp, block_number, "COLLATERAL_TOKEN1", token1, col_reserves_normalized[1], dec1, price1, {"source": "getCollateralReserves", "leg": "token1"}))
        if has_pair_prices:
            supply_usd = sum(float(row[11] or 0.0) for row in components if str(row[5]).startswith("COLLATERAL"))
    if constants2 and debt_reserves:
        n0, d0, n1, d1 = [int(x) for x in constants2]
        debt_reserves_normalized = (
            normalize_dex_amount(debt_reserves[0], n0, d0),
            normalize_dex_amount(debt_reserves[1], n1, d1),
            normalize_dex_amount(debt_reserves[2], n0, d0),
            normalize_dex_amount(debt_reserves[3], n1, d1),
            normalize_dex_amount(debt_reserves[4], n0, d0),
            normalize_dex_amount(debt_reserves[5], n1, d1),
        )
        components.append(component_row("DEX", dex, timestamp, block_number, "DEBT_TOKEN0", token0, debt_reserves_normalized[0], dec0, price0, {"source": "getDebtReserves", "leg": "token0"}))
        components.append(component_row("DEX", dex, timestamp, block_number, "DEBT_TOKEN1", token1, debt_reserves_normalized[1], dec1, price1, {"source": "getDebtReserves", "leg": "token1"}))
        if has_pair_prices:
            borrow_usd = sum(float(row[11] or 0.0) for row in components if str(row[5]).startswith("DEBT"))
    liquidity_usd = supply_usd
    if supply_usd > 0:
        utilization = max(0.0, min(1.0, borrow_usd / supply_usd))
    pricing_status, oracle_status = dex_pricing_status(components, supply_usd, borrow_usd)
    no_exposure_storage: dict[str, Any] = {}
    if not components:
        total_supply_shares, supply_share_err = call_storage_uint(rpc, dex, DEX_TOTAL_SUPPLY_SHARES_SLOT, block_number)
        total_borrow_shares, borrow_share_err = call_storage_uint(rpc, dex, DEX_TOTAL_BORROW_SHARES_SLOT, block_number)
        no_exposure_storage = {
            "totalSupplyShares": str(total_supply_shares or 0),
            "totalBorrowShares": str(total_borrow_shares or 0),
            "supplyShareError": supply_share_err,
            "borrowShareError": borrow_share_err,
        }
        if int(total_supply_shares or 0) == 0 and int(total_borrow_shares or 0) == 0:
            pricing_status = "PRICED"
            oracle_status = "NO_EXPOSURE"
    provenance = {
        "source": "rpc",
        "methods": ["constantsView", "constantsView2", "getPricesAndExchangePrices", "getCollateralReserves", "getDebtReserves"],
        "token0": token0,
        "token1": token1,
        "token0PriceUsd": price0.price_usd,
        "token1PriceUsd": price1.price_usd,
        "token0OracleStatus": price0.oracle_status,
        "token1OracleStatus": price1.oracle_status,
        "constantsView2": [str(x) for x in constants2] if constants2 else [],
        "collateralReservesRaw": [str(x) for x in col_reserves] if col_reserves else [],
        "collateralReservesNormalized": [str(x) for x in col_reserves_normalized] if col_reserves_normalized else [],
        "debtReservesRaw": [str(x) for x in debt_reserves] if debt_reserves else [],
        "debtReservesNormalized": [str(x) for x in debt_reserves_normalized] if debt_reserves_normalized else [],
        "noExposureStorage": no_exposure_storage,
    }
    return [
        ETHEREUM_CHAIN_ID,
        "DEX",
        dex,
        timestamp,
        block_number,
        symbol,
        "",
        token0,
        token1,
        float(supply_usd),
        float(borrow_usd),
        0.0,
        float(liquidity_usd),
        0.0,
        0.0,
        0.0,
        0.0,
        float(utilization),
        0.0,
        0.0,
        0,
        0,
        pricing_status,
        oracle_status,
        "OK" if not errors else "PARTIAL",
        json.dumps(provenance, sort_keys=True),
        "; ".join(errors)[:1000],
    ], components


def snapshot_vault_metadata(rpc: RpcClient, ch, vault: str, block_number: int, timestamp: dt.datetime, prices: PriceContext, dex_share_prices: dict[tuple[str, str], PriceResolution] | None = None, error: str = "") -> tuple[list, list[list]]:
    dex_share_prices = dex_share_prices or {}
    vault_type, _type_err = call_uint(rpc, vault, "TYPE", block_number)
    errors = [error]
    symbol = "VAULT"
    collateral_token = ""
    debt_token = ""
    supply_usd = borrow_usd = collateral_usd = utilization = ltv = liquidation_threshold = 0.0
    position_count = 0
    pricing_status = "UNPRICED"
    oracle_status = "RESOLVER_METADATA_ONLY"
    snapshot_status = "METADATA_ONLY"
    components: list[list] = []
    provenance: dict[str, Any] = {"source": "rpc", "factory": FLUID_VAULT_FACTORY, "methods": ["TYPE", "constantsView", "getVaultEntireData"], "resolver": FLUID_VAULT_RESOLVER}

    constants = None
    if vault_type and int(vault_type) > 1:
        constants, constants_err = call_tuple(rpc, vault, "constantsView", VAULT_CONSTANTS_TYPE, block_number)
        if constants_err:
            errors.append(constants_err)
    else:
        t1_type = "(address,address,address,address,address,address,uint8,uint8,uint256,bytes32,bytes32,bytes32,bytes32)"
        t1_constants, constants_err = call_tuple(rpc, vault, "constantsView", t1_type, block_number)
        if constants_err:
            errors.append(constants_err)
        if t1_constants:
            constants = (
                t1_constants[0], t1_constants[1], vault, t1_constants[2], t1_constants[3], ZERO_ADDRESS,
                t1_constants[0], t1_constants[0], (t1_constants[4], ZERO_ADDRESS), (t1_constants[5], ZERO_ADDRESS),
                t1_constants[8], 1, t1_constants[9], t1_constants[10], t1_constants[11], t1_constants[12]
            )

    if constants:
        supply_tokens = [normalize_address(constants[8][0]), normalize_address(constants[8][1])]
        borrow_tokens = [normalize_address(constants[9][0]), normalize_address(constants[9][1])]
        collateral_token = supply_tokens[0]
        debt_token = borrow_tokens[0]
        symbol = f"{token_symbol(supply_tokens[0])}"
        if supply_tokens[1] != ZERO_ADDRESS:
            symbol += f"-{token_symbol(supply_tokens[1])}"
        symbol += f"/{token_symbol(borrow_tokens[0])}"
        if borrow_tokens[1] != ZERO_ADDRESS:
            symbol += f"-{token_symbol(borrow_tokens[1])}"
        provenance.update({
            "vaultType": int(constants[11]),
            "vaultId": int(constants[10]),
            "supply": normalize_address(constants[6]),
            "borrow": normalize_address(constants[7]),
            "supplyTokens": supply_tokens,
            "borrowTokens": borrow_tokens,
            "constantsShape": "T1" if int(constants[11]) == 1 else "T2_T3_T4",
        })

    entire, entire_err = call_tuple_address_arg(rpc, FLUID_VAULT_RESOLVER, "getVaultEntireData", vault, VAULT_ENTIRE_TYPE, block_number)
    if entire_err:
        errors.append(entire_err)
    if entire:
        snapshot_status = "OK"
        is_smart_col = bool(entire[1])
        is_smart_debt = bool(entire[2])
        constants = entire[3]
        configs = entire[4]
        totals = entire[6]
        vault_state = entire[8]
        supply_tokens = [normalize_address(constants[8][0]), normalize_address(constants[8][1])]
        borrow_tokens = [normalize_address(constants[9][0]), normalize_address(constants[9][1])]
        collateral_token = supply_tokens[0]
        debt_token = borrow_tokens[0]
        position_count = int(vault_state[0] or 0)
        ltv = float(configs[2] or 0) / 10000.0
        liquidation_threshold = float(configs[3] or 0) / 10000.0
        provenance.update({
            "isSmartCol": is_smart_col,
            "isSmartDebt": is_smart_debt,
            "oracle": normalize_address(configs[8]),
            "oraclePriceOperate": str(configs[9] or 0),
            "totalSupplyAndBorrowRaw": [str(x) for x in totals],
            "vaultStateRaw": [str(vault_state[0]), str(vault_state[4]), str(vault_state[5])],
        })
        if not is_smart_col and collateral_token:
            dec = token_decimals(ch, collateral_token)
            price = resolve_fluid_token_price(collateral_token, token_symbol(collateral_token), prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "COLLATERAL", collateral_token, int(totals[0] or 0), dec, price, {"source": "getVaultEntireData.totalSupplyVault"}))
        elif is_smart_col and constants:
            smart_token = normalize_address(constants[6])
            share_price = resolve_smart_share_price(rpc, ch, smart_token, "COLLATERAL", block_number, timestamp, prices, dex_share_prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "SMART_COLLATERAL_SHARES", smart_token, int(totals[0] or 0), DEX_SHARE_DECIMALS, share_price, {"source": "getVaultEntireData.totalSupplyVault", "supplyTokens": supply_tokens, "sharePrice": share_price.reason}))
        if not is_smart_debt and debt_token:
            dec = token_decimals(ch, debt_token)
            price = resolve_fluid_token_price(debt_token, token_symbol(debt_token), prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "DEBT", debt_token, int(totals[1] or 0), dec, price, {"source": "getVaultEntireData.totalBorrowVault"}))
        elif is_smart_debt and constants:
            smart_token = normalize_address(constants[7])
            share_price = resolve_smart_share_price(rpc, ch, smart_token, "DEBT", block_number, timestamp, prices, dex_share_prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "SMART_DEBT_SHARES", smart_token, int(totals[1] or 0), DEX_SHARE_DECIMALS, share_price, {"source": "getVaultEntireData.totalBorrowVault", "borrowTokens": borrow_tokens, "sharePrice": share_price.reason}))
        collateral_usd = sum(float(row[11] or 0.0) for row in components if "COLLATERAL" in str(row[5]))
        borrow_usd = sum(float(row[11] or 0.0) for row in components if "DEBT" in str(row[5]))
        supply_usd = collateral_usd
        if collateral_usd > 0:
            utilization = max(0.0, min(10.0, borrow_usd / collateral_usd))
        pricing_status, oracle_status = vault_pricing_status(components, collateral_usd, borrow_usd)

    if not components and constants:
        vault_variables, vars_err = call_storage_uint(rpc, vault, 0, block_number)
        rate_raw, rate_err = call_storage_uint(rpc, vault, 8, block_number)
        if vars_err:
            errors.append(vars_err)
        if rate_err:
            errors.append(rate_err)
        supply_raw, borrow_raw, supply_ep, borrow_ep = decode_vault_packed_totals(vault_variables, rate_raw)
        vault_type_value = int(provenance.get("vaultType", constants[11] if len(constants) > 11 else 1) or 1)
        is_smart_col = vault_type_value in (20000, 40000)
        is_smart_debt = vault_type_value in (30000, 40000)
        provenance.update({"storageFallback": True, "vaultVariablesRaw": str(vault_variables or 0), "rateRaw": str(rate_raw or 0), "vaultSupplyExchangePrice": str(supply_ep), "vaultBorrowExchangePrice": str(borrow_ep)})
        if not is_smart_col and collateral_token:
            dec = token_decimals(ch, collateral_token)
            price = resolve_fluid_token_price(collateral_token, token_symbol(collateral_token), prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "COLLATERAL", collateral_token, supply_raw, dec, price, {"source": "vault.readFromStorage", "slot": 0}))
        elif is_smart_col:
            smart_token = normalize_address(constants[6])
            share_price = resolve_smart_share_price(rpc, ch, smart_token, "COLLATERAL", block_number, timestamp, prices, dex_share_prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "SMART_COLLATERAL_SHARES", smart_token, supply_raw, DEX_SHARE_DECIMALS, share_price, {"source": "vault.readFromStorage", "slot": 0, "sharePrice": share_price.reason}))
        if not is_smart_debt and debt_token:
            dec = token_decimals(ch, debt_token)
            price = resolve_fluid_token_price(debt_token, token_symbol(debt_token), prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "DEBT", debt_token, borrow_raw, dec, price, {"source": "vault.readFromStorage", "slot": 0}))
        elif is_smart_debt:
            smart_token = normalize_address(constants[7])
            share_price = resolve_smart_share_price(rpc, ch, smart_token, "DEBT", block_number, timestamp, prices, dex_share_prices)
            components.append(component_row("VAULT", vault, timestamp, block_number, "SMART_DEBT_SHARES", smart_token, borrow_raw, DEX_SHARE_DECIMALS, share_price, {"source": "vault.readFromStorage", "slot": 0, "sharePrice": share_price.reason}))
        collateral_usd = sum(float(row[11] or 0.0) for row in components if "COLLATERAL" in str(row[5]))
        borrow_usd = sum(float(row[11] or 0.0) for row in components if "DEBT" in str(row[5]))
        supply_usd = collateral_usd
        if collateral_usd > 0:
            utilization = max(0.0, min(10.0, borrow_usd / collateral_usd))
        pricing_status, oracle_status = vault_pricing_status(components, collateral_usd, borrow_usd)
        snapshot_status = "OK" if not errors else "PARTIAL"

    clean_errors = [e for e in errors if e]
    if clean_errors and snapshot_status == "OK":
        snapshot_status = "PARTIAL"
    return [
        ETHEREUM_CHAIN_ID,
        "VAULT",
        vault,
        timestamp,
        block_number,
        symbol,
        "",
        collateral_token,
        debt_token,
        float(supply_usd),
        float(borrow_usd),
        float(collateral_usd),
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        float(utilization),
        float(ltv),
        float(liquidation_threshold),
        int(position_count),
        0,
        pricing_status,
        oracle_status,
        snapshot_status if (collateral_token or debt_token) else "PARTIAL",
        json.dumps(provenance, sort_keys=True),
        "; ".join(clean_errors)[:1000],
    ], components

def discovery_snapshot(product_type: str, contract: str, factory: str, block_number: int, timestamp: dt.datetime, error: str = "") -> list:
    return [
        ETHEREUM_CHAIN_ID,
        product_type,
        contract,
        timestamp,
        block_number,
        product_type,
        "",
        "",
        "",
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0,
        0,
        "UNPRICED",
        "RESOLVER_ADAPTER_PENDING",
        "DISCOVERED_ONLY",
        json.dumps({"source": "rpc", "factory": factory, "note": "aggregate resolver adapter pending"}, sort_keys=True),
        error[:1000],
    ]



def snapshot_revenue(rpc: RpcClient, ch, block_number: int, timestamp: dt.datetime, prices: PriceContext) -> tuple[list, list[list]]:
    res = rpc.call(FLUID_REVENUE_RESOLVER, SELECTORS["getRevenues"], block_number)
    components: list[list] = []
    errors: list[str] = []
    if not res.ok:
        errors.append(res.error)
    else:
        try:
            revenues = abi_decode(["(address,uint256)[]"], bytes.fromhex(res.result[2:]))[0]
            for token, amount in revenues:
                token = normalize_address(token)
                dec = token_decimals(ch, token)
                price = resolve_fluid_token_price(token, token_symbol(token), prices)
                components.append(component_row("REVENUE", FLUID_REVENUE_RESOLVER, timestamp, block_number, "REVENUE", token, int(amount or 0), dec, price, {"source": "RevenueResolver.getRevenues"}))
        except Exception as exc:
            errors.append(str(exc)[:500])
    fees_usd, pricing_status, oracle_status = component_totals(components)
    provenance = {"source": "rpc", "resolver": FLUID_REVENUE_RESOLVER, "methods": ["getRevenues"], "componentCount": len(components)}
    return [
        ETHEREUM_CHAIN_ID, "REVENUE", FLUID_REVENUE_RESOLVER, timestamp, block_number, "Fluid Revenue", "", "", "",
        0.0, 0.0, 0.0, 0.0, 0.0, float(fees_usd), 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0,
        pricing_status, oracle_status, "OK" if not errors else "PARTIAL", json.dumps(provenance, sort_keys=True), "; ".join(errors)[:1000]
    ], components


def snapshot_steth(block_number: int, timestamp: dt.datetime) -> tuple[list, list[list]]:
    provenance = {"source": "registry", "resolver": FLUID_STETH_RESOLVER, "note": "verified Ethereum stETH resolver address not configured"}
    return [
        ETHEREUM_CHAIN_ID, "STETH", FLUID_STETH_RESOLVER, timestamp, block_number, "Fluid stETH", "", "", "",
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0,
        "UNPRICED", "RESOLVER_NOT_CONFIGURED", "DISCOVERED_ONLY", json.dumps(provenance, sort_keys=True), "verified Ethereum stETH resolver address not configured"
    ], []


def snapshot_fluid_oracles(rpc: RpcClient, ch, block_number: int, timestamp: dt.datetime, prices: PriceContext) -> int:
    try:
        rows = ch.query(
            """
            SELECT asset, argMax(symbol, updated_at) AS latest_symbol
            FROM fluid_asset_oracle_support FINAL
            WHERE oracle_support = 'ORACLE_SNAPSHOT_REQUIRED'
               OR upper(toString(symbol)) IN ('GHO', 'USDTB', 'USR', 'METH', 'EZETH', 'RSETH', 'DEUSD', 'EBTC', 'OSETH', 'WEETHS')
            GROUP BY asset
            """
        ).result_rows
    except Exception:
        rows = []
    snapshots: list[OracleSnapshot] = []
    for asset, symbol in rows:
        asset = normalize_address(asset)
        symbol = str(symbol or token_symbol(asset))
        existing = prices.oracle_prices.get(asset, 0.0)
        if existing > 0:
            continue
        feed_price, feed_status = resolve_fluid_feed_price(symbol, prices.feed_prices)
        if feed_price > 0:
            snapshots.append(OracleSnapshot(ETHEREUM_CHAIN_ID, "FLUID", "CHAINLINK_COMPOSED", asset, asset, feed_status, block_number, timestamp, "1", "1", float(feed_price), "OK", ""))
            prices.oracle_prices[asset] = float(feed_price)
            continue
        proxy_price, proxy_err, proxy_meta = resolve_chainlink_proxy_price(rpc, symbol, prices, block_number)
        if proxy_price > 0:
            snapshots.append(OracleSnapshot(ETHEREUM_CHAIN_ID, "FLUID", "CHAINLINK_PROXY", asset, proxy_meta.get("proxy", asset), proxy_meta.get("feed", "latestRoundData"), block_number, timestamp, proxy_meta.get("answer", "0"), str(10 ** int(proxy_meta.get("decimals", 0))), float(proxy_price), "OK", json.dumps(proxy_meta, sort_keys=True)))
            prices.oracle_prices[asset] = float(proxy_price)
            continue

        rate_price, rate_err, rate_meta = resolve_rate_provider_price(rpc, symbol, prices, block_number)
        if rate_price > 0:
            snapshots.append(OracleSnapshot(ETHEREUM_CHAIN_ID, "FLUID", "RATE_PROVIDER", asset, rate_meta.get("contract", asset), rate_meta.get("feed", "getRate"), block_number, timestamp, rate_meta.get("rateRaw", "0"), str(10 ** int(rate_meta.get("decimals", 0))), float(rate_price), "OK", json.dumps(rate_meta, sort_keys=True)))
            prices.oracle_prices[asset] = float(rate_price)
            continue

        decimals, dec_err = call_uint(rpc, asset, "decimals", block_number)
        underlying, asset_err = call_address(rpc, asset, "asset", block_number)
        if not underlying:
            underlying, asset_err = call_address(rpc, asset, "underlyingAsset", block_number)
        if decimals is not None and underlying:
            shares = 10 ** int(decimals)
            assets, conv_err = call_uint_arg(rpc, asset, "convertToAssets", shares, block_number)
            base = resolve_fluid_token_price(underlying, token_symbol(underlying), prices)
            if assets is not None and base.pricing_status == "PRICED":
                underlying_decimals = token_decimals(ch, underlying)
                rate = float(assets) / float(10 ** underlying_decimals)
                price_usd = rate * base.price_usd
                snapshots.append(OracleSnapshot(ETHEREUM_CHAIN_ID, "FLUID", "ERC4626", asset, asset, "convertToAssets", block_number, timestamp, str(assets), str(10 ** underlying_decimals), float(price_usd), "OK", ""))
                prices.oracle_prices[asset] = float(price_usd)
                continue
            error = conv_err or base.reason or proxy_err or rate_err or "missing underlying price"
        else:
            error = asset_err or dec_err or proxy_err or rate_err or "asset()/convertToAssets() unsupported"
        snapshots.append(OracleSnapshot(ETHEREUM_CHAIN_ID, "FLUID", "UNSUPPORTED", asset, asset, "erc4626_probe", block_number, timestamp, "0", "0", 0.0, "ERROR", error[:1000]))
    return insert_oracle_snapshots(ch, snapshots)


def write_validation_run(ch, started_at: dt.datetime, finished_at: dt.datetime) -> None:
    details: dict[str, Any] = {}
    unpriced_nonzero = ch.query(
        """
        SELECT count()
        FROM (
            SELECT
                product_type,
                product_id,
                argMax(pricing_status, tuple(timestamp, block_number)) AS pricing_status,
                argMax(supply_usd, tuple(timestamp, block_number)) AS supply_usd,
                argMax(borrow_usd, tuple(timestamp, block_number)) AS borrow_usd,
                argMax(collateral_usd, tuple(timestamp, block_number)) AS collateral_usd,
                argMax(liquidity_usd, tuple(timestamp, block_number)) AS liquidity_usd,
                argMax(fees_usd, tuple(timestamp, block_number)) AS fees_usd
            FROM fluid_product_snapshots FINAL
            GROUP BY product_type, product_id
        )
        WHERE pricing_status = 'UNPRICED' AND (supply_usd != 0 OR borrow_usd != 0 OR collateral_usd != 0 OR liquidity_usd != 0 OR fees_usd != 0)
        """
    ).result_rows[0][0]
    component_mismatches = ch.query(
        """
        SELECT count()
        FROM (
            SELECT product_type, product_id, timestamp, block_number, sum(amount_usd) AS component_usd
            FROM fluid_product_components FINAL
            GROUP BY product_type, product_id, timestamp, block_number
        ) c
        INNER JOIN fluid_product_snapshots AS s FINAL USING(product_type, product_id, timestamp, block_number)
        WHERE s.pricing_status = 'PRICED' AND abs((s.supply_usd + s.borrow_usd + s.fees_usd) - c.component_usd) > greatest(1.0, c.component_usd * 0.02)
        """
    ).result_rows[0][0]
    mismatch_count = int(unpriced_nonzero or 0) + int(component_mismatches or 0)
    details["unpricedNonzeroRows"] = int(unpriced_nonzero or 0)
    details["componentMismatchRows"] = int(component_mismatches or 0)
    ch.insert(
        "fluid_rpc_validation_runs",
        [[f"fluid-full-{uuid4().hex}", ETHEREUM_CHAIN_ID, "FULL_COVERAGE", started_at, finished_at, 2, mismatch_count, 0.0, 0.0, "OK" if mismatch_count == 0 else "WARN", json.dumps(details, sort_keys=True)]],
        column_names=["run_id", "chain_id", "target", "started_at", "finished_at", "checked_count", "mismatch_count", "max_relative_supply_diff", "max_relative_borrow_diff", "status", "details"],
    )


def run(args) -> int:
    apply_env_from_config(args.config)
    rpc_url = args.rpc_url or os.getenv("MAINNET_RPC_URL", "").strip()
    if not rpc_url:
        raise SystemExit("MAINNET_RPC_URL is required")
    ch = clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
    )
    try:
        ensure_schema(ch)
        ensure_fluid_full_coverage_tables(ch)
        ensure_oracle_snapshot_tables(ch)
        seed_core_fluid_contracts(ch)
        rpc = RpcClient(rpc_url, timeout_sec=args.http_timeout_sec, retries=args.retries)
        block_number = int(args.block_number or rpc.block_number())
        timestamp = latest_block_timestamp(ch, block_number)
        prices = build_price_context(ch)
        started_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        oracle_snapshots = 0 if (args.skip_oracles or args.dry_run) else snapshot_fluid_oracles(rpc, ch, block_number, timestamp, prices)
        prices = build_price_context(ch)

        ftokens, ftoken_err = discover_ftokens(rpc, block_number)
        vaults, vault_err = discover_indexed_contracts(rpc, FLUID_VAULT_FACTORY, "totalVaults", "getVaultAddress", block_number, args.max_contracts)
        dexes, dex_err = discover_indexed_contracts(rpc, FLUID_DEX_FACTORY, "totalDexes", "getDexAddress", block_number, args.max_contracts)

        contract_rows = []
        snapshot_rows = []
        component_rows = []
        dex_share_prices: dict[tuple[str, str], PriceResolution] = {}
        for token in ftokens:
            contract_rows.append([ETHEREUM_CHAIN_ID, "FTOKEN", token, FLUID_LENDING_FACTORY, "Fluid fToken", 0, 1, "", "discovered_by=allTokens"])
            snapshot, components = snapshot_ftoken(rpc, ch, token, block_number, timestamp, prices)
            snapshot_rows.append(snapshot)
            component_rows.extend(components)
        for dex in dexes:
            contract_rows.append([ETHEREUM_CHAIN_ID, "DEX", dex, FLUID_DEX_FACTORY, "Fluid DEX", 0, 1, "", "discovered_by=getDexAddress"])
            snapshot, components = snapshot_dex(rpc, ch, dex, block_number, timestamp, prices)
            snapshot_rows.append(snapshot)
            component_rows.extend(components)
            dex_share_prices.update(build_dex_share_prices(rpc, dex, block_number, snapshot))
        for vault in vaults:
            contract_rows.append([ETHEREUM_CHAIN_ID, "VAULT", vault, FLUID_VAULT_FACTORY, "Fluid Vault", 0, 1, FLUID_VAULT_RESOLVER, "discovered_by=getVaultAddress"])
            snapshot, components = snapshot_vault_metadata(rpc, ch, vault, block_number, timestamp, prices, dex_share_prices, vault_err)
            snapshot_rows.append(snapshot)
            component_rows.extend(components)
        revenue_snapshot, revenue_components = snapshot_revenue(rpc, ch, block_number, timestamp, prices)
        snapshot_rows.append(revenue_snapshot)
        component_rows.extend(revenue_components)
        steth_snapshot, steth_components = snapshot_steth(block_number, timestamp)
        snapshot_rows.append(steth_snapshot)
        component_rows.extend(steth_components)

        if args.dry_run:
            print(json.dumps({"block": block_number, "ftokens": len(ftokens), "vaults": len(vaults), "dexes": len(dexes), "snapshots": len(snapshot_rows), "components": len(component_rows), "oracleSnapshots": oracle_snapshots, "errors": {"ftokens": ftoken_err, "vaults": vault_err, "dexes": dex_err}}, indent=2))
            return 0
        upsert_contracts(ch, contract_rows)
        insert_snapshots(ch, snapshot_rows)
        insert_components(ch, component_rows)
        finished_at = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        if not args.skip_validation:
            write_validation_run(ch, started_at, finished_at)
        print(json.dumps({"block": block_number, "snapshots": len(snapshot_rows), "components": len(component_rows), "contracts": len(contract_rows), "oracleSnapshots": oracle_snapshots, "ftokens": len(ftokens), "vaults": len(vaults), "dexes": len(dexes)}, indent=2))
        return 0
    finally:
        ch.close()


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default=None)
    parser.add_argument("--rpc-url", default=None)
    parser.add_argument("--block-number", type=int, default=None)
    parser.add_argument("--max-contracts", type=int, default=None)
    parser.add_argument("--http-timeout-sec", type=int, default=60)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-oracles", action="store_true")
    parser.add_argument("--skip-validation", action="store_true")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill Fluid product discovery and snapshots")
    add_args(parser)
    raise SystemExit(run(parser.parse_args()))
