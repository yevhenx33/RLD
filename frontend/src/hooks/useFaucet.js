import { useState, useCallback, useEffect, useMemo } from "react";
import { FAUCET_API_URL } from "../api/endpoints.js";
import { debugLog } from "../utils/debugLogger.js";

/**
 * Faucet: POST /api/faucet -> faucet_server.py -> SimFunder.fund().
 */

const FAUCET_REQUEST_TIMEOUT_MS = 30000;
const FAUCET_WATCHDOG_TIMEOUT_MS = 45000;
const BALANCE_READ_TIMEOUT_MS = 8000;
const BALANCE_CONFIRM_ATTEMPTS = 12;
const BALANCE_CONFIRM_DELAY_MS = 1000;
export const FAUCET_BALANCE_THRESHOLD_RAW = 1_000_000n;

const WAUSDC_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
];
const ERC20_ABI = [
  "function balanceOf(address owner) view returns (uint256)",
];

const DEFAULT_FAUCET_STATE = Object.freeze({
  loading: false,
  pending: false,
  error: null,
  step: "",
  waUsdcBalance: null,
  usdcBalance: null,
  ethBalance: null,
  startedAt: null,
});

const faucetStateByKey = new Map();
const faucetSubscribersByKey = new Map();

let ethersModulePromise;
function loadEthers() {
  ethersModulePromise ||= import("ethers").then((mod) => mod.ethers);
  return ethersModulePromise;
}

let rpcProviderPromise;
function loadRpcProvider() {
  rpcProviderPromise ||= import("../utils/provider").then((mod) => mod.rpcProvider);
  return rpcProviderPromise;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function withTimeout(promise, timeoutMs, message) {
  let timeoutId;
  const timeout = new Promise((_, reject) => {
    timeoutId = setTimeout(() => reject(new Error(message)), timeoutMs);
  });

  return Promise.race([promise, timeout]).finally(() => clearTimeout(timeoutId));
}

function toBigInt(value) {
  if (typeof value === "bigint") return value;
  if (value == null) return 0n;
  return BigInt(value.toString());
}

function faucetKey(account, waUsdcAddress, usdcAddress) {
  if (!account || !waUsdcAddress || !usdcAddress) return null;
  return [
    account.toLowerCase(),
    waUsdcAddress.toLowerCase(),
    usdcAddress.toLowerCase(),
  ].join(":");
}

function getStoredFaucetState(key) {
  if (!key) return { ...DEFAULT_FAUCET_STATE };
  const state = faucetStateByKey.get(key) || { ...DEFAULT_FAUCET_STATE };
  if (
    state.loading &&
    (!state.startedAt || Date.now() - state.startedAt > FAUCET_WATCHDOG_TIMEOUT_MS)
  ) {
    const recovered = {
      ...state,
      loading: false,
      pending: false,
      step: "",
      startedAt: null,
    };
    faucetStateByKey.set(key, recovered);
    return recovered;
  }
  return state;
}

function subscribeFaucetState(key, listener) {
  if (!key) return () => {};
  const subscribers = faucetSubscribersByKey.get(key) || new Set();
  subscribers.add(listener);
  faucetSubscribersByKey.set(key, subscribers);
  return () => {
    subscribers.delete(listener);
    if (subscribers.size === 0) faucetSubscribersByKey.delete(key);
  };
}

function publishFaucetState(key, patch) {
  if (!key) return null;
  const next = { ...getStoredFaucetState(key), ...patch };
  faucetStateByKey.set(key, next);
  const subscribers = faucetSubscribersByKey.get(key);
  if (subscribers) {
    for (const listener of subscribers) listener(next);
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("rld:faucet-state", {
      detail: { key, state: next },
    }));
  }
  return next;
}

export function hasFundedTokenBalance(
  balances,
  thresholdRaw = FAUCET_BALANCE_THRESHOLD_RAW,
) {
  if (!balances) return false;
  const threshold = toBigInt(thresholdRaw);
  return (
    toBigInt(balances.waRaw) >= threshold ||
    toBigInt(balances.usdcRaw) >= threshold
  );
}

