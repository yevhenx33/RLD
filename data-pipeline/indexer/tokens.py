"""
Shared token metadata — single source of truth for all protocol sources.

Token address mappings, asset classification, and price helpers used
by Aave, Morpho, Fluid, and Chainlink sources.
"""

# ── Token address → (symbol, decimals) ─────────────────────
# Used by Fluid (ADDR_MAP), Morpho (KNOWN_TOKENS), Aave (RESERVE_MAP)
# Addresses are LOWERCASE, WITHOUT 0x prefix.
TOKENS = {
    # Stablecoins
    "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": ("USDC", 6),
    "dac17f958d2ee523a2206206994597c13d831ec7": ("USDT", 6),
    "6b175474e89094c44da98b954eedeac495271d0f": ("DAI", 18),
    "4c9edd5852cd905f086c759e8383e09bff1e68b3": ("USDe", 18),
    "c139190f447e929f090edeb554d95abb8b18ac1c": ("USDtb", 18),
    "9d39a5de30e57443bff2a8307a4256c8797a3497": ("sUSDe", 18),
    "40d16fc0246ad3160ccc09b8d0d3a2cd28ae6c2f": ("GHO", 18),
    "66a1e37c9b0eaddca17d3662d6c05f4decf3e110": ("USR", 18),
    "085780639cc2cacd35e474e71f4d000e2405d8f6": ("fxUSD", 18),
    "73a15fed60bf67631dc6cd7bc5b6e8da8190acf5": ("USD0", 18),
    "b40b6608b2743e691c9b54ddbdee7bf03cd79f1c": ("USD0pp", 18),
    "15700b564ca08d9439c58ca5053166e8317aa138": ("deUSD", 18),
    "48f9e38f3070ad8945dfeae3fa70987722e3d89c": ("iUSD", 18),
    "a3931d71877c0e7a3148cb7eb4463524fec27fbd": ("sUSDS", 18),
    "dc035d45d973e3ec169d2276ddab16f1e407384f": ("USDS", 18),
    "3d7d6fdf07ee548b939a80edbc9b2256d0cdc003": ("srUSDe", 18),
    "c58d044404d8b14e953c115e67823784dea53d8f": ("jrUSDe", 18),
    "5086bf358635b81d8c47c66d1c8b9e567db70c72": ("reUSD", 18),
    "beefc011e94f43b8b7b455ebab290c7ab4e216f1": ("csUSDL", 18),
    "1202f5c7b4b9e47a1a484e8b270be34dbbc75055": ("wstUSR", 18),
    "80ac24aa929eaf5013f6436cda2a7ba190f5cc0b": ("syrupUSDC", 6),
    "356b8d89c1e1239cbbb9de4815c39a1474d5ba7d": ("syrupUSDT", 6),
    "6c3ea9036406852006290770bedfcaba0e23a0e8": ("PYUSD", 6),
    # Additional Morpho-listed stablecoins
    "e42f72e1c12f56e34a5e4ee3820af94b4e1ad533": ("RLUSD", 18),
    "c76a3cba4d77223d53e3a7aa5b3b2e13ff33ee0e": ("EURCV", 18),
    "c139190f447e929f090edeb554d95abb8b18ac1c": ("USDTB", 18),
    "3456a06eb13286b0354899e76d42a57c20b4e2e7": ("MSUSD", 18),
    "09b6413b87c7c22ca6f33281e24f977e34d6fe2e": ("RUSD", 18),
    "84f7cf3bfe0a3f88b1ab58d26e8f6d5b3a1c7bef": ("FRXUSD", 18),
    "dab1c73e2db1f8d1598b3c7c7292e2b37d0c6e8c": ("ZCHF", 18),
    "a4c5a443eeb0e2e7d6dab1e0e3b6f5c41e8a9e32": ("LVLUSD", 18),
    "83f20f44975d03b1b09e64809b757c47f942beea": ("sDAI", 18),
    "a35b1b31ce002fbf2058d22f30f95d405200a15b": ("sFRAX", 18),
    "5f98805a4e8be255a32880fdec7f6728c6568ba0": ("LUSD", 18),
    "853d955acef822db058eb8505911ed77f175b99e": ("FRAX", 18),
    "f939e0a03fb07f59a73314e73794be0e57ac1b4e": ("crvUSD", 18),
    "1a7e4e63778b4f12a199c062f3efdd288afcbce8": ("agEUR", 18),
    "57f5e098cad7a3d1eed53991d4d66c45c9af7812": ("wUSDM", 18),
    "defe616913fa88a5af0c5fc6a5e0d25e89ea5471": ("Paxos", 18),
    "e72b141df173b999ae7c1adcbf60cc9833ce56a8": ("EURV", 18),
    "1c7d4b196cb0c7b01d743fbc6116a902379c7238": ("EURC", 6),
    # ETH derivatives
    "c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": ("WETH", 18),
    "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": ("ETH", 18),
    "7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": ("wstETH", 18),
    "ae78736cd615f374d3085123a210448e74fc6393": ("rETH", 18),
    "be9895146f7af43049ca1c1ae358b0541ea49704": ("cbETH", 18),
    "cd5fe23c85820f7b72d0926fc9b05b43e359b7ee": ("weETH", 18),
    "917cee801a67f933f2e6b33fc0cd1ed2d5909d88": ("weETHs", 18),
    "bf5495efe5db9ce00f80364c8b423567e58d2110": ("ezETH", 18),
    "a1290d69c65a6fe4df752f95823fae25cb99e5a7": ("rsETH", 18),
    "f1c9acdc66974dfb6decb12aa385b9cd01190e38": ("osETH", 18),
    "d5f7838f5c461feff7fe49ea5ebaf7728bb0adfa": ("mETH", 18),
    "ae7ab96520de3a18e5e111b5eaab095312d7fe84": ("stETH", 18),
    "35fa164735182de50811e8e2e824cfb9b6118ac2": ("eETH", 18),
    "5e8422345238f34275888049021821e8e08caa1f": ("frxETH", 18),
    "ac3e018457b222d93114458476f3e3416abbe38f": ("sfrxETH", 18),
    "edfa23602d0ec14714057867a78d01e94176bea0": ("osETH_aave", 18),  # Aave-listed osETH
    "5c7e299cf531eb66f2a1df637d37abb78e6200c7": ("WOETH", 18),
    "7122985931b4d0b1aa7cc69dc3e466fc6c7bca44": ("KSETH", 18),
    "8c1bed5b9a0928467c9b1341da1d7bd5e10b6549": ("lsETH", 18),
    "2416092f143378750bb29b79ed961ab195cceea5": ("ezSOL", 9),
    # BTC derivatives
    "2260fac5e5542a773aa44fbcfedf7c193bc2c599": ("WBTC", 8),
    "cbb7c0000ab88b473b1f5afd9ef808440eed33bf": ("cbBTC", 8),
    "8236a87084f8b84306f72007f36f2618a5634494": ("LBTC", 8),
    "18084fba666a33d37592fa2633fd49a74dd93a88": ("tBTC", 18),
    "657e8c867d8b37dcc18fa4caead9c45eb088c642": ("eBTC", 8),
    # Gold
    "45804880de22913dafe09f4980848ece6ecbaf78": ("PAXG", 18),
    "68749665ff8d2d112fa859aa293f07a622782f38": ("XAUt", 6),
    # Governance / other
    "514910771af9ca656af840dff83e8264ecf986ca": ("LINK", 18),
    "7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": ("AAVE", 18),
    "9f8f72aa9304c8b593d555f12ef6589cc3a579a2": ("MKR", 18),
    "1f9840a85d5af5bf1d1762f925bdaddc4201f984": ("UNI", 18),
    "c011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f": ("SNX", 18),
    "ba100000625a3754423978a60c9317c58a424e3d": ("BAL", 18),
    "d533a949740bb3306d119cc777fa900ba034cd52": ("CRV", 18),
    "530824da86689c9c17cdc2871ff29b058345b44a": ("STKAAVE", 18),
    "a663b02cf0a4b149d2ad41910cb81e23e1c41c32": ("sFRX", 18),
    "6f40d4a6237c257fff2db00fa0510deeecd303eb": ("FLUID", 18),
    "4956b52ae2ff65d74ca2d61207523288e4528f96": ("RLP", 18),
}

