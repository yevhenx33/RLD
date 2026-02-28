import { useState, useCallback, useEffect } from "react";
import { ethers } from "ethers";

/**
 * One-click Anvil faucet: provisions ETH + waUSDC to the connected wallet.
 *
 * Flow (all via Anvil admin RPCs — zero MetaMask popups):
 *   1. anvil_setBalance → 100 ETH for gas
 *   2. Impersonate USDC whale → transfer USDC to user
 *   3. Impersonate user → approve + supply to Aave → aUSDC
 *   4. Impersonate user → approve + wrap aUSDC → waUSDC
 *
 * @param {string} account        Connected wallet address
 * @param {string} waUsdcAddress   Live waUSDC contract address (from indexer)
 */

// ── Mainnet addresses (Anvil fork of mainnet) ─────────────────────
const USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
const AUSDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c";
const AAVE_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2";
const USDC_WHALE = "0x37305B1cD40574E4C5Ce33f8e8306Be057fD7341";
const RPC_URL = `${window.location.origin}/rpc`;

// Amount to fund: 100k USDC (6 decimals)
const FUND_AMOUNT = 100_000n * 1_000_000n; // 100,000 USDC in wei

// Minimal ABIs for the calls we need
const ERC20_ABI = [
  "function transfer(address to, uint256 amount) returns (bool)",
  "function approve(address spender, uint256 amount) returns (bool)",
  "function balanceOf(address owner) view returns (uint256)",
];
const AAVE_POOL_ABI = [
  "function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode)",
];
const WAUSDC_ABI = [
  "function wrap(uint256 aTokenAmount) returns (uint256 shares)",
  "function balanceOf(address owner) view returns (uint256)",
];

/**
 * Send a transaction from an impersonated account via raw JSON-RPC.
 * This bypasses MetaMask entirely — the tx is sent directly to Anvil.
 */
async function sendImpersonatedTx(rpcUrl, from, to, data) {
  const res = await fetch(rpcUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      method: "eth_sendTransaction",
      params: [{ from, to, data, gas: "0x7A1200" }], // 8M gas limit
      id: Date.now(),
    }),
  });
  const json = await res.json();
  if (json.error) throw new Error(`TX failed: ${json.error.message}`);
  return json.result; // tx hash
}

/**
 * Call an Anvil admin RPC method (e.g. anvil_setBalance).
 */
