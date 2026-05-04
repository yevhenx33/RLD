import { useState, useCallback, useEffect } from "react";
import { ethers } from "ethers";
import { rpcProvider } from "../utils/provider";
import { debugLog } from "../utils/debugLogger";

/**
 * Faucet: POST /api/faucet → faucet_server.py → SimFunder.fund() (atomic on-chain tx).
 */

const FAUCET_API = `${window.location.origin}/api/faucet`;

const WAUSDC_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
];
const ERC20_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
];

async function faucetReth(apiUrl, user, setStep) {
  debugLog("[faucet] Requesting funds via API...");

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

  debugLog("[faucet] ✓ Funded user", data);
  return { success: true, ...data };
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
        debugLog(`[faucet] Starting for ${user}`);

        setStep("Funding via backend...");
        await faucetReth(FAUCET_API, user, setStep);

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

        debugLog(`[faucet] ✓ Complete: waUSDC=${formattedWa}, USDC=${formattedUsdc}, ETH=${formattedEth}`);
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
