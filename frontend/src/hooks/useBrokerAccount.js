import { useState, useCallback, useEffect } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";
import { rpcProvider } from "../utils/provider";

// ── Minimal ABIs ───────────────────────────────────────────────────
const FACTORY_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
  "function createBroker(bytes32 salt) returns (address broker)",
  "event BrokerCreated(address indexed broker, address indexed owner, uint256 tokenId)",
];

const ERC20_ABI = [
  "function transfer(address to, uint256 amount) returns (bool)",
  "function balanceOf(address owner) view returns (uint256)",
];



async function _sendImpersonatedTx(from, to, data) {
  const res = await fetch(RPC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: "eth_sendTransaction",
      params: [{ from, to, data, gas: "0x7A1200" }],
      id: Date.now(),
    }),
  });
  const json = await res.json();
  if (json.error) throw new Error(`TX failed: ${json.error.message}`);
  return json.result;
}

async function _waitForTx(txHash, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const res = await fetch(RPC_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        method: "eth_getTransactionReceipt",
        params: [txHash],
        id: Date.now(),
      }),
    });
    const json = await res.json();
    if (json.result && json.result.status) {
      if (json.result.status === "0x1") return json.result;
      throw new Error(`TX reverted: ${txHash}`);
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`TX timeout: ${txHash}`);
}

// ── Hook ───────────────────────────────────────────────────────────

/**
 * Hook for broker account lifecycle: check, create, deposit.
 *
 * @param {string} account            Connected wallet address
 * @param {string} brokerFactoryAddr  From indexer /api/market-info
 * @param {string} waUsdcAddr         From indexer /api/market-info
 */
