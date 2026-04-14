"""
ChainlinkSource — Chainlink AnswerUpdated price feed decoder.

Indexes price updates from 76 Chainlink aggregator contracts (covering 67
unique feeds) into the chainlink_prices table. Addresses and feed names
were discovered by reading BASE_FEED_1/2 and QUOTE_FEED_1/2 from all 1,004
Morpho oracle contracts, then resolving each proxy's aggregator() and
description() via batch RPC.
"""

import datetime
import logging
from typing import Optional

from ..base import BaseSource

log = logging.getLogger("indexer.chainlink")

# Aggregator implementation address → feed name (from proxy description())
# 76 addresses, 67 unique feeds
AGGREGATORS = {
    "0x05581918dad3f026169593863f7a52bbbe08ef5e": "USDF / USD",
    "0x056339c044055819e8db84e71f5f2e1f536b2e5b": "mTBILL/USD",
    "0x0b539d864c16398dcc7353521c62186380de6b56": "USDT / ETH",
    "0x0d5f4aadf3fde31bbb55db5f42c080f18ad54df5": "USDT / USD",
    "0x0e3dd634ffbf7ea89bbdcf09ccc463302fd5f903": "XAU / USD",
    "0x0f316f6b0c2e2ebe3c3a8b23f6c61009238d51fd": "weETH / ETH",
    "0x19219bc90f48dee4d5cf202e09c438faacfd8bea": "Redstone Price Feed",
    "0x1e726556244d772d1d50cacb19b87e7205fa509e": "CBETH / ETH",
    "0x2053257478ba1fedf7f99def0c412006753ac9bf": "SPYon-USD (Calculated)",
    "0x26ae9b951f84e6c28f58a92133c30e312d42e0fe": "USDC / ETH",
    "0x26f196806f43e88fd27798c9e3fb8fdf4618240f": "STETH / USD",
    "0x320e22c489e4bb634ac1aa5822543014a6fbb292": "QQQon-USD (Calculated)",
    "0x3359921992c33ef23169193a6c91f2944a82517c": "mHyperBTC/BTC",
    "0x39e31761911b9aabaef5fb81b18fd1c24a60e884": "PYUSD / USD",
    "0x3b9c09bde7776c32c518e2e787412a9bbaa7685f": "Redstone Price Feed",
    "0x3f6047b77131ce78ab4775fee2d38b7339471a01": "LBTC / BTC",
    "0x42c8cb3565254006efe97d60edd2093d8f4ba35e": "solvBTC / BTC",
    "0x43881b05c3be68b2d33eb70addf9f666c5005f68": "mHYPER/USD",
    "0x4a3411ac2948b33c69666b35cc6d055b27ea84f1": "BTC / USD",
    "0x525b031c1ee01502c113500a2d1a999cd3f9c98f": "YFI / USD",
    "0x5763fc5fabca9080ad12bcafae7a335023b1f9b4": "CHF / USD",
    "0x592700e4fcdd674dc54d2681ded3b63f54f63f9a": "USDS / USD",
    "0x5b4728ba4f1a210b3545959a4e0fb6c3a16fe8f7": "EIGEN / USD",
    "0x5c00518d3d423ec59d553af123be8a63b11078cf": "USCC NAV",
    "0x5c81ee2c3ee8aaac2eef68ecb512472d9e08a0fd": "mHyperETH/ETH",
    "0x5e2420cace3650622f62b2713b2b3727fc8bcdd1": "cbBTC / USD",
    "0x5f09aff8b9b1f488b7d1bbad4d89648579e55d61": "mMEV/USD",
    "0x62a897c3e81d809c7444bb63d7d51e1f2ebb6c3d": "frxUSD / USD",
    "0x6418bb052fbb827a6022f4ec3f2d6a20444304ec": "SKY / USD",
    "0x6795d4a47c9c8f4117b409d966259cdcf6a9eb6e": "PAXG / USD",
    "0x698da5d987a71b68ebf30c1555cfd38f190406b7": "mEDGE/USD",
    "0x6d68a0636246d1de3ebe972ad8bee886b10610ee": "MKR / ETH",
    "0x6f3f8d82694d52e6b6171a7b26a88c9554e7999b": "BTC / USD",
    "0x709783ab12b65fd6cd948214eee6448f3bdd72a3": "DAI / USD",
    "0x725609ae7d540a7985d7fd189e155db9d72c1d44": "STRC / USD",
    "0x76a495b0bffb53ef3f0e94ef0763e03ce410835c": "Redstone Price Feed",
    "0x7a5dc0c6a59e76b3a65c73224316c110663ced1b": "ETH / BTC",
    "0x7d06199061da586dafc5d18fd1aeeaf18ae7593b": "USDC / USD",
    "0x7d4e742018fb52e48b08be73d041c18b21de6fb5": "ETH / USD",
    "0x7d95b7bf7bb7750d818f42df114739b6c88cf9bc": "RLUSD / USD",
    "0x82cd6b814cf9cc8e4164480f7e1347ca38bcb4fa": "LsETH / ETH Exchange Rate",
    "0x84303e5568c7b167fa4febc6253cddfe12b7ee4b": "mAPOLLO/USD",
    "0x84e32ab7a70be2be619ebcb06d2c725f8b7fb839": "DAI / ETH",
    "0x8f73090a7c58b8bdcc9a93cbb6816e5cc4f01e8c": "FRAX / USD",
    "0x90fb891e7ee51972f7d9309a6b812d04ed2643c7": "USD0 / USD",
    "0x94ac91b209043162e6761942563a9f1f8dd75209": "SolvBTC.BBN / SolvBTC Exchange Rate",
    "0x95dc7c293ad1706c80bcde068b609ca61b3ff78c": "TSLAon / USD (Ondo API)",
    "0x966dad3b93c207a9ee3a79c336145e013c5cd3fc": "EUR / USD",
    "0x96d6e33b411dc1f4e3f1e894a5a5d9ce0f96738d": "LINK / USD",
    "0x9ddb5fba9a737860c7cced0d9177af56ab16c183": "SPYon / USD (Ondo API)",
    "0xa0b5260bdfd1011c4bcdc7a099c75bff6340b38c": "JPY / USD",
    "0xa5e3a55cea42b86560a5215094981c300899199d": "WBTC / BTC",
    "0xa674a0fd742f37bd5077afc90d1e82485c91989c": "EURC / USD",
    "0xa736eae8805ddeffba40cab8c99bcb309deabd9b": "Redstone Price Feed",
    "0xad88fc1a810379ef4efbf2d97ede57e306178e5a": "ETH / USD",
    "0xb0fd105dad6b9b07f36d5f8496712a36114279ad": "BTC / ETH",
    "0xc1c24f0f2103f5899b7ab415a1930e519b7d3423": "USTBL NAV",
    "0xc3990f01cdf334df305335bf2f4a5bae9d52b6f5": "SAVUSD / AVUSD Exchange Rate",
    "0xc77904cd2ca0806cc3db0819e9630ff3e2f6093d": "RETH / ETH",
    "0xc9c8efa84eab332d1950e5ba0a913b090775825c": "STETH / ETH",
    "0xc9e1a09622afdb659913fefe800feae5dbbfe9d7": "USDC / USD",
    "0xcc16f670129f965b396f2e81312f6e339ffdb18e": "USDe / USD",
    "0xd7496378523f90f1e82da528f385b2b30120afe2": "USDG / USD",
    "0xdaa1c6511aa051e9e83dd7ac2d65d5e41d1f6b98": "EUTBL NAV",
    "0xdae05e337c56cd1b988fd7a6b74e8bbd3028c4c6": "TBTC / USD",
    "0xddb6f90ffb4d3257dd666b69178e5b3c5bf41136": "Redstone Price Feed",
    "0xdef8c51d7c1040637a198effc39613865b32ea51": "UNI / USD",
    "0xe13fafe4fb769e0f4a1cb69d35d21ef99188eff7": "USDC / USD",
    "0xe4f2ae539442e1d3fb40f03ceebf4a372a390d24": "mBASIS/USD",
    "0xe660b4dc23430bdf2ec30b961fcaf6ccac8276a3": "QQQon / USD (Ondo API)",
    "0xe6c7ae04e83aa7e491988caeecf5bd6a240a0d14": "IB01 / USD",
    "0xe9a6bccde4875f8c1228975f9c84598558a75ac8": "MKR / USD",
    "0xf3a0a2363ee3e5fc1ccf923f4ea9c06bac1a6834": "CRVUSD / USD",
    "0xf4a3e183f59d2599ee3df213ff78b1b3b1923696": "Redstone Price Feed",
    "0xf816091ba795c1b55859599fcda8f786b2816e01": "USD0++ / USD",
    "0xfc7b00a255f24c979ba96135e11b58bd6f693ec4": "LDO / ETH",
}

