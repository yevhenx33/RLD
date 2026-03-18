/**
 * Chain-ID sync utilities — works on both Anvil and Reth.
 *
 * On Anvil fork: chainId is 1 (mainnet) but MetaMask signs with 31337.
 *   → Must call anvil_setChainId to sync before signing.
 *
 * On Reth dev: chainId is already 31337, no sync needed.
 *   → anvil_setChainId is not available — skip gracefully.
 *
 * `getAnvilSigner()` and `restoreAnvilChainId()` encapsulate this pattern.
 */

import { ethers } from "ethers";

export const RPC_URL = `${window.location.origin}/rpc`;
const ANVIL_CHAIN_ID = 31337;
const ANVIL_CHAIN_HEX = "0x7a69";

/**
 * Low-level JSON-RPC call to the node.
 */
export async function anvilRpc(method, params = []) {
  const res = await fetch(RPC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: Date.now() }),
  });
  const json = await res.json();
  if (json.error)
    throw new Error(`RPC ${method} failed: ${json.error.message}`);
  return json.result;
}

/**
 * Prepare node + MetaMask for a signed transaction and return an
 * ethers.js Signer.
 *
 * Call `restoreAnvilChainId()` in your `finally` block after the
 * transaction completes.
 *
 * @returns {Promise<ethers.Signer>}
 */
export async function getAnvilSigner() {
  // 1. Sync node's reported chainId to 31337 (Anvil-only, skip on Reth)
  try {
    await anvilRpc("anvil_setChainId", [ANVIL_CHAIN_ID]);
  } catch {
    // Reth doesn't support anvil_setChainId — chainId is already 31337
    console.log("[signer] anvil_setChainId not available (Reth mode), skipping");
  }

  // 2. Make sure MetaMask is on the Anvil/Reth network
  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: ANVIL_CHAIN_HEX }],
    });
  } catch (switchErr) {
    console.warn("[signer] Network switch skipped:", switchErr);
  }

  // 3. "any" network bypasses ethers chain-id enforcement
  const provider = new ethers.BrowserProvider(window.ethereum, "any");
  return provider.getSigner();
}

/**
 * Restore Anvil's chainId back to mainnet (1) so read-only RPC calls
 * continue to work against the mainnet fork. No-op on Reth.
 */
export async function restoreAnvilChainId() {
  try {
    await anvilRpc("anvil_setChainId", [1]);
  } catch {
    /* ignored — Reth doesn't need this */
  }
}
