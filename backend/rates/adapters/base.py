"""
Abstract base class for protocol adapters.

Each lending protocol (Aave V3, Fluid, Euler) implements this
interface to define how to:
  1. Build eth_call payloads for a given block
  2. Decode RPC responses into (symbol, apy) records
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RateRecord:
    """A single rate data point from a protocol."""
    symbol: str
    apy: float


class ProtocolAdapter(ABC):
    """Base class for lending protocol rate adapters.

    To add a new protocol:
      1. Create rates/adapters/{protocol}.py
      2. Subclass ProtocolAdapter
      3. Name the class 'Adapter' (convention for auto-discovery)
      4. Implement build_rpc_calls() and decode_results()
    """

    def __init__(self, config: dict):
        """Initialize with protocol config from PROTOCOLS registry.

        Args:
            config: Protocol config dict containing pool_address, assets, etc.
        """
        self.config = config
        self.name = config["name"]
        self.pool_address = config.get("pool_address")
        self.assets = config.get("assets", {})

    @abstractmethod
    def build_rpc_calls(self, block_hex: str) -> list[dict]:
        """Build JSON-RPC eth_call payloads for a single block.

        Args:
            block_hex: Block number as hex string (e.g. '0x1234')

        Returns:
            List of JSON-RPC request dicts. Each must have an 'id'
            field that will be used to match responses in decode_results().
        """
        ...

    @abstractmethod
    def decode_results(self, results: list[dict]) -> list[RateRecord]:
        """Decode RPC responses into rate records.

        Args:
            results: JSON-RPC response dicts matching the calls from
                     build_rpc_calls(). Matched by 'id' field.

        Returns:
            List of RateRecord(symbol, apy) for each successfully decoded asset.
        """
        ...

    def get_call_count_per_block(self) -> int:
        """Number of RPC calls this adapter adds per block.

        Used by the daemon to calculate batch sizes and assign IDs.
        Default: one call per asset.
        """
        return len(self.assets)
