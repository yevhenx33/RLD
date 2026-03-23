import { useState, useCallback, useEffect } from "react";
import { ethers } from "ethers";
import { rpcProvider } from "../utils/provider";

/**
 * Universal faucet hook — auto-detects Anvil vs Reth mode.
 *
 * Anvil mode (Docker Anvil):
 *   Uses anvil_setStorageAt to directly set USDC + waUSDC balances.
 *   Zero MetaMask popups, instant.
 *
 * Reth mode (persistent fork):
 *   POST /api/faucet → faucet_server.py → SimFunder.fund()
 *   Atomic: USDC → Aave → aUSDC → wrap → waUSDC in one tx.
 *
 * Detection: try anvil_nodeInfo — if it succeeds, we're on Anvil.
 */

const RPC_URL = `${window.location.origin}/rpc`;
const FAUCET_API = `${window.location.origin}/api/faucet`;

const WAUSDC_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
];
const ERC20_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
];

/**
 * Call an Anvil admin RPC method.
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
 * Detect if we're running on Anvil (vs Reth).
 * Anvil supports anvil_nodeInfo; Reth doesn't.
 */
async function detectAnvil(rpcUrl) {
  try {
    await anvilRpc(rpcUrl, "anvil_nodeInfo");
    return true;
  } catch {
    return false;
  }
}

/**
 * Anvil faucet: directly sets storage slots (instant, no tx needed).
 */
async function faucetAnvil(rpcUrl, user, waUsdcAddress, usdc) {
  console.log("[faucet] Using Anvil mode (storage manipulation)");

  // 1. Set ETH balance
  await anvilRpc(rpcUrl, "anvil_setBalance", [
    user,
    "0x56BC75E2D63100000", // 100 ETH
  ]);

  // 2. Set USDC + waUSDC via storage slots
  const coder = new ethers.AbiCoder();
  const amountPerToken = BigInt("50000000000"); // 50,000 * 10^6
  const hexBalance = "0x" + amountPerToken.toString(16).padStart(64, "0");

  // USDC: mainnet proxy uses slot 9
  const usdcSlot = ethers.keccak256(
    coder.encode(["address", "uint256"], [user, 9]),
  );
  await anvilRpc(rpcUrl, "anvil_setStorageAt", [usdc, usdcSlot, hexBalance]);

  // waUSDC: solmate ERC20 uses slot 3
  const waUsdcSlot = ethers.keccak256(
    coder.encode(["address", "uint256"], [user, 3]),
  );
  await anvilRpc(rpcUrl, "anvil_setStorageAt", [
    waUsdcAddress,
    waUsdcSlot,
    hexBalance,
  ]);

  console.log("[faucet] ✓ Anvil: balances set via storage");
  return { success: true, mode: "anvil" };
}

/**
 * Reth faucet: POST /api/faucet → SimFunder.fund() (atomic on-chain tx).
 */
async function faucetReth(apiUrl, user, setStep) {
  console.log("[faucet] Using Reth mode (SimFunder.fund)");

  setStep("Sending transaction...");
  const res = await fetch(apiUrl, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ address: user }),
  });

  const data = await res.json();
  if (!res.ok || data.error) {
    throw new Error(data.error || `Faucet failed (HTTP ${res.status})`);
  }
  setStep("Transaction confirmed!");

  console.log("[faucet] ✓ Reth: SimFunder funded user", data);
  return { success: true, mode: "reth", ...data };
}

export function useFaucet(account, waUsdcAddress, externalContracts) {
  const USDC =
    externalContracts?.usdc || "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [waUsdcBalance, setWaUsdcBalance] = useState(null);
  const [usdcBalance, setUsdcBalance] = useState(null);
  const [ethBalance, setEthBalance] = useState(null);

  // ── Fetch balances ───────────────────────────────────────
  const fetchBalance = useCallback(
    async (addr) => {
      if (!addr || !waUsdcAddress) return;
      try {
        const provider = rpcProvider;

        const waUsdcContract = new ethers.Contract(
          waUsdcAddress,
          WAUSDC_ABI,
          provider,
        );
        const waBal = await waUsdcContract.balanceOf(addr);
        setWaUsdcBalance(ethers.formatUnits(waBal, 6));

        const usdcContract = new ethers.Contract(USDC, ERC20_ABI, provider);
        const uBal = await usdcContract.balanceOf(addr);
        setUsdcBalance(ethers.formatUnits(uBal, 6));

        const ethBal = await provider.getBalance(addr);
        setEthBalance(ethers.formatEther(ethBal));
      } catch (e) {
        console.warn("Failed to fetch balances:", e);
      }
    },
    [waUsdcAddress, USDC],
  );

  useEffect(() => {
    if (account && waUsdcAddress) fetchBalance(account);
  }, [account, waUsdcAddress, fetchBalance]);

  // ── Main faucet request ──────────────────────────────────
  const requestFaucet = useCallback(
    async (userAddress) => {
      if (!userAddress) throw new Error("No wallet connected");
      if (!waUsdcAddress) throw new Error("waUSDC address not loaded yet");

      setLoading(true);
      setError(null);

      try {
        const user = userAddress.toLowerCase();
        console.log(`[faucet] Starting for ${user}`);

        // Auto-detect Anvil vs Reth
        setStep("Detecting environment...");
        const isAnvil = await detectAnvil(RPC_URL);

        if (isAnvil) {
          setStep("Funding via Anvil...");
          await faucetAnvil(RPC_URL, user, waUsdcAddress, USDC);
        } else {
          await faucetReth(FAUCET_API, user, setStep);
        }

        // Poll until balances actually update on-chain
        setStep("Confirming balances...");
        const provider = rpcProvider;
        const waC = new ethers.Contract(waUsdcAddress, WAUSDC_ABI, provider);
        const uC = new ethers.Contract(USDC, ERC20_ABI, provider);

        let newWa, newUsdc, newEth;
        for (let i = 0; i < 10; i++) {
          newWa = await waC.balanceOf(user);
          newUsdc = await uC.balanceOf(user);
          newEth = await provider.getBalance(user);
          if (newWa > 0n || newUsdc > 0n) break;
          await new Promise((r) => setTimeout(r, 1000));
        }

        // Atomic state update — all balances + step flip together
        const formattedWa = ethers.formatUnits(newWa, 6);
        const formattedUsdc = ethers.formatUnits(newUsdc, 6);
        const formattedEth = ethers.formatEther(newEth);
        setWaUsdcBalance(formattedWa);
        setUsdcBalance(formattedUsdc);
        setEthBalance(formattedEth);
        setStep("Done!");

        console.log(`[faucet] ✓ Complete: waUSDC=${formattedWa}, USDC=${formattedUsdc}, ETH=${formattedEth}`);
        return { success: true, waUsdcBalance: formattedWa, usdcBalance: formattedUsdc, ethBalance: formattedEth };
      } catch (err) {
        console.error("Faucet error:", err);
        setError(err.message || "Faucet failed");
        return { success: false, error: err.message };
      } finally {
        setLoading(false);
      }
    },
    [waUsdcAddress, USDC],
  );

  return {
    requestFaucet,
    loading,
    error,
    step,
    waUsdcBalance,
    usdcBalance,
    ethBalance,
    refreshBalance: () => fetchBalance(account),
  };
}
