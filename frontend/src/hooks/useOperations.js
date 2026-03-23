import { useState, useEffect, useCallback, useRef } from "react";
import { ethers } from "ethers";
import { rpcProvider } from "../utils/provider";

// BrokerRouter event ABIs
const EVENTS_ABI = [
  "event LongExecuted(address indexed broker, uint256 amountIn, uint256 amountOut)",
  "event LongClosed(address indexed broker, uint256 amountIn, uint256 amountOut)",
  "event ShortExecuted(address indexed broker, uint256 debtAmount, uint256 proceeds)",
  "event ShortClosed(address indexed broker, uint256 debtRepaid, uint256 collateralSpent)",
  "event Deposited(address indexed broker, uint256 underlyingAmount, uint256 wrappedAmount)",
];

const IFACE = new ethers.Interface(EVENTS_ABI);

// Pre-compute topic hashes
const EVENT_TOPICS = {
  LongExecuted: IFACE.getEvent("LongExecuted").topicHash,
  LongClosed: IFACE.getEvent("LongClosed").topicHash,
  ShortExecuted: IFACE.getEvent("ShortExecuted").topicHash,
  ShortClosed: IFACE.getEvent("ShortClosed").topicHash,
  Deposited: IFACE.getEvent("Deposited").topicHash,
};

const OP_META = {
  LongExecuted: { label: "OPEN_LONG", color: "text-green-400 bg-green-500/20" },
  LongClosed: { label: "CLOSE_LONG", color: "text-pink-400 bg-pink-500/20" },
  ShortExecuted: {
    label: "OPEN_SHORT",
    color: "text-orange-400 bg-orange-500/20",
  },
  ShortClosed: {
    label: "CLOSE_SHORT",
    color: "text-yellow-400 bg-yellow-500/20",
  },
  Deposited: { label: "DEPOSIT", color: "text-cyan-400 bg-cyan-500/20" },
};

/**
 * Hook that fetches all BrokerRouter operations for a specific broker address
 * by querying on-chain logs. Polls every `pollInterval` ms.
 *
 * Works by querying ALL events from the router, then filtering client-side
 * by broker address topic. This is the most reliable approach.
 */
export function useOperations(
  routerAddress,
  brokerAddress,
  pollInterval = 10000,
) {
  const [operations, setOperations] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const mountedRef = useRef(true);
  const initialLoadDone = useRef(false);

  const fetchOps = useCallback(async () => {
    if (!routerAddress) {
      setOperations([]);
      setLoaded(true);
      return;
    }

    try {
      if (!initialLoadDone.current) setLoading(true);
      const provider = rpcProvider;

      // Build topic filter: any of our 4 event types
      const eventTopics = Object.values(EVENT_TOPICS);

      // Pad broker address to 32-byte topic if filtering by broker
      const brokerTopic = brokerAddress
        ? "0x" + brokerAddress.slice(2).toLowerCase().padStart(64, "0")
        : null;

      // Query logs from the router contract
      const filter = {
        address: routerAddress,
        fromBlock: 0,
        toBlock: "latest",
        topics: [
          eventTopics, // topic0: any of our event signatures
          brokerTopic ? brokerTopic : null, // topic1: broker address (or null for all)
        ],
      };

      const logs = await provider.getLogs(filter);

      // Parse and enrich with block timestamps
      const allOps = [];
      // Batch-fetch unique block numbers for timestamps
      const blockNums = [...new Set(logs.map((l) => l.blockNumber))];
      const blockMap = {};
      await Promise.all(
        blockNums.map(async (bn) => {
          try {
            const block = await provider.getBlock(bn);
            blockMap[bn] = block?.timestamp || 0;
          } catch {
            blockMap[bn] = 0;
          }
        }),
      );

      for (const log of logs) {
        try {
          const parsed = IFACE.parseLog({ topics: log.topics, data: log.data });
          if (!parsed) continue;

          const meta = OP_META[parsed.name];
          if (!meta) continue;

          allOps.push({
            id: `${log.transactionHash}-${log.index}`,
            type: parsed.name,
            label: meta.label,
            color: meta.color,
            args: parsed.args,
            blockNumber: log.blockNumber,
            txHash: log.transactionHash,
            timestamp: blockMap[log.blockNumber] || 0,
          });
        } catch {
          // skip unparsable logs
        }
      }

      // Sort newest first
      allOps.sort((a, b) => b.blockNumber - a.blockNumber);

      if (mountedRef.current) {
        setOperations(allOps);
        initialLoadDone.current = true;
        setLoaded(true);
      }
    } catch (e) {
      console.warn("useOperations fetch failed:", e);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  }, [routerAddress, brokerAddress]);

  // Fetch on mount + poll
  useEffect(() => {
    mountedRef.current = true;
    fetchOps();
    const interval = setInterval(fetchOps, pollInterval);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
  }, [fetchOps, pollInterval]);

  return { operations, loading, loaded, refetch: fetchOps };
}

/**
 * Format an amount from 6-decimal BigInt to human-readable string
 */
export function formatOpAmount(raw) {
  const num = Number(raw) / 1e6;
  if (num >= 1e6) return `${(num / 1e6).toFixed(1)}M`;
  if (num >= 1e3) return `${(num / 1e3).toFixed(1)}K`;
  return num.toFixed(2);
}