export function useBrokerAccount(account, brokerFactoryAddr, waUsdcAddr) {
  const [hasBroker, setHasBroker] = useState(null); // null=loading
  const [brokerAddress, setBrokerAddress] = useState(null);
  const [brokerBalance, setBrokerBalance] = useState(null); // waUSDC balance of broker
  const [creating, setCreating] = useState(false);
  const [depositing, setDepositing] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState(""); // status text

  // ── Fetch broker's waUSDC balance ──────────────────────────────
  const fetchBrokerBalance = useCallback(
    async (brokerAddr) => {
      if (!brokerAddr || !waUsdcAddr) return;
      try {
        const provider = rpcProvider;
        const token = new ethers.Contract(waUsdcAddr, ERC20_ABI, provider);
        const bal = await token.balanceOf(brokerAddr);
        // waUSDC has 6 decimals
        setBrokerBalance(ethers.formatUnits(bal, 6));
      } catch (e) {
        console.warn("Failed to fetch broker balance:", e);
      }
    },
    [waUsdcAddr],
  );

  // ── Check broker ownership & resolve address ────────────────────
  const checkBroker = useCallback(async () => {
    if (!account || !brokerFactoryAddr) {
      setHasBroker(null);
      setBrokerAddress(null);
      return;
    }
    try {
      const provider = rpcProvider;
      const factory = new ethers.Contract(
        brokerFactoryAddr,
        FACTORY_ABI,
        provider,
      );
      const balance = await factory.balanceOf(account);

      if (Number(balance) > 0) {
        // Resolve broker contract address from BrokerCreated events
        // BrokerCreated(address indexed broker, address indexed owner, uint256 tokenId)
        const filter = factory.filters.BrokerCreated(null, account);
        const events = await factory.queryFilter(filter, 0, "latest");
        if (events.length > 0) {
          // Use the most recent broker
          const latestEvent = events[events.length - 1];
          const addr = latestEvent.args.broker;
          setBrokerAddress(addr);
          fetchBrokerBalance(addr);
        }
        setHasBroker(true);
      } else {
        setHasBroker(false);
        setBrokerAddress(null);
        setBrokerBalance(null);
      }
    } catch (e) {
      console.warn("Broker check failed:", e);
      setHasBroker(null);
    }
  }, [account, brokerFactoryAddr, fetchBrokerBalance]);

  useEffect(() => {
    checkBroker();
  }, [checkBroker]);

  // ── Auto-refresh broker balance every 12s ──────────────────────
  useEffect(() => {
    if (!brokerAddress || !waUsdcAddr) return;
    const interval = setInterval(() => {
      fetchBrokerBalance(brokerAddress);
    }, 12_000);
    return () => clearInterval(interval);
  }, [brokerAddress, waUsdcAddr, fetchBrokerBalance]);

  // ── Create broker via MetaMask signing ──────────────────────────
  const createBroker = useCallback(async () => {
    if (!account || !brokerFactoryAddr) return;
    if (!window.ethereum) {
      setError("MetaMask not found");
      return;
    }

    setCreating(true);
    setError(null);
    setStep("Preparing transaction...");

    try {
      // Get MetaMask signer (handles Anvil chainId sync)
      setStep("Syncing chain ID...");
      const signer = await getAnvilSigner();

      const factory = new ethers.Contract(
        brokerFactoryAddr,
        FACTORY_ABI,
        signer,
      );

      // Generate deterministic salt from address + timestamp
      const salt = ethers.keccak256(
        ethers.solidityPacked(
          ["address", "uint256"],
          [account, BigInt(Date.now())],
        ),
      );

      setStep("Confirm in wallet...");
      const tx = await factory.createBroker(salt, { gasLimit: 8_000_000 });

      setStep("Waiting for confirmation...");
      const receipt = await tx.wait();

      // Parse BrokerCreated event from logs
      const iface = new ethers.Interface(FACTORY_ABI);
      for (const log of receipt.logs) {
        try {
          const parsed = iface.parseLog({ topics: log.topics, data: log.data });
          if (parsed && parsed.name === "BrokerCreated") {
            setBrokerAddress(parsed.args.broker);
            break;
          }
        } catch {
          // Not our event
        }
      }

      setHasBroker(true);
      setStep("Broker deployed ✓");
    } catch (e) {
      console.error("Broker creation failed:", e);
      // User rejected or tx failed
      const msg =
        e.code === "ACTION_REJECTED"
          ? "Transaction rejected"
          : e.message || "Failed to create broker";
      setError(msg);
      setStep("");
    } finally {
      await restoreAnvilChainId();
      setCreating(false);
    }
  }, [account, brokerFactoryAddr]);

  // ── Deposit waUSDC into broker (MetaMask signed) ────────────────
  const depositFunds = useCallback(
    async (amount) => {
      if (!account || !brokerAddress || !waUsdcAddr) return;
      if (!window.ethereum) {
        setError("MetaMask not found");
        return;
      }

      setDepositing(true);
      setError(null);
      setStep("Preparing deposit...");

      try {
        // Sync Anvil + MetaMask for signed tx
        const signer = await getAnvilSigner();

        // waUSDC has 6 decimals
        const amountWei = ethers.parseUnits(amount.toString(), 6);

        const token = new ethers.Contract(waUsdcAddr, ERC20_ABI, signer);

        setStep("Confirm in wallet...");
        const tx = await token.transfer(brokerAddress, amountWei, {
          gasLimit: 200_000,
        });

        setStep("Waiting for confirmation...");
        await tx.wait();

        // Refresh broker balance after deposit
        await fetchBrokerBalance(brokerAddress);

        setStep("Deposit confirmed ✓");
      } catch (e) {
        console.error("Deposit failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.message || "Failed to deposit funds";
        setError(msg);
        setStep("");
      } finally {
        await restoreAnvilChainId();
        setDepositing(false);
      }
    },
    [account, brokerAddress, waUsdcAddr, fetchBrokerBalance],
  );

  return {
    hasBroker,
    brokerAddress,
    brokerBalance,
    creating,
    depositing,
    error,
    step,
    createBroker,
    depositFunds,
    checkBroker,
    fetchBrokerBalance,
  };
}