# ── Asset classification ───────────────────────────────────
STABLES = {
    "USDC", "USDT", "DAI", "USDe", "USDtb", "GHO", "USR", "fxUSD", "USD0",
    "USD0pp", "deUSD", "iUSD", "sUSDS", "USDS", "srUSDe", "jrUSDe", "reUSD",
    "csUSDL", "wstUSR", "syrupUSDC", "syrupUSDT", "PYUSD", "sDAI", "sFRAX",
    "LUSD", "FRAX", "crvUSD", "wUSDM", "Paxos",
    "sUSDe",
    # Additional Morpho stablecoins (uppercase from morpho_market_params)
    "RLUSD", "USDTB", "MSUSD", "RUSD", "FRXUSD", "LVLUSD",
    "USDE", "APXUSD", "AUSD", "EUSD", "MUSD", "PMUSD", "USDCV", "USDF",
    "USDQ", "USDR", "USDU",
    # Uppercase aliases for case-insensitive lookup
    "SYRUPUSDC", "SYRUPUSDT",
}

ETH_ASSETS = {
    "WETH", "ETH", "wstETH", "rETH", "cbETH", "weETH", "weETHs", "ezETH",
    "rsETH", "osETH", "osETH_aave", "mETH", "stETH", "eETH", "frxETH",
    "sfrxETH", "WOETH", "KSETH", "lsETH",
    # Uppercase aliases
    "WSTETH", "WEETH", "MSETH",
}

