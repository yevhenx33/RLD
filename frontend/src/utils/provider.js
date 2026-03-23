/**
 * Singleton read-only JsonRpcProvider.
 *
 * WHY: ethers.js v6 creates a new network-detection request (eth_chainId +
 * eth_blockNumber) every time you construct a JsonRpcProvider.  When 15+
 * hooks each create their own provider on every poll cycle, the browser
 * fires dozens of RPC calls per second through Cloudflare, which triggers
 * ethers' built-in throttle (-32002: "too many errors, retrying in 0.5m").
 *
 * FIX: One shared provider with `staticNetwork` — no auto-detection calls.
 * All read-only hooks import `rpcProvider` from here instead of constructing
 * their own.  Write operations still use BrowserProvider/signer as before.
 */

import { ethers } from "ethers";
import { RPC_URL } from "./anvil";

// Chain ID 31337 = Anvil / Reth dev mode
const CHAIN_ID = 31337;

export const rpcProvider = new ethers.JsonRpcProvider(RPC_URL, CHAIN_ID, {
  staticNetwork: true,
  batchMaxCount: 10,
});