# Feeds with non-standard decimals (default is 8)
FEED_DECIMALS = {
    "BTC / ETH": 18,
    "CBETH / ETH": 18,
    "DAI / ETH": 18,
    "EUTBL NAV": 6,
    "LDO / ETH": 18,
    "LsETH / ETH Exchange Rate": 18,
    "MKR / ETH": 18,
    "RETH / ETH": 18,
    "SAVUSD / AVUSD Exchange Rate": 18,
    "STETH / ETH": 18,
    "SolvBTC.BBN / SolvBTC Exchange Rate": 18,
    "USCC NAV": 6,
    "USDC / ETH": 18,
    "USDT / ETH": 18,
    "USTBL NAV": 6,
    "weETH / ETH": 18,
}


class ChainlinkSource(BaseSource):
    name = "CHAINLINK"
    contracts = list(AGGREGATORS.keys())
    topics = [
        "0x0559884fd3a460db3073b7fc896cc77986f16e378210ded43186175bf646fc5f",  # AnswerUpdated
    ]
    raw_table = "chainlink_events"  # Prices go directly to chainlink_prices

    def get_cursor(self, ch) -> int:
        """Track cursor via chainlink_prices table."""
        result = ch.command("SELECT max(block_number) FROM chainlink_prices")
        return int(result) if result else 0

    def decode(self, log_entry, block_ts_map) -> Optional[dict]:
        """Decode AnswerUpdated: price from topic1, timestamp from data."""
        topics = log_entry.topics or []
        if len(topics) < 2:
            return None

        addr = (log_entry.address or "").lower()
        feed_name = AGGREGATORS.get(addr)
        if not feed_name:
            return None

        # topic1 = indexed int256 price
        price_raw = int(topics[1], 16)
        if price_raw > (1 << 255):
            price_raw -= (1 << 256)

        # Use feed-specific decimals (default 8)
        decimals = FEED_DECIMALS.get(feed_name, 8)
        price = price_raw / (10 ** decimals)

        if price <= 0:
            return None

        # data = updatedAt (uint256 unix timestamp)
        data = log_entry.data or "0x"
        updated_at = int(data, 16) if data != "0x" and len(data) > 2 else 0
        ts = (datetime.datetime.fromtimestamp(updated_at, tz=datetime.UTC)
              if updated_at > 0
              else datetime.datetime.now(datetime.UTC))

        return {
            "block_number": log_entry.block_number,
            "timestamp": ts.replace(tzinfo=None),
            "feed": feed_name,
            "price": price,
        }

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        """Insert decoded prices into chainlink_prices table."""
        if not decoded_rows:
            return 0

        rows = [
            [d["block_number"], d["timestamp"], d["feed"], d["price"]]
            for d in decoded_rows
        ]
        ch.insert("chainlink_prices", rows,
                   column_names=["block_number", "timestamp", "feed", "price"])
        return len(rows)