async function parseJsonResponse(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Faucet returned invalid JSON (HTTP ${res.status})`);
  }
}

function faucetErrorMessage(status, data) {
  const message = data?.error || data?.message;
  if (message) return message;
  if (status === 429) return "Faucet is rate limited. Try again shortly.";
  if (status >= 500) return "Faucet service is unavailable.";
  return `Faucet failed (HTTP ${status})`;
}

export async function requestFaucetFunds(
  apiUrl,
  user,
  {
    fetchImpl = fetch,
    timeoutMs = FAUCET_REQUEST_TIMEOUT_MS,
    setStep = () => {},
  } = {},
) {
  debugLog("[faucet] Requesting funds via API...");

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    setStep("Sending transaction...");
    const res = await fetchImpl(apiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address: user }),
      signal: controller.signal,
    });

    const data = await parseJsonResponse(res);
    if (!res.ok || data?.success === false || data?.ok === false || data?.error) {
      throw new Error(faucetErrorMessage(res.status, data));
    }

    setStep("Transaction confirmed!");
    debugLog("[faucet] funded user", data);
    return { success: true, ...data };
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("Faucet request timed out. Check service health and retry.");
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function readBalances(user, waUsdcAddress, usdcAddress) {
  const [ethers, provider] = await Promise.all([loadEthers(), loadRpcProvider()]);
  const waC = new ethers.Contract(waUsdcAddress, WAUSDC_ABI, provider);
  const uC = new ethers.Contract(usdcAddress, ERC20_ABI, provider);

  const [waRaw, usdcRaw, ethRaw] = await withTimeout(
    Promise.all([
      waC.balanceOf(user),
      uC.balanceOf(user),
      provider.getBalance(user),
    ]),
    BALANCE_READ_TIMEOUT_MS,
    "Balance read timed out. Check RPC health and retry.",
  );

  return {
    waRaw,
    usdcRaw,
    ethRaw,
    waUsdcBalance: ethers.formatUnits(waRaw, 6),
    usdcBalance: ethers.formatUnits(usdcRaw, 6),
    ethBalance: ethers.formatEther(ethRaw),
  };
}

export function useFaucet(account, waUsdcAddress, externalContracts, { enabled = true } = {}) {
  const USDC =
    externalContracts?.usdc || "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
  const stateKey = useMemo(
    () => faucetKey(account, waUsdcAddress, USDC),
    [account, waUsdcAddress, USDC],
  );
  const [sharedState, setSharedState] = useState(() => getStoredFaucetState(stateKey));

  useEffect(() => {
    setSharedState(getStoredFaucetState(stateKey));
    return subscribeFaucetState(stateKey, setSharedState);
  }, [stateKey]);

  const publishState = useCallback(
    (patch) => {
      if (!stateKey) {
        setSharedState((current) => ({ ...current, ...patch }));
        return null;
      }
      return publishFaucetState(stateKey, patch);
    },
    [stateKey],
  );

  const updateBalances = useCallback((balances) => {
    if (!balances) return;
    publishState({
      waUsdcBalance: balances.waUsdcBalance,
      usdcBalance: balances.usdcBalance,
      ethBalance: balances.ethBalance,
    });
  }, [publishState]);

  const fetchBalance = useCallback(
    async (addr) => {
      if (!addr || !waUsdcAddress) return null;
      try {
        const balances = await readBalances(addr, waUsdcAddress, USDC);
        updateBalances(balances);
        return balances;
      } catch (e) {
        console.warn("Failed to fetch balances:", e);
        return null;
      }
    },
    [waUsdcAddress, USDC, updateBalances],
  );

  useEffect(() => {
    if (enabled && account && waUsdcAddress) fetchBalance(account);
  }, [enabled, account, waUsdcAddress, fetchBalance]);

  const requestFaucet = useCallback(
    async (userAddress) => {
      if (!userAddress) throw new Error("No wallet connected");
      if (!waUsdcAddress) throw new Error("Collateral address not loaded yet");
      if (!stateKey) throw new Error("Faucet runtime state not loaded yet");
      const currentState = getStoredFaucetState(stateKey);
      if (currentState.pending || currentState.loading) {
        return {
          success: false,
          pending: true,
          error: "Faucet request already pending",
        };
      }

      const ethers = await loadEthers();
      if (!ethers.isAddress(userAddress)) {
        throw new Error("Invalid wallet address");
      }

      publishState({
        loading: true,
        pending: true,
        error: null,
        startedAt: Date.now(),
      });
      const watchdogId = setTimeout(() => {
        publishState({
          loading: false,
          pending: false,
          error: "Faucet request timed out. Refresh balances and retry.",
          step: "",
          startedAt: null,
        });
      }, FAUCET_WATCHDOG_TIMEOUT_MS);

      const user = userAddress.toLowerCase();
      try {
        debugLog(`[faucet] Starting for ${user}`);

        publishState({ step: "Checking balances..." });
        const existingBalances = await fetchBalance(user);
        if (hasFundedTokenBalance(existingBalances)) {
          publishState({ step: "Already funded" });
          return { success: true, skipped: true, ...existingBalances };
        }

        publishState({ step: "Funding via backend..." });
        await requestFaucetFunds(FAUCET_API_URL, user, {
          setStep: (nextStep) => publishState({ step: nextStep }),
        });

        publishState({ step: "Confirming balances..." });
        let balances = null;
        for (let i = 0; i < BALANCE_CONFIRM_ATTEMPTS; i++) {
          balances = await readBalances(user, waUsdcAddress, USDC);
          if (hasFundedTokenBalance(balances)) break;
          await delay(BALANCE_CONFIRM_DELAY_MS);
        }

        if (!hasFundedTokenBalance(balances)) {
          throw new Error("Faucet transaction sent, but funded balances were not observed.");
        }

        updateBalances(balances);
        publishState({ step: "Done!" });

        debugLog(
          `[faucet] Complete: waUSDC=${balances.waUsdcBalance}, USDC=${balances.usdcBalance}, ETH=${balances.ethBalance}`,
        );
        return { success: true, ...balances };
      } catch (err) {
        const maybeBalances = await fetchBalance(user);
        if (
          err?.message?.toLowerCase().includes("rate limited") &&
          maybeBalances &&
          hasFundedTokenBalance(maybeBalances)
        ) {
          publishState({ step: "Already funded" });
          return { success: true, rateLimited: true, ...maybeBalances };
        }

        console.error("Faucet error:", err);
        publishState({ error: err.message || "Faucet failed" });
        return { success: false, error: err.message };
      } finally {
        clearTimeout(watchdogId);
        publishState({
          loading: false,
          pending: false,
          startedAt: null,
        });
      }
    },
    [waUsdcAddress, USDC, fetchBalance, updateBalances, publishState, stateKey],
  );

  return {
    requestFaucet,
    loading: sharedState.loading,
    error: sharedState.error,
    step: sharedState.step,
    waUsdcBalance: sharedState.waUsdcBalance,
    usdcBalance: sharedState.usdcBalance,
    ethBalance: sharedState.ethBalance,
    refreshBalance: () => fetchBalance(account),
  };
}
