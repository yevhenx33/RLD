import useSWR from "swr";
import axios from "axios";

// --- CONFIGURATION ---
const RPC_URL = "http://127.0.0.1:8545"; // Local Anvil Node
// Ensure this matches your deployment
const CONTRACT_ADDRESS = "0x0b4868Dbdfb981a4b26eE5d670739Bc03b16cd9c";

// Event Signature: TwarUpdated(uint256,uint256,uint256)
const EVENT_TOPIC =
  "0x9430f137bf43acb4b9ff47618de5ad3de34064da2ed2401931e9d5c047d55ba8";

const hexToBigInt = (hex) => {
  if (!hex) return 0n;
  return BigInt(hex);
};

const fetcher = async () => {
  const payload = {
    jsonrpc: "2.0",
    id: 1,
    method: "eth_getLogs",
    params: [
      {
        address: CONTRACT_ADDRESS,
        fromBlock: "0x0", // Start from genesis for history
        toBlock: "latest",
        topics: [EVENT_TOPIC],
      },
    ],
  };

  try {
    const res = await axios.post(RPC_URL, payload);
    if (res.data.error) throw new Error(res.data.error.message);

    const logs = res.data.result;
    if (!logs || logs.length === 0) return [];

    const formattedData = logs.map((log) => {
      // 1. Timestamp (Indexed -> Topic[1])
      const timestampBig = hexToBigInt(log.topics[1]);
      const timestamp = Number(timestampBig);

      // 2. Data (Twar + Diff)
      const dataRaw = log.data.replace("0x", "");
      const twarHex = "0x" + dataRaw.substring(0, 64);

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
    console.error("Symbiotic Oracle Stream Error:", err);
    return [];
  }
};

export function useSymbioticOracle() {
  const { data, error } = useSWR("symbiotic-oracle-data", fetcher, {
    refreshInterval: 5000,
    dedupingInterval: 2000,
  });

  return {
    data: data || [],
    isLoading: !error && !data,
    isError: error,
  };
}
