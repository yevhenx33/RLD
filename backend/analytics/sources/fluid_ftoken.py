"""Fluid fToken raw event source collected through HyperSync/Envio."""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from ..base import BaseSource, insert_rows_batched
from ..fluid_full_coverage import ETHEREUM_CHAIN_ID, FLUID_LENDING_FACTORY, ensure_fluid_full_coverage_tables
from ..protocols import FLUID_FTOKEN

log = logging.getLogger("indexer.fluid_ftoken")

FTOKEN_GENESIS_BLOCK = 19_258_464
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_DEPOSIT = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
TOPIC_WITHDRAW = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"
TOPIC_LOG_UPDATE_RATES = "0x9dd85e9767d796973b86c6ccf3a294429cfd5e3e93fa23ac388b9277bb8283fd"
EVENT_NAMES = {
    TOPIC_TRANSFER: "Transfer",
    TOPIC_DEPOSIT: "Deposit",
    TOPIC_WITHDRAW: "Withdraw",
    TOPIC_LOG_UPDATE_RATES: "LogUpdateRates",
}


def _normalize_address(value: str | None) -> str:
    raw = str(value or "").lower().removeprefix("0x")
    return "0x" + raw[-40:].rjust(40, "0")


class FluidFTokenSource(BaseSource):
    """Collect fToken ERC20/ERC4626 raw logs via HyperSync.

    Processing/replay is handled by ``fluid-ftoken-replay`` so this source only
    provides canonical raw event ingestion into ``fluid_product_raw_events``.
    """

    name = FLUID_FTOKEN
    contracts: list[str] = []
    topics = [TOPIC_TRANSFER, TOPIC_DEPOSIT, TOPIC_WITHDRAW, TOPIC_LOG_UPDATE_RATES]
    raw_table = "fluid_product_raw_events"
    genesis_block = FTOKEN_GENESIS_BLOCK

    def _ensure_tables(self, ch) -> None:
        ensure_fluid_full_coverage_tables(ch)

    def _load_contracts(self, ch) -> None:
        self._ensure_tables(ch)
        rows = ch.query(
            """
            SELECT contract
            FROM fluid_contract_registry FINAL
            WHERE product_type = %(product_type)s
              AND name = %(name)s
              AND active = 1
            """,
            parameters={"product_type": "FTOKEN", "name": "Fluid fToken"},
        ).result_rows
        contracts = [_normalize_address(str(row[0])) for row in rows if row and row[0]]
        if not contracts:
            # Static fallback to the seven currently discoverable Fluid fTokens.
            contracts = [
                "0x9fb7b4477576fe5b32be4c1843afb1e55f251b33",  # fUSDC
                "0x90551c1795392094fe6d29b758eccd233cfaa260",  # fWETH
                "0x5c20b550819128074fd538edf79791733ccedd18",  # fUSDT
                "0x2411802d8bea09be0af8fd8d08314a63e706b29c",  # fwstETH
                "0x6a29a46e21c730dca1d8b23d637c101cec605c5b",  # fGHO
                "0x2bbe31d63e6813e3ac858c04dae43fb2a72b0d11",  # fsUSDS
                "0x15e8c742614b5d8db4083a41df1a14f5d2bfb400",  # fUSDtb
            ]
        self.contracts = sorted(set(contracts))

    def get_cursor(self, ch) -> int:
        self._load_contracts(ch)
        rows = ch.query(
            "SELECT max(block_number) FROM fluid_product_raw_events WHERE product_type = %(product_type)s",
            parameters={"product_type": "FTOKEN"},
        ).result_rows
        result = rows[0][0] if rows else 0
        return int(result) if result else 0

    def route(self, log_entry) -> bool:
        addr = _normalize_address(getattr(log_entry, "address", None))
        return addr in {contract.lower() for contract in self.contracts}

    def _event_name(self, log_entry) -> str:
        topics = [str(topic).lower() for topic in (getattr(log_entry, "topics", None) or [])]
        return EVENT_NAMES.get(topics[0], "") if topics else ""

    def insert_raw(self, ch, logs: list, block_ts_map: dict) -> int:
        if not logs:
            return 0
        rows = []
        for entry in logs:
            topics = [str(topic).lower() for topic in (entry.topics or [])]
            ts = block_ts_map.get(entry.block_number, datetime.datetime.now(datetime.UTC))
            ts_naive = ts.replace(tzinfo=None) if getattr(ts, "tzinfo", None) else ts
            topic0 = topics[0] if topics else ""
            rows.append([
                ETHEREUM_CHAIN_ID,
                "FTOKEN",
                int(entry.block_number),
                ts_naive,
                str(entry.transaction_hash or "").lower(),
                int(entry.log_index or 0),
                _normalize_address(entry.address),
                EVENT_NAMES.get(topic0, ""),
                topic0,
                topics[1] if len(topics) > 1 else None,
                topics[2] if len(topics) > 2 else None,
                topics[3] if len(topics) > 3 else None,
                str(entry.data or "0x"),
            ])
        return insert_rows_batched(
            ch,
            self.raw_table,
            rows,
            [
                "chain_id", "product_type", "block_number", "block_timestamp",
                "tx_hash", "log_index", "contract", "event_name", "topic0",
                "topic1", "topic2", "topic3", "data",
            ],
        )

    def decode(self, log_entry, block_ts_map: dict) -> Optional[dict]:
        return None

    def merge(self, ch, decoded_rows: list[dict]) -> int:
        return 0