BTC_ASSETS = {"WBTC", "cbBTC", "LBTC", "tBTC", "eBTC", "CBBTC"}

# Fluid-specific: approximate exchange rates vs base asset for LSDs
PRICE_MULTIPLIERS = {
    "ETH": 1.000, "WETH": 1.000, "wstETH": 1.230, "weETH": 1.050,
    "weETHs": 1.050, "rsETH": 1.040, "ezETH": 1.020, "osETH": 1.010,
    "mETH": 1.040,
    "WBTC": 1.000, "cbBTC": 1.000, "LBTC": 1.000, "tBTC": 1.000,
    "eBTC": 1.000,
}

# ── Build reverse lookup: symbol → decimals ────────────────
SYM_DECIMALS: dict[str, int] = {}
for _addr, (_sym, _dec) in TOKENS.items():
    SYM_DECIMALS[_sym] = _dec
    SYM_DECIMALS[_sym.upper()] = _dec  # uppercase alias
# Explicit overrides for tokens not in TOKENS but in Morpho markets
SYM_DECIMALS.setdefault("RLUSD", 18)
SYM_DECIMALS.setdefault("EURCV", 18)
SYM_DECIMALS.setdefault("USDTB", 18)
SYM_DECIMALS.setdefault("MSUSD", 18)
SYM_DECIMALS.setdefault("RUSD", 18)
SYM_DECIMALS.setdefault("FRXUSD", 18)
SYM_DECIMALS.setdefault("ZCHF", 18)
SYM_DECIMALS.setdefault("LVLUSD", 18)
SYM_DECIMALS.setdefault("APXUSD", 18)
SYM_DECIMALS.setdefault("MSETH", 18)


