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

export const RPC_URL = `${window.location.origin}/rpc`;
export const CHAIN_ID = 31337;
const CHAIN_HEX = "0x7a69";

// Static network object — reused across all BrowserProvider instances.
const RLD_NETWORK = ethers.Network.from({ chainId: CHAIN_ID, name: "rld" });

// Cached BrowserProvider — no detection RPC calls thanks to static network.
let _provider = null;

/**
 * Get an ethers.js Signer for write operations.
 *
 * 1. Switches MetaMask to chain 31337 (no-op if already there).
 * 2. Returns a fresh Signer (picks up the active account).
 */
export async function getSigner() {
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: CHAIN_HEX }],
    });
  } catch {
    // User rejected or chain not configured — TX will fail downstream
  }

  if (!_provider) {
    _provider = new ethers.BrowserProvider(window.ethereum, RLD_NETWORK);
  }

  return _provider.getSigner();
}
