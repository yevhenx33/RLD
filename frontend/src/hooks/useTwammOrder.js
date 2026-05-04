import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getSigner } from "../utils/connection";
import { debugLog } from "../utils/debugLogger";

// ── ABI fragments ────────────────────────────────────────────────

const PRIME_BROKER_TWAMM_ABI = [
  "function submitTwammOrder(address twapEngine, bytes32 marketId, bool zeroForOne, uint256 duration, uint256 amountIn) external returns (bytes32 orderId)",
  "function cancelTwammOrder() external returns (uint256 buyTokensOut, uint256 sellTokensRefund)",
  "function claimExpiredTwammOrder() external returns (uint256 claimedBuyToken)",
  "function claimExpiredTwammOrderWithId(address twapEngine, bytes32 marketId, bytes32 orderId) external returns (uint256 claimedBuyToken)",
  "function setActiveTwammOrder(address twapEngine, (bytes32 marketId, bytes32 orderId) info) external",
];

// ── Helpers ───────────────────────────────────────────────────────

function getTwapEngine(infra) {
  return infra?.twapEngine || infra?.twap_engine || null;
}

function getTwammMarketId(marketId, infra) {
  return infra?.poolId || infra?.pool_id || marketId || null;
}

function normalizeOrderId(orderId) {
  if (!orderId) return null;
  const value = String(orderId);
  const hex = value.startsWith("0x") ? value : `0x${value}`;
  return /^0x[0-9a-fA-F]{64}$/.test(hex) ? hex : null;
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
  marketId,
  infrastructure,
  collateralAddr,
  positionAddr,
  { onRefreshComplete = [] } = {},
) {
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [txHash, setTxHash] = useState(null);
  const twapEngine = getTwapEngine(infrastructure);
  const twammMarketId = getTwammMarketId(marketId, infrastructure);

   
  const _syncAndNotify = useCallback(async (successStep, onSuccess, receipt) => {
    setStep("Syncing...");
    await Promise.all(onRefreshComplete.map(fn => fn?.()).filter(Boolean));
    setStep(successStep);
    if (onSuccess) onSuccess(receipt);
  }, [onRefreshComplete]);

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
      const missing = [];
      if (!account) missing.push("account");
      if (!brokerAddress) missing.push("broker");
      if (!twapEngine) missing.push("twapEngine");
      if (!twammMarketId) missing.push("marketId/poolId");
      if (missing.length > 0) {
        setError(`Missing required addresses: ${missing.join(", ")}`);
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
        const signer = await getSigner();
        const broker = new ethers.Contract(
          brokerAddress,
          PRIME_BROKER_TWAMM_ABI,
          signer,
        );

        const amountInWei = ethers.parseUnits(String(amountIn), 6);
        // Option E (deferred start): the contract starts at the next epoch
        // boundary and streams for exactly `duration` seconds. No extra
        // padding needed — the user gets precise duration.
        const EXPIRATION_INTERVAL = 3600n; // must match JTM's expirationInterval
        const durationSeconds = BigInt(Math.round(durationHours)) * EXPIRATION_INTERVAL;

        debugLog("[TWAMM] submitTwammOrder params:", {
          twapEngine,
          marketId: twammMarketId,
          zeroForOne,
          durationSeconds: durationSeconds.toString(),
          amountIn: amountInWei.toString(),
        });

        setStep("Confirm TWAMM order in wallet...");
        const tx = await broker.submitTwammOrder(
          twapEngine,
          twammMarketId,
          zeroForOne,
          durationSeconds,
          amountInWei,
          { gasLimit: 2_000_000n },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Order submitted ✓", onSuccess, receipt);
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
        setExecuting(false);
      }
    },
    [account, brokerAddress, twammMarketId, twapEngine, _syncAndNotify],
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
        const signer = await getSigner();
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
          await _syncAndNotify("Order cancelled ✓", onSuccess, receipt);
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
        setExecuting(false);
      }
    },
    [account, brokerAddress, _syncAndNotify],
  );

  /**
   * Claim tokens from an expired TWAMM order via PrimeBroker.
   *
   * If the order is currently tracked (activeTwammOrder), uses the simpler
   * claimExpiredTwammOrder() which reads from storage.
   * If untracked, uses claimExpiredTwammOrderWithId(twapEngine, marketId, orderId)
   * which can claim any order owned by this broker.
   *
   * @param {object}   order     Enriched order object (needs .tracked, .expiration, .zeroForOne)
   * @param {Function} onSuccess Called with tx receipt on success
   */
  const claimExpiredOrder = useCallback(
    async (order, onSuccess) => {
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
      setStep("Claiming expired TWAMM order...");

      try {
        const signer = await getSigner();
        const broker = new ethers.Contract(
          brokerAddress,
          PRIME_BROKER_TWAMM_ABI,
          signer,
        );

        let tx;
        setStep("Confirm claim in wallet...");

        if (order?.tracked) {
          // Tracked order: use parameterless version (reads activeTwammOrder from storage)
          tx = await broker.claimExpiredTwammOrder({ gasLimit: 1_000_000n });
        } else {
          // Untracked order: claim by explicit order id
          const orderId = normalizeOrderId(order?.orderId);
          if (!twapEngine || !twammMarketId || !orderId) {
            setError("Missing twap engine, market id, or order id");
            setExecuting(false);
            return;
          }
          debugLog("[TWAMM] claimWithId params:", {
            twapEngine,
            marketId: twammMarketId,
            orderId,
          });
          tx = await broker.claimExpiredTwammOrderWithId(
            twapEngine,
            twammMarketId,
            orderId,
            { gasLimit: 1_000_000n },
          );
        }
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Tokens claimed ✓", onSuccess, receipt);
        } else {
          setError("Claim reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[TWAMM] claimExpiredOrder failed:", e);
        let msg = "Claim failed";
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
        setExecuting(false);
      }
    },
    [account, brokerAddress, twammMarketId, twapEngine, _syncAndNotify],
  );

  /**
   * Track a TWAMM order as collateral by calling setActiveTwammOrder.
   *
   * @param {object} order  Enriched order from useTwammPositions (needs expiration, zeroForOne, orderId)
   * @param {Function} onSuccess Called on success
   */
  const trackTwammOrder = useCallback(
    async (order, onSuccess) => {
      if (!account || !brokerAddress || !twapEngine || !twammMarketId) {
        setError("Missing required addresses");
        return;
      }
      if (!window.ethereum) { setError("MetaMask not found"); return; }

      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Tracking TWAMM order as collateral...");

      try {
        const signer = await getSigner();
        const broker = new ethers.Contract(brokerAddress, PRIME_BROKER_TWAMM_ABI, signer);
        const orderId = normalizeOrderId(order?.orderId);
        if (!orderId) {
          throw new Error("Invalid order id");
        }
        const info = {
          marketId: twammMarketId,
          orderId,
        };

        setStep("Confirm in wallet...");
        const tx = await broker.setActiveTwammOrder(twapEngine, info, { gasLimit: 500_000n });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Order tracked ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[TWAMM] trackOrder failed:", e);
        const reason = e.revert?.args?.[0] || e.reason || e.shortMessage || e.message;
        setError(reason || "Track order failed");
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, twammMarketId, twapEngine, _syncAndNotify],
  );

  /**
   * Untrack the active TWAMM order from collateral.
   *
   * @param {Function} onSuccess Called on success
   */
  const untrackTwammOrder = useCallback(
    async (onSuccess) => {
      if (!account || !brokerAddress || !twapEngine || !twammMarketId) {
        setError("Missing required addresses");
        return;
      }
      if (!window.ethereum) { setError("MetaMask not found"); return; }

      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Untracking TWAMM order...");

      try {
        const signer = await getSigner();
        const broker = new ethers.Contract(brokerAddress, PRIME_BROKER_TWAMM_ABI, signer);

        // Pass zeroed-out info to clear the active order
        const emptyInfo = {
          marketId: twammMarketId,
          orderId: ethers.ZeroHash,
        };

        setStep("Confirm in wallet...");
        const tx = await broker.setActiveTwammOrder(twapEngine, emptyInfo, { gasLimit: 300_000n });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Order untracked ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("[TWAMM] untrackOrder failed:", e);
        const reason = e.revert?.args?.[0] || e.reason || e.shortMessage || e.message;
        setError(reason || "Untrack order failed");
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, twammMarketId, twapEngine, _syncAndNotify],
  );

  return {
    submitOrder,
    cancelOrder,
    claimExpiredOrder,
    trackTwammOrder,
    untrackTwammOrder,
    executing,
    error,
    step,
    txHash,
  };
}
