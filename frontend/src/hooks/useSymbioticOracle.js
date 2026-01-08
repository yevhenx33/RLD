import useSWR from "swr";
import axios from "axios";

// --- CONFIGURATION ---
const RPC_URL = "http://127.0.0.1:8545"; // Local Anvil Node

import addresses from "../../../shared/addresses.json";
const CONTRACT_ADDRESS = addresses.SymbioticRateOracle;

// Event Signature: TwarUpdated(uint256,uint256,uint256)
const EVENT_TOPIC =
  "0x9430f137bf43acb4b9ff47618de5ad3de34064da2ed2401931e9d5c047d55ba8";

const hexToBigInt = (hex) => {
  if (!hex) return 0n;
  return BigInt(hex);
};

const fetcher = async () => {
  // Debug Log
  console.log(`[Symbiotic] Fetching logs from ${CONTRACT_ADDRESS}...`);

  const payload = {
    jsonrpc: "2.0",
    id: 1,
    method: "eth_getLogs",
    params: [
      {
        address: CONTRACT_ADDRESS,
        fromBlock: "earliest",
        toBlock: "latest",
        topics: [EVENT_TOPIC],
      },
    ],
  };

  try {
    const res = await axios.post(RPC_URL, payload);

    // Check for RPC Errors
    if (res.data.error) {
      console.error("[Symbiotic] RPC Error:", res.data.error);
      throw new Error(res.data.error.message);
    }

    const logs = res.data.result;
    console.log(`[Symbiotic] Found ${logs.length} logs`);

    if (!logs || logs.length === 0) return [];

    // Decode Logs
    const formattedData = logs.map((log) => {
      // 1. Timestamp (Indexed -> Topic[1])
      const timestampBig = hexToBigInt(log.topics[1]);
      const timestamp = Number(timestampBig);

      // 2. Data (Twar + Diff)
      // Remove 0x, split into 32-byte chunks (64 hex chars)
      const dataRaw = log.data.replace("0x", "");
      const twarHex = "0x" + dataRaw.substring(0, 64);
      // const diffHex = "0x" + dataRaw.substring(64, 128); // Not used yet

      const twarBig = hexToBigInt(twarHex);
      const twar = Number(twarBig) / 1e18;

      return {
        timestamp,
        twar,
        value: twar,
      };
    });

    return formattedData.sort((a, b) => a.timestamp - b.timestamp);
  } catch (err) {
    console.error("[Symbiotic] Fetch Failed:", err);
    return []; // Return empty to prevent crash
  }
};

export function useSymbioticOracle() {
  const { data, error } = useSWR("symbiotic-oracle-data", fetcher, {
    refreshInterval: 5000, // Poll faster (5s) for local testing
    dedupingInterval: 2000,
  });

  return {
    data: data || [],
    isLoading: !error && !data,
    isError: error,
  };
}