# ── Price helpers ──────────────────────────────────────────
# Feed names match Chainlink contract description() exactly (spaces around /)
def get_usd_price(symbol: str, eth_price: float = 2000.0,
                  btc_price: float = 70000.0,
                  extra_prices: dict[str, float] | None = None) -> float:
    """Get USD price for a token symbol using Chainlink oracle feeds.

    extra_prices keys use Chainlink description() format: 'ETH / USD', etc.
    """
    ep = extra_prices or {}

    if symbol in STABLES:
        if symbol in ("USDC", "syrupUSDC", "SYRUPUSDC"):
            return ep.get("USDC / USD", 1.0)
        if symbol in ("USDT", "syrupUSDT", "SYRUPUSDT"):
            return ep.get("USDT / USD", ep.get("FRAX / USD", 1.0))
        if symbol in ("DAI", "sDAI"):
            return ep.get("DAI / USD", 1.0)
        if symbol in ("PYUSD",):
            return ep.get("PYUSD / USD", 1.0)
        if symbol in ("USDe", "USDE", "sUSDe", "SUSDE"):
            return ep.get("USDe / USD", 1.0)
        if symbol in ("USDS", "sUSDS", "SUSDS"):
            return ep.get("USDS / USD", 1.0)
        if symbol in ("USD0", "USD0pp"):
            return ep.get("USD0 / USD", ep.get("USD0++ / USD", 1.0))
        if symbol in ("RLUSD",):
            return ep.get("RLUSD / USD", 1.0)
        return 1.0

    if symbol in ETH_ASSETS:
        if symbol in ("wstETH", "WSTETH"):
            # Use STETH / USD feed directly if available, else compose
            steth_usd = ep.get("STETH / USD")
            if steth_usd:
                wst_ratio = ep.get("wstETH/stETH exchange rate", 1.18)
                return steth_usd * wst_ratio
            # Fallback to multiplier
            return eth_price * PRICE_MULTIPLIERS.get("wstETH", 1.23)
        if symbol in ("stETH", "STETH"):
            return ep.get("STETH / USD", ep.get("STETH / ETH", 1.0) * eth_price)
        if symbol in ("weETH", "WEETH"):
            weeth_eth = ep.get("weETH / ETH", ep.get("weETH/ETH exchange rate", 1.05))
            return eth_price * weeth_eth
        if symbol in ("rETH", "RETH"):
            reth_eth = ep.get("RETH / ETH", 1.10)
            return eth_price * reth_eth
        mult = PRICE_MULTIPLIERS.get(symbol, 1.0)
        return eth_price * mult

    if symbol in BTC_ASSETS:
        if symbol in ("cbBTC", "CBBTC"):
            return ep.get("cbBTC / USD", btc_price)
        if symbol in ("WBTC",):
            wbtc_btc = ep.get("WBTC / BTC", 1.0)
            return btc_price * wbtc_btc
        if symbol in ("LBTC",):
            lbtc_btc = ep.get("LBTC / BTC", 1.0)
            return btc_price * lbtc_btc
        if symbol in ("tBTC", "TBTC"):
            return ep.get("TBTC / USD", btc_price)
        return btc_price

    if symbol in ("PAXG", "XAUt"):
        return ep.get("PAXG / USD", ep.get("XAU / USD", 3300.0))

    # Governance tokens with dedicated feeds
    if symbol in ("LINK", "link"):
        return ep.get("LINK / USD", 15.0)
    if symbol in ("UNI", "uni"):
        return ep.get("UNI / USD", 3.0)
    if symbol in ("MKR", "SKY"):
        return ep.get("MKR / USD", ep.get("SKY / USD", 1500.0))
    if symbol in ("LDO",):
        ldo_eth = ep.get("LDO / ETH", 0.0005)
        return eth_price * ldo_eth
    if symbol in ("EIGEN",):
        return ep.get("EIGEN / USD", 2.0)

    # EUR / CHF pegged
    if symbol in ("EURV", "EURCV", "agEUR", "EURC"):
        return ep.get("EUR / USD", ep.get("EURC / USD", 1.08))
    if symbol in ("ZCHF",):
        return ep.get("CHF / USD", 1.10)

    if symbol == "FLUID":
        return 0.50
    return 1.0  # Unknown — assume $1


def get_chainlink_prices(ch) -> tuple[float, float]:
    """Fetch latest ETH/USD and BTC/USD from chainlink_prices table."""
    eth_price = 2000.0
    btc_price = 70000.0
    try:
        ep = ch.command(
            "SELECT argMax(price, timestamp) FROM chainlink_prices "
            "WHERE feed = 'ETH / USD'"
        )
        if ep:
            eth_price = float(ep)
        bp = ch.command(
            "SELECT argMax(price, timestamp) FROM chainlink_prices "
            "WHERE feed = 'BTC / USD'"
        )
        if bp:
            btc_price = float(bp)
    except Exception:
        pass
    return eth_price, btc_price
