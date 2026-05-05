/**
 * RLD Protocol — Connection utilities.
 *
 * Single source of truth for RPC URL, chain ID, and signer creation.
 *
 * Reth dev mode uses chainId 31337 natively — no chainId flipping needed.
 * Passing a static network to BrowserProvider avoids the eth_chainId +
 * eth_blockNumber detection calls that trigger ethers.js throttling
 * (-32002: "too many errors, retrying in 0.5 minutes").
 */

import { ethers } from "ethers";
import { RPC_URL as ENDPOINT_RPC_URL } from "../api/endpoints.js";

export const RPC_URL = ENDPOINT_RPC_URL;
export const CHAIN_ID = 31337;
export const CHAIN_HEX = "0x7a69";
export const CHAIN_NAME = "RLD Demo Chain";

// Static network object — reused across all BrowserProvider instances.
const RLD_NETWORK = ethers.Network.from({ chainId: CHAIN_ID, name: "rld" });

// Cached BrowserProvider — no detection RPC calls thanks to static network.
let _provider = null;
let _chainParamsRefreshed = false;

function getEthereumProvider(ethereum = undefined) {
  return ethereum || (typeof window !== "undefined" ? window.ethereum : null);
}

export function getWalletErrorCode(error) {
  return (
    error?.code ??
    error?.error?.code ??
    error?.data?.originalError?.code ??
    error?.info?.error?.code ??
    null
  );
}

export function isChainMissingError(error) {
  return getWalletErrorCode(error) === 4902;
}

export function isUserRejectedError(error) {
  return getWalletErrorCode(error) === 4001 || error?.code === "ACTION_REJECTED";
}

export function getWalletErrorMessage(error, fallback = "Wallet request failed") {
  if (isUserRejectedError(error)) return "Wallet request rejected";
  const code = getWalletErrorCode(error);
  const rawMessage = String(error?.message || error?.info?.error?.message || "");
  if (code === -32002 && rawMessage.toLowerCase().includes("rpc endpoint returned too many errors")) {
    return "MetaMask is throttling the current RPC endpoint. Refresh the page and approve the RLD network RPC update.";
  }
  if (code === -32002) return "Open MetaMask and finish the pending wallet request";
  return error?.shortMessage || error?.reason || error?.message || fallback;
}

export function buildRldChainParams(rpcUrl = RPC_URL) {
  return {
    chainId: CHAIN_HEX,
    chainName: CHAIN_NAME,
    rpcUrls: [rpcUrl],
    nativeCurrency: {
      name: "Ether",
      symbol: "ETH",
      decimals: 18,
    },
  };
}

export function createBrowserProvider(ethereum = undefined) {
  const provider = getEthereumProvider(ethereum);
  if (!provider) {
    throw new Error("MetaMask not found");
  }
  return new ethers.BrowserProvider(provider, RLD_NETWORK);
}

async function addOrRefreshRldChain(provider) {
  await provider.request({
    method: "wallet_addEthereumChain",
    params: [buildRldChainParams()],
  });
  _chainParamsRefreshed = true;
}

async function refreshRldChainParamsOnce(provider) {
  if (_chainParamsRefreshed) return;
  try {
    await addOrRefreshRldChain(provider);
  } catch (error) {
    throw new Error(getWalletErrorMessage(error, "Failed to refresh RLD RPC endpoint"));
  }
}

async function verifyWalletRpc(provider) {
  try {
    await provider.request({ method: "eth_blockNumber", params: [] });
  } catch (error) {
    throw new Error(getWalletErrorMessage(
      error,
      "RLD wallet RPC is unavailable. Remove the existing RLD Demo Chain from MetaMask and reconnect.",
    ));
  }
}

export async function ensureRldChain(ethereum = undefined) {
  const provider = getEthereumProvider(ethereum);
  if (!provider?.request) {
    throw new Error("MetaMask not found");
  }

  try {
    await provider.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: CHAIN_HEX }],
    });
  } catch (switchError) {
    if (!isChainMissingError(switchError)) {
      throw new Error(getWalletErrorMessage(switchError, "Failed to switch wallet network"));
    }

    try {
      await addOrRefreshRldChain(provider);
    } catch (addError) {
      throw new Error(getWalletErrorMessage(addError, "Failed to add RLD Demo Chain"));
    }

    try {
      await provider.request({
        method: "wallet_switchEthereumChain",
        params: [{ chainId: CHAIN_HEX }],
      });
    } catch (secondSwitchError) {
      throw new Error(getWalletErrorMessage(secondSwitchError, "Failed to switch wallet network"));
    }
  }

  await refreshRldChainParamsOnce(provider);

  const activeChainId = await provider.request({ method: "eth_chainId" });
  if (String(activeChainId).toLowerCase() !== CHAIN_HEX) {
    throw new Error(`Wallet is on chain ${activeChainId || "unknown"}, expected ${CHAIN_HEX}`);
  }
  await verifyWalletRpc(provider);
}

export function resetCachedWalletProvider() {
  _provider = null;
  _chainParamsRefreshed = false;
}

/**
 * Get an ethers.js Signer for write operations.
 *
 * 1. Switches MetaMask to chain 31337, adding it first when missing.
 * 2. Returns a fresh Signer (picks up the active account).
 */
export async function getSigner() {
  const ethereum = getEthereumProvider();
  await ensureRldChain(ethereum);

  if (!_provider) {
    _provider = createBrowserProvider(ethereum);
  }

  return _provider.getSigner();
}
