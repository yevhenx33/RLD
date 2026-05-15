"""Shared Fluid asset, oracle, and product registry.

Fluid reserve pricing, product snapshots, and support classification must agree
on which assets require explicit on-chain snapshots. Keep that policy here so a
symbol cannot be priced one way in the reserve worker and another way in the
snapshot worker.
"""

from __future__ import annotations

ETHEREUM_CHAIN_ID = 1

FLUID_LIQUIDITY = "0x52aa899454998be5b000ad077a46bbe360f4e497"
FLUID_LENDING_FACTORY = "0x54b91a0d94cb471f37f949c60f7fa7935b551d03"
FLUID_VAULT_FACTORY = "0x324c5dc1fc42c7a4d43d92df1eba58a54d13bf2d"
FLUID_DEX_FACTORY = "0x91716c4eda1fb55e84bf8b4c7085f84285c19085"
FLUID_VAULT_RESOLVER = "0x814c8c7ceb1411b364c2940c4b9380e739e06686"
FLUID_DEX_RESOLVER = "0x71783f64719899319b56bda4f27e1219d9af9a3d"
FLUID_REVENUE_RESOLVER = "0xfe4affad55c7aec012346195654634f7c786fa2c"
FLUID_STETH_RESOLVER = ""

MULTICALL3 = "0xca11bde05977b3631167028862be2a173976ca11"

FLUID_PRODUCTS = ("LIQUIDITY", "FTOKEN", "VAULT", "DEX", "REVENUE", "STETH")
FLUID_PRODUCT_PROTOCOLS = ("FLUID_FTOKEN", "FLUID_VAULT", "FLUID_DEX", "FLUID_REVENUE", "FLUID_STETH")

BTC_DERIVATIVE_SYMBOLS = {"LBTC", "EBTC", "TBTC", "FBTC", "BTC.B"}

FLUID_WRAPPER_ORACLE_HINTS = {
    "GHO": "GHO / USD proxy feed",
    "SUSDE": "ERC4626/Ethena share rate + USDe / USD",
    "USR": "Resolv/USR oracle or peg governance source",
    "WSTUSR": "wstUSR wrapper share rate + USR price",
    "XAUT": "XAUt / USD or gold oracle feed",
    "FXUSD": "fxUSD oracle or peg governance source",
    "DEUSD": "deUSD oracle or peg governance source",
    "USDTB": "USDTB oracle or peg governance source",
    "IUSD": "iUSD oracle or peg governance source",
    "REUSD": "Re Protocol reUSD Fluid vault oracle",
    "SUSDS": "Savings USDS share rate + USDS / USD",
    "SYRUPUSDC": "Maple syrupUSDC exchange rate + USDC / USD",
    "SRUSDE": "Ethena srUSDe share rate + USDe / USD",
    "CSUSDL": "Concrete/Veda accountant + USDC/USD base",
    "SYRUPUSDT": "Maple syrupUSDT exchange rate + USDT / USD",
    "JRUSDE": "Ethena jrUSDe share rate + USDe / USD",
    "FLUID": "FLUID token market oracle",
    "RLP": "RLP price provider",
    "EZETH": "Renzo ezETH / ETH oracle + ETH / USD",
    "METH": "Mantle mETH / ETH oracle + ETH / USD",
    "OSETH": "StakeWise osETH / ETH oracle + ETH / USD",
    "RSETH": "Kelp rsETH / ETH oracle + ETH / USD",
    "WEETHS": "weETHs wrapper rate + ETH / USD",
    "LBTC": "Lombard BTC derivative oracle + BTC / USD",
    "EBTC": "Ether.fi eBTC oracle + BTC / USD",
    "TBTC": "Threshold tBTC oracle + BTC / USD",
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

FLUID_VAULT_ORACLE_FEEDS = {
    "REUSD": [
        {
            "oracle": "0x60f6c752bbee95ebb5964dd3fa9991b2917eeeca",
            "vault": "0x9b3207736dbf0a04d292071b814821032152605a",
            "feed": "USDC per 1 REUSD",
            "quote": "USDC",
        },
        {
            "oracle": "0xe0615826c36f1063cb47972ae1b9f4d0541c887a",
            "vault": "0xbbe29582232cf6450d71a3b53535ce58035fabe2",
            "feed": "USDT per 1 REUSD",
            "quote": "USDT",
        },
        {
            "oracle": "0x49487e69c0d35f55464d7734e1e42b4c34a7d3f7",
            "vault": "0x767dd0dec9f68bb85028708066337a758e06ad7b",
            "feed": "GHO per 1 REUSD",
            "quote": "GHO",
        },
    ],
}

INTENTIONAL_UNPRICED_SYMBOLS = {}

SNAPSHOT_REQUIRED_SYMBOLS = tuple(sorted(set(FLUID_WRAPPER_ORACLE_HINTS) | BTC_DERIVATIVE_SYMBOLS))


def fluid_symbol_key(symbol: str | None) -> str:
    return str(symbol or "").strip().upper()


def support_hint(symbol: str) -> str:
    return FLUID_WRAPPER_ORACLE_HINTS.get(fluid_symbol_key(symbol), "")


def needs_explicit_snapshot(symbol: str) -> bool:
    key = fluid_symbol_key(symbol)
    return key in BTC_DERIVATIVE_SYMBOLS or bool(FLUID_WRAPPER_ORACLE_HINTS.get(key))