async function anvilRpc(rpcUrl, method, params = []) {
  const res = await fetch(rpcUrl, {
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
 * Wait for a transaction to be mined.
 */
async function waitForTx(rpcUrl, txHash, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const res = await fetch(rpcUrl, {
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

export function useFaucet(account, waUsdcAddress) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [step, _setStep] = useState(""); // Current step description
  const [waUsdcBalance, setWaUsdcBalance] = useState(null);

  // ── Fetch waUSDC balance for any address ───────────────────────
  const fetchBalance = useCallback(
    async (addr) => {
      if (!addr || !waUsdcAddress) return;
      try {
        const provider = new ethers.JsonRpcProvider(RPC_URL);
        const contract = new ethers.Contract(
          waUsdcAddress,
          WAUSDC_ABI,
          provider,
        );
        const bal = await contract.balanceOf(addr);
        setWaUsdcBalance(ethers.formatUnits(bal, 6));
      } catch (e) {
        console.warn("Failed to fetch waUSDC balance:", e);
      }
    },
    [waUsdcAddress],
  );

  // Auto-fetch balance when account connects or waUSDC address changes
  useEffect(() => {
    if (account && waUsdcAddress) fetchBalance(account);
  }, [account, waUsdcAddress, fetchBalance]);

  const requestFaucet = useCallback(
    async (userAddress) => {
      if (!userAddress) throw new Error("No wallet connected");
      if (!waUsdcAddress) throw new Error("waUSDC address not loaded yet");

      setLoading(true);
      setError(null);

      // Encode calldata helpers
      const iface = {
        erc20: new ethers.Interface(ERC20_ABI),
        pool: new ethers.Interface(AAVE_POOL_ABI),
        waUsdc: new ethers.Interface(WAUSDC_ABI),
      };

      try {
        const user = userAddress.toLowerCase();

        // ── Step 1: Set ETH balance (100 ETH for gas) ────────────────

        await anvilRpc(RPC_URL, "anvil_setBalance", [
          user,
          "0x56BC75E2D63100000", // 100 ETH in hex wei
        ]);

        // ── Step 2: Impersonate whale → transfer USDC to user ────────

        await anvilRpc(RPC_URL, "anvil_impersonateAccount", [USDC_WHALE]);

        // Give whale some ETH for gas too
        await anvilRpc(RPC_URL, "anvil_setBalance", [
          USDC_WHALE,
          "0x56BC75E2D63100000",
        ]);

        const transferData = iface.erc20.encodeFunctionData("transfer", [
          user,
          FUND_AMOUNT,
        ]);
        const txTransfer = await sendImpersonatedTx(
          RPC_URL,
          USDC_WHALE,
          USDC,
          transferData,
        );
        await waitForTx(RPC_URL, txTransfer);
        await anvilRpc(RPC_URL, "anvil_stopImpersonatingAccount", [USDC_WHALE]);

        // ── Step 3: Impersonate user → deposit USDC to Aave ──────────

        await anvilRpc(RPC_URL, "anvil_impersonateAccount", [user]);

        // Approve USDC → Aave Pool
        const approveAaveData = iface.erc20.encodeFunctionData("approve", [
          AAVE_POOL,
          FUND_AMOUNT,
        ]);
        const txApproveAave = await sendImpersonatedTx(
          RPC_URL,
          user,
          USDC,
          approveAaveData,
        );
        await waitForTx(RPC_URL, txApproveAave);

        // Supply USDC to Aave → get aUSDC
        const supplyData = iface.pool.encodeFunctionData("supply", [
          USDC,
          FUND_AMOUNT,
          user,
          0,
        ]);
        const txSupply = await sendImpersonatedTx(
          RPC_URL,
          user,
          AAVE_POOL,
          supplyData,
        );
        await waitForTx(RPC_URL, txSupply);

        // ── Step 4: Wrap aUSDC → waUSDC ──────────────────────────────

        // Read aUSDC balance
        const aUsdcProvider = new ethers.JsonRpcProvider(RPC_URL);
        const aUsdcContract = new ethers.Contract(
          AUSDC,
          ERC20_ABI,
          aUsdcProvider,
        );
        const aUsdcBal = await aUsdcContract.balanceOf(user);

        // Approve aUSDC → waUSDC wrapper
        const approveWrapData = iface.erc20.encodeFunctionData("approve", [
          waUsdcAddress,
          aUsdcBal,
        ]);
        const txApproveWrap = await sendImpersonatedTx(
          RPC_URL,
          user,
          AUSDC,
          approveWrapData,
        );
        await waitForTx(RPC_URL, txApproveWrap);

        // Wrap
        const wrapData = iface.waUsdc.encodeFunctionData("wrap", [aUsdcBal]);
        const txWrap = await sendImpersonatedTx(
          RPC_URL,
          user,
          waUsdcAddress,
          wrapData,
        );
        await waitForTx(RPC_URL, txWrap);

        await anvilRpc(RPC_URL, "anvil_stopImpersonatingAccount", [user]);

        // ── Read final waUSDC balance ────────────────────────────────

        await fetchBalance(user);

        return { success: true, waUsdcBalance };
      } catch (err) {
        console.error("Faucet error:", err);
        setError(err.message || "Faucet failed");
        return { success: false, error: err.message };
      } finally {
        setLoading(false);
      }
    },
    [fetchBalance, waUsdcBalance, waUsdcAddress],
  );

  return {
    requestFaucet,
    loading,
    error,
    step,
    waUsdcBalance,
    refreshBalance: () => fetchBalance(account),
  };
}
