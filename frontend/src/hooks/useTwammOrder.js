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
    { name: "nonce", type: "uint256" },
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
  {
    name: "claimExpiredTwammOrder",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [],
    outputs: [
      { name: "claimed0", type: "uint256" },
      { name: "claimed1", type: "uint256" },
    ],
  },
  {
    name: "claimExpiredTwammOrderWithKey",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "twammHook", type: "address" },
      POOL_KEY_TUPLE,
      ORDER_KEY_TUPLE,
    ],
    outputs: [
      { name: "claimed0", type: "uint256" },
      { name: "claimed1", type: "uint256" },
    ],
  },
  {
    name: "setActiveTwammOrder",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      {
        name: "info",
        type: "tuple",
        components: [
          POOL_KEY_TUPLE,
          ORDER_KEY_TUPLE,
          { name: "orderId", type: "bytes32" },
        ],
      },
    ],
    outputs: [],
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
  { onRefreshComplete = [] } = {},
) {
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState(null);
  const [step, setStep] = useState("");
  const [txHash, setTxHash] = useState(null);

   
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
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
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
        await restoreAnvilChainId();
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
   * If untracked, uses claimExpiredTwammOrderWithKey(hook, key, orderKey)
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
        const signer = await getAnvilSigner();
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
          // Untracked order: must pass explicit key data
          if (!infrastructure?.twamm_hook || !collateralAddr || !positionAddr) {
            setError("Missing pool/hook addresses for untracked claim");
            setExecuting(false);
            return;
          }
          const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
          const orderKey = {
            owner: brokerAddress,
            expiration: BigInt(order.expiration),
            zeroForOne: order.zeroForOne,
            nonce: BigInt(order.nonce ?? 0),
          };
          console.log("[TWAMM] claimWithKey params:", {
            hook: infrastructure.twamm_hook,
            poolKey,
            orderKey: { ...orderKey, expiration: orderKey.expiration.toString(), nonce: orderKey.nonce.toString() },
          });
          tx = await broker.claimExpiredTwammOrderWithKey(
            infrastructure.twamm_hook,
            poolKey,
            orderKey,
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
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
  );

  /**
   * Track a TWAMM order as collateral by calling setActiveTwammOrder.
   *
   * @param {object} order  Enriched order from useTwammPositions (needs expiration, zeroForOne, orderId)
   * @param {Function} onSuccess Called on success
   */
  const trackTwammOrder = useCallback(
    async (order, onSuccess) => {
      if (!account || !brokerAddress || !infrastructure?.twamm_hook) {
        setError("Missing required addresses");
        return;
      }
      if (!window.ethereum) { setError("MetaMask not found"); return; }

      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Tracking TWAMM order as collateral...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(brokerAddress, PRIME_BROKER_TWAMM_ABI, signer);
        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);

        const orderId = order.orderId.startsWith("0x") ? order.orderId : `0x${order.orderId}`;
        const info = {
          key: poolKey,
          orderKey: {
            owner: brokerAddress,
            expiration: BigInt(order.expiration),
            zeroForOne: order.zeroForOne,
          },
          orderId,
        };

        setStep("Confirm in wallet...");
        const tx = await broker.setActiveTwammOrder(info, { gasLimit: 500_000n });
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
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
  );

  /**
   * Untrack the active TWAMM order from collateral.
   *
   * @param {Function} onSuccess Called on success
   */
  const untrackTwammOrder = useCallback(
    async (onSuccess) => {
      if (!account || !brokerAddress || !infrastructure?.twamm_hook) {
        setError("Missing required addresses");
        return;
      }
      if (!window.ethereum) { setError("MetaMask not found"); return; }

      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Untracking TWAMM order...");

      try {
        const signer = await getAnvilSigner();
        const broker = new ethers.Contract(brokerAddress, PRIME_BROKER_TWAMM_ABI, signer);

        // Pass zeroed-out info to clear the active order
        const emptyInfo = {
          key: {
            currency0: ethers.ZeroAddress,
            currency1: ethers.ZeroAddress,
            fee: 0,
            tickSpacing: 0,
            hooks: ethers.ZeroAddress,
          },
          orderKey: {
            owner: ethers.ZeroAddress,
            expiration: 0n,
            zeroForOne: false,
          },
          orderId: ethers.ZeroHash,
        };

        setStep("Confirm in wallet...");
        const tx = await broker.setActiveTwammOrder(emptyInfo, { gasLimit: 300_000n });
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
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, _syncAndNotify],
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
