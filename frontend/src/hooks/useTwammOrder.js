import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";

// ── ABI fragments ────────────────────────────────────────────────

const POOL_KEY_TUPLE = {
  name: "key",
  type: "tuple",
  components: [
    { name: "currency0", type: "address" },
    { name: "currency1", type: "address" },
    { name: "fee", type: "uint24" },
    { name: "tickSpacing", type: "int24" },
    { name: "hooks", type: "address" },
  ],
};

const SUBMIT_ORDER_PARAMS_TUPLE = {
  name: "params",
  type: "tuple",
  components: [
    POOL_KEY_TUPLE,
    { name: "zeroForOne", type: "bool" },
    { name: "duration", type: "uint256" },
    { name: "amountIn", type: "uint256" },
  ],
};

const ORDER_KEY_TUPLE = {
  name: "orderKey",
  type: "tuple",
  components: [
    { name: "owner", type: "address" },
    { name: "expiration", type: "uint160" },
    { name: "zeroForOne", type: "bool" },
  ],
};

const PRIME_BROKER_TWAMM_ABI = [
  {
    name: "submitTwammOrder",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "twammHook", type: "address" },
      SUBMIT_ORDER_PARAMS_TUPLE,
    ],
    outputs: [
      { name: "orderId", type: "bytes32" },
      ORDER_KEY_TUPLE,
    ],
  },
  {
    name: "cancelTwammOrder",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [],
    outputs: [
      { name: "buyTokensOut", type: "uint256" },
      { name: "sellTokensRefund", type: "uint256" },
    ],
  },
];

// ── Helpers ───────────────────────────────────────────────────────

function buildPoolKey(infrastructure, collateralAddr, positionAddr) {
  const token0 =
    collateralAddr.toLowerCase() < positionAddr.toLowerCase()
      ? collateralAddr
      : positionAddr;
  const token1 =
    collateralAddr.toLowerCase() < positionAddr.toLowerCase()
      ? positionAddr
      : collateralAddr;
  return {
    currency0: token0,
    currency1: token1,
    fee: infrastructure.pool_fee || 500,
    tickSpacing: infrastructure.tick_spacing || 5,
    hooks: infrastructure.twamm_hook,
  };
}

// ── Hook ──────────────────────────────────────────────────────────

/**
 * useTwammOrder — Submit and cancel TWAMM streaming orders via PrimeBroker.
 *
 * Provides:
 * - submitOrder(amountIn, durationHours, zeroForOne, onSuccess)
 * - cancelOrder(onSuccess)
 *
 * @param {string} account       Connected wallet address
 * @param {string} brokerAddress PrimeBroker address
 * @param {object} infrastructure { twamm_hook, pool_fee, tick_spacing }
 * @param {string} collateralAddr waUSDC address
 * @param {string} positionAddr  wRLP address
 */
export function useTwammOrder(
  account,
  brokerAddress,
  infrastructure,
  collateralAddr,
  positionAddr,
) {
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [txHash, setTxHash] = useState(null);

  /**
   * Submit a TWAMM streaming order via PrimeBroker.
   *
   * @param {number|string} amountIn       Human-readable amount (6 decimals)
   * @param {number}        durationHours  Duration in hours (must be >= 1, whole number)
   * @param {boolean}       zeroForOne     true = sell currency0, false = sell currency1
   * @param {Function}      onSuccess      Called with tx receipt on success
   */
  const submitOrder = useCallback(
    async (amountIn, durationHours, zeroForOne, onSuccess) => {
      if (
        !account ||
        !brokerAddress ||
        !infrastructure?.twamm_hook ||
        !collateralAddr ||
        !positionAddr
      ) {
        setError("Missing required addresses");
        return;
      }
      if (!window.ethereum) {
        setError("MetaMask not found");
        return;
      }

      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Preparing TWAMM order...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(
          brokerAddress,
          PRIME_BROKER_TWAMM_ABI,
          signer,
        );

        const poolKey = buildPoolKey(
          infrastructure,
          collateralAddr,
          positionAddr,
        );
        const amountInWei = ethers.parseUnits(String(amountIn), 6);
        // Option E (deferred start): the contract starts at the next epoch
        // boundary and streams for exactly `duration` seconds. No extra
        // padding needed — the user gets precise duration.
        const EXPIRATION_INTERVAL = 3600n; // must match JTM's expirationInterval
        const durationSeconds = BigInt(Math.round(durationHours)) * EXPIRATION_INTERVAL;

        const params = {
          key: poolKey,
          zeroForOne,
          duration: durationSeconds,
          amountIn: amountInWei,
        };

        console.log("[TWAMM] submitTwammOrder params:", {
          twammHook: infrastructure.twamm_hook,
          poolKey,
          zeroForOne,
          durationSeconds: durationSeconds.toString(),
          amountIn: amountInWei.toString(),
        });

        setStep("Confirm TWAMM order in wallet...");
        const tx = await broker.submitTwammOrder(
          infrastructure.twamm_hook,
          params,
          { gasLimit: 2_000_000n },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          setStep("Order submitted ✓");
          if (onSuccess) onSuccess(receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[TWAMM] submitOrder failed:", e);
        let msg = "TWAMM order failed";
        if (e.code === "ACTION_REJECTED") {
          msg = "Transaction rejected";
        } else {
          const reason =
            e.revert?.args?.[0] ||
            e.reason ||
            e.shortMessage ||
            e.message;
          msg = reason || msg;
        }
        setError(msg);
        setStep("");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr],
  );

  /**
   * Cancel the active TWAMM order via PrimeBroker.
   *
   * @param {Function} onSuccess Called with tx receipt on success
   */
  const cancelOrder = useCallback(
    async (onSuccess) => {
      if (!account || !brokerAddress) {
        setError("Missing required addresses");
        return;
      }
      if (!window.ethereum) {
        setError("MetaMask not found");
        return;
      }

      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Cancelling TWAMM order...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(
          brokerAddress,
          PRIME_BROKER_TWAMM_ABI,
          signer,
        );

        setStep("Confirm cancellation in wallet...");
        const tx = await broker.cancelTwammOrder({ gasLimit: 1_000_000n });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          setStep("Order cancelled ✓");
          if (onSuccess) onSuccess(receipt);
        } else {
          setError("Cancel reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[TWAMM] cancelOrder failed:", e);
        let msg = "Cancel failed";
        if (e.code === "ACTION_REJECTED") {
          msg = "Transaction rejected";
        } else {
          const reason =
            e.revert?.args?.[0] ||
            e.reason ||
            e.shortMessage ||
            e.message;
          msg = reason || msg;
        }
        setError(msg);
        setStep("");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress],
  );

  return {
    submitOrder,
    cancelOrder,
    executing,
    error,
    step,
    txHash,
  };
}
