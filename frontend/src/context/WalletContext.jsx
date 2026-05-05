import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { ethers } from "ethers";
import { RUNTIME_MANIFEST_URL } from "../api/endpoints";
import {
  CHAIN_HEX,
  CHAIN_ID,
  RPC_URL,
  createBrowserProvider,
  ensureRldChain,
  getWalletErrorMessage,
} from "../utils/connection";
import { rpcProvider } from "../utils/provider";

const WalletContext = createContext();

// USDC Addresses by Chain ID.
const USDC_ADDRESSES = {
  1: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
  31337: "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
  11155111: "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
};

const USDC_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
  "function decimals() view returns (uint8)",
];

function parseWalletChainId(value) {
  if (value == null) return null;
  try {
    if (typeof value === "number") return String(value);
    const text = String(value);
    return text.startsWith("0x")
      ? String(Number.parseInt(text, 16))
      : text;
  } catch {
    return null;
  }
}

async function readWalletChainId(ethereum) {
  if (!ethereum?.request) return null;
  const value = await ethereum.request({ method: "eth_chainId" });
  return parseWalletChainId(value);
}

function getEthereum() {
  return typeof window !== "undefined" ? window.ethereum : null;
}

async function refreshRuntimeManifest() {
  if (typeof fetch !== "function") return null;
  const response = await fetch(RUNTIME_MANIFEST_URL, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Runtime manifest unavailable (HTTP ${response.status})`);
  }
  return response.json();
}

async function readLatestBlock(provider) {
  try {
    return await provider.getBlockNumber();
  } catch {
    return null;
  }
}

export function WalletProvider({ children }) {
  const [account, setAccount] = useState(null);
  const [provider, setProvider] = useState(null);
  const [balance, setBalance] = useState("0");
  const [usdcBalance, setUsdcBalance] = useState("0");
  const [chainId, setChainId] = useState(null);
  const [debugInfo, setDebugInfo] = useState("");
  const [walletError, setWalletError] = useState(null);

  const resetBalances = useCallback(() => {
    setBalance("0");
    setUsdcBalance("0");
  }, []);

  const fetchBalances = useCallback(async (acc) => {
    if (!acc) return;

    let net;
    try {
      net = await rpcProvider.getNetwork();
      setChainId(net.chainId.toString());
    } catch (err) {
      setDebugInfo(`Network error: ${err.message}`);
      resetBalances();
      return;
    }

    try {
      const bal = await rpcProvider.getBalance(acc);
      setBalance(ethers.formatEther(bal));
    } catch (err) {
      setDebugInfo(`ETH balance error: ${err.message}`);
      setBalance("0");
    }

    try {
      const currentChainId = net.chainId.toString();
      const usdcAddr = USDC_ADDRESSES[currentChainId];

      if (!usdcAddr) {
        setDebugInfo(`No USDC config for chain ${currentChainId}`);
        setUsdcBalance("0.00");
        return;
      }

      const usdcContract = new ethers.Contract(usdcAddr, USDC_ABI, rpcProvider);
      const code = await rpcProvider.getCode(usdcAddr);
      if (code === "0x") {
        setDebugInfo(`USDC contract missing on chain ${currentChainId}`);
        setUsdcBalance("0.00");
        return;
      }

      const usdcBal = await usdcContract.balanceOf(acc);
      setDebugInfo(`Connected: ${currentChainId}. USDC: ${usdcBal.toString()}`);
      setUsdcBalance(ethers.formatUnits(usdcBal, 6));
    } catch (error) {
      setDebugInfo(`USDC balance error: ${error.message}`);
      setUsdcBalance("0.00");
    }
  }, [resetBalances]);

  const activateWallet = useCallback(async (accounts, { requireDemoChain = false } = {}) => {
    const ethereum = getEthereum();
    if (!ethereum) {
      const message = "No Ethereum wallet found. Please install MetaMask.";
      setWalletError(message);
      return { success: false, error: message };
    }

    if (!accounts?.length) {
      setAccount(null);
      resetBalances();
      return { success: false, error: "No wallet account selected" };
    }

    try {
      if (requireDemoChain) {
        await ensureRldChain(ethereum);
      }
      const nextChainId = await readWalletChainId(ethereum);
      setChainId(nextChainId);

      const tempProvider = createBrowserProvider(ethereum);
      setProvider(tempProvider);
      setAccount(accounts[0]);
      setWalletError(null);

      if (nextChainId === String(CHAIN_ID)) {
        await readLatestBlock(rpcProvider);
        if (requireDemoChain) {
          await refreshRuntimeManifest().catch((err) => {
            setDebugInfo(err.message);
          });
        }
        await fetchBalances(accounts[0]);
      } else {
        resetBalances();
        setDebugInfo(`Wrong network: ${nextChainId || "unknown"}`);
      }

      return { success: true, account: accounts[0] };
    } catch (error) {
      const message = getWalletErrorMessage(error, "Connection failed");
      setWalletError(message);
      setDebugInfo(message);
      return { success: false, error: message };
    }
  }, [fetchBalances, resetBalances]);

  useEffect(() => {
    const ethereum = getEthereum();
    if (!ethereum) return undefined;

    ethereum
      .request({ method: "eth_accounts" })
      .then((accounts) => activateWallet(accounts, { requireDemoChain: false }))
      .catch((error) => {
        setWalletError(getWalletErrorMessage(error, "Failed to read wallet accounts"));
      });

    readWalletChainId(ethereum)
      .then(setChainId)
      .catch(() => setChainId(null));

    const handleAccountsChanged = (accounts) => {
      activateWallet(accounts, { requireDemoChain: false });
    };

    const handleChainChanged = (nextChainHex) => {
      const nextChainId = parseWalletChainId(nextChainHex);
      setChainId(nextChainId);
      if (nextChainId !== String(CHAIN_ID)) {
        resetBalances();
        setDebugInfo(`Wrong network: ${nextChainId || "unknown"}`);
        return;
      }
      const currentAccount = account;
      if (currentAccount) {
        const tempProvider = createBrowserProvider(ethereum);
        setProvider(tempProvider);
        fetchBalances(currentAccount);
      }
    };

    ethereum.on?.("accountsChanged", handleAccountsChanged);
    ethereum.on?.("chainChanged", handleChainChanged);

    return () => {
      ethereum.removeListener?.("accountsChanged", handleAccountsChanged);
      ethereum.removeListener?.("chainChanged", handleChainChanged);
    };
  }, [account, activateWallet, fetchBalances, resetBalances]);

  const connectWallet = useCallback(async () => {
    const ethereum = getEthereum();
    if (!ethereum) {
      const message = "No Ethereum wallet found. Please install MetaMask.";
      setWalletError(message);
      return { success: false, error: message };
    }

    try {
      const accounts = await ethereum.request({ method: "eth_requestAccounts" });
      return await activateWallet(accounts, { requireDemoChain: true });
    } catch (error) {
      const message = getWalletErrorMessage(error, "Connection failed");
      setWalletError(message);
      setDebugInfo(message);
      return { success: false, error: message };
    }
  }, [activateWallet]);

  const switchNetwork = useCallback(async () => {
    const ethereum = getEthereum();
    if (!ethereum) {
      const message = "No Ethereum wallet found. Please install MetaMask.";
      setWalletError(message);
      return { success: false, error: message };
    }

    try {
      await ensureRldChain(ethereum);
      const nextChainId = await readWalletChainId(ethereum);
      setChainId(nextChainId);
      setWalletError(null);
      if (account) {
        const tempProvider = createBrowserProvider(ethereum);
        setProvider(tempProvider);
        await readLatestBlock(rpcProvider);
        await refreshRuntimeManifest().catch((err) => {
          setDebugInfo(err.message);
        });
        await fetchBalances(account);
      }
      return { success: true };
    } catch (error) {
      const message = getWalletErrorMessage(error, "Failed to switch network");
      setWalletError(message);
      setDebugInfo(message);
      return { success: false, error: message };
    }
  }, [account, fetchBalances]);

  const disconnect = useCallback(() => {
    setAccount(null);
    resetBalances();
    setChainId(null);
    setDebugInfo("");
    setWalletError(null);
  }, [resetBalances]);

  return (
    <WalletContext.Provider
      value={{
        account,
        provider,
        balance,
        usdcBalance,
        chainId,
        debugInfo,
        walletError,
        expectedChainId: String(CHAIN_ID),
        expectedChainHex: CHAIN_HEX,
        rpcUrl: RPC_URL,
        connectWallet,
        disconnect,
        switchNetwork,
      }}
    >
      {children}
    </WalletContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export const useWallet = () => useContext(WalletContext);
