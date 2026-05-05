import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getSigner } from "../utils/connection";
import { rpcProvider } from "../utils/provider";
import {
  BROKER_ROUTER_ABI,
  buildHooklessPoolKey,
} from "../lib/peripheryIntegration";

// PrimeBroker.setOperator + operators check
const BROKER_ABI = [
  "function operators(address) view returns (bool)",
  "function setOperator(address operator, bool active)",
];
const SLIPPAGE_EXCEEDED_SELECTOR = "0x8199f5f3";
const PANIC_SELECTOR = "0x4e487b71";
const PANIC_UNDERFLOW_CODE =
  "0000000000000000000000000000000000000000000000000000000000000011";

function extractRevertData(error) {
  const candidates = [
    error?.data,
    error?.error?.data,
    error?.info?.error?.data,
    error?.info?.error?.error?.data,
    error?.revert?.data,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.startsWith("0x")) {
      return value.toLowerCase();
    }
  }
  return null;
}

function isSlippageExceededError(error) {
  if (error?.revert?.name === "SlippageExceeded") return true;
  const revertData = extractRevertData(error);
  return (
    typeof revertData === "string" &&
    revertData.startsWith(SLIPPAGE_EXCEEDED_SELECTOR)
  );
}

function isExecutionRevertError(error) {
  if (error?.code === "CALL_EXCEPTION" || error?.code === "UNPREDICTABLE_GAS_LIMIT") {
    return true;
  }
  const message = `${error?.shortMessage || ""} ${error?.message || ""}`.toLowerCase();
  return message.includes("execution reverted") || message.includes("revert");
}

function formatOpenShortError(error) {
  if (isSlippageExceededError(error)) {
    return "Slippage exceeded. Increase Max_Slippage or reduce short size.";
  }
  const reason =
    error?.reason || error?.revert?.name || error?.shortMessage || error?.message;
  return reason || "Short failed";
}

function isPanicUnderflow(error) {
  const revertData = extractRevertData(error);
  if (!revertData) return false;
  return (
    revertData.startsWith(PANIC_SELECTOR) &&
    revertData.endsWith(PANIC_UNDERFLOW_CODE)
  );
}

function formatRepayDebtError(error) {
  if (isPanicUnderflow(error)) {
    return "Repay amount exceeds broker wRLP balance or outstanding debt.";
  }
  const reason =
    error?.reason || error?.revert?.name || error?.shortMessage || error?.message;
  return reason || "Debt repay failed";
}

function normalizeCallbackArgs(minOut, onSuccess) {
  if (typeof minOut === "function") {
    return { minOut: 0, onSuccess: minOut };
  }
  return { minOut, onSuccess };
}

function parseRequiredMinOut(value, label) {
  const amount = Number(value ?? 0);
  if (!Number.isFinite(amount) || amount <= 0) {
    throw new Error(`${label} unavailable. Refresh the route quote before confirming.`);
  }
  return ethers.parseUnits(String(amount), 6);
}

// ── Shared helpers ────────────────────────────────────────────────

function assertRuntimeReady(infrastructure) {
  if (
    infrastructure?.runtime_ready !== false &&
    infrastructure?.runtimeReady !== false
  ) {
    return;
  }
  const reasons =
    infrastructure?.runtimeReadiness?.reasons ||
    infrastructure?.runtime_readiness?.reasons ||
    [];
  throw new Error(
    reasons.length
      ? `Runtime not ready: ${reasons.join(", ")}`
      : "Runtime manifest is not ready",
  );
}

function buildPoolKey(infrastructure, collateralAddr, positionAddr) {
  return buildHooklessPoolKey(infrastructure, collateralAddr, positionAddr);
}

/** Ensure BrokerRouter is approved as operator on the broker. */
async function ensureOperator(brokerAddress, routerAddress, setStep) {
  const broker = new ethers.Contract(brokerAddress, BROKER_ABI, rpcProvider);
  const isOperator = await broker.operators(routerAddress);

  if (!isOperator) {
    setStep("Approving BrokerRouter as operator...");
    const signer = await getSigner();
    const brokerSigned = new ethers.Contract(brokerAddress, BROKER_ABI, signer);

    setStep("Confirm operator approval in wallet...");
    const opTx = await brokerSigned.setOperator(routerAddress, true, {
      gasLimit: 200_000,
    });
    setStep("Waiting for approval...");
    await opTx.wait();

  }
}

/** Estimate tx gas with buffer and safe fallback. */
async function estimateGasLimit(estimateFn, fallbackGasLimit) {
  const fallback = BigInt(fallbackGasLimit);
  try {
    const estimated = await estimateFn();
    if (!estimated || estimated <= 0n) return fallback;

    // Add 20% headroom for volatile paths (oracle + solvency checks).
    const buffered = (estimated * 120n) / 100n;
    return buffered > fallback ? buffered : fallback;
  } catch (error) {
    // If simulation already reports a revert, do not send a doomed tx.
    if (isExecutionRevertError(error)) throw error;
    return fallback;
  }
}

// ── Hook ──────────────────────────────────────────────────────────

/**
 * useSwapExecution — Execute trades via BrokerRouter with MetaMask signing.
 *
 * Provides:
 * - executeLong(amountIn, minAmountOut, onSuccess)  — open long: waUSDC → wRLP
 * - executeCloseLong(amountIn, minAmountOut, onSuccess) — close long: wRLP → waUSDC
 * - executeShort(collateral, debt, minProceeds, onSuccess) — open short with min-out guard
 * - executeCloseShort(amountIn, minDebtBought, onSuccess) — close short: buy wRLP + repay debt
 * - executeRepayDebt(wrlpAmount, onSuccess) — direct repay: burn wRLP to reduce debt
 */
export function useSwapExecution(
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

  // Atomic refresh: await all data refreshes before firing onSuccess
   
  const _syncAndNotify = useCallback(async (successStep, onSuccess, receipt) => {
    setStep("Syncing...");
    await Promise.all(onRefreshComplete.map(fn => fn?.()).filter(Boolean));
    setStep(successStep);
    if (onSuccess) onSuccess(receipt);
  }, [onRefreshComplete]);

  const approveRouter = useCallback(
    async (onSuccess) => {
      if (!account || !brokerAddress || !infrastructure?.broker_router) {
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
      setStep("Checking router approval...");

      try {
        assertRuntimeReady(infrastructure);
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);
        setStep("Router approved ✓");
        if (onSuccess) onSuccess();
      } catch (e) {
        console.error("Router approval failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.shortMessage || e.message || "Router approval failed";
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure],
  );

  const executeLong = useCallback(
    async (amountIn, minAmountOut, onSuccessArg) => {
      const { minOut, onSuccess } = normalizeCallbackArgs(
        minAmountOut,
        onSuccessArg,
      );
      if (
        !account ||
        !brokerAddress ||
        !infrastructure?.broker_router ||
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
      setStep("Checking operator status...");

      try {
        assertRuntimeReady(infrastructure);
        // 1. Ensure operator
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        // 2. Execute the swap via MetaMask
        setStep("Preparing swap...");
        const signer = await getSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const amountInWei = ethers.parseUnits(String(amountIn), 6);
        const minAmountOutWei = parseRequiredMinOut(
          minOut,
          "Minimum received",
        );
        const longArgs = [
          brokerAddress,
          amountInWei,
          poolKey,
          minAmountOutWei,
        ];

        setStep("Preflighting swap...");
        await router.executeLong.staticCall(...longArgs);

        setStep("Estimating gas...");
        const gasLimit = await estimateGasLimit(
          () => router.executeLong.estimateGas(...longArgs),
          1_000_000,
        );

        setStep("Confirm swap in wallet...");
        const tx = await router.executeLong(...longArgs, { gasLimit });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Swap confirmed ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("Swap execution failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.shortMessage || e.message || "Swap failed";
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
  );

  /**
   * Close Long: sell wRLP → receive waUSDC
   * @param {number} amountIn — wRLP amount (human-readable, 6 decimals)
   */
  const executeCloseLong = useCallback(
    async (amountIn, minAmountOut, onSuccessArg) => {
      const { minOut, onSuccess } = normalizeCallbackArgs(
        minAmountOut,
        onSuccessArg,
      );
      if (
        !account ||
        !brokerAddress ||
        !infrastructure?.broker_router ||
        !collateralAddr ||
        !positionAddr
      ) {
        setError("Missing required addresses");
        return;
      }
      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Checking operator status...");

      try {
        assertRuntimeReady(infrastructure);
        const amountInNum = Number(amountIn);
        if (!Number.isFinite(amountInNum) || amountInNum <= 0) {
          setError("Enter a valid wRLP amount");
          setStep("");
          return;
        }

        const amountInWei = ethers.parseUnits(String(amountInNum), 6);

        // Preflight balance check prevents on-chain TRANSFER_FAILED reverts.
        setStep("Checking broker wRLP balance...");
        const positionToken = new ethers.Contract(
          positionAddr,
          ["function balanceOf(address) view returns (uint256)"],
          rpcProvider,
        );
        const availableWei = await positionToken.balanceOf(brokerAddress);
        if (amountInWei > availableWei) {
          const maxAvailable = Number(
            ethers.formatUnits(availableWei, 6),
          ).toFixed(6);
          setError(`Insufficient wRLP balance. Max available: ${maxAvailable}`);
          setStep("");
          return;
        }

        setStep("Checking operator status...");
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        setStep("Preparing close...");
        const signer = await getSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const minAmountOutWei = parseRequiredMinOut(
          minOut,
          "Minimum received",
        );
        const closeArgs = [
          brokerAddress,
          amountInWei,
          poolKey,
          minAmountOutWei,
        ];

        setStep("Preflighting close...");
        await router.closeLong.staticCall(...closeArgs);

        setStep("Estimating gas...");
        const gasLimit = await estimateGasLimit(
          () => router.closeLong.estimateGas(...closeArgs),
          1_000_000,
        );

        setStep("Confirm close in wallet...");
        const tx = await router.closeLong(...closeArgs, { gasLimit });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Position closed ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("Close long failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.shortMessage || e.message || "Close failed";
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [
      account,
      brokerAddress,
      infrastructure,
      collateralAddr,
      positionAddr,
      _syncAndNotify,
    ],
  );

  /**
   * Open Short: deposit collateral + borrow wRLP + swap wRLP → waUSDC
   * @param {number} initialCollateral — collateral amount in USDC (human-readable, 6 decimals)
   * @param {number} targetDebtAmount — wRLP to borrow (human-readable, 6 decimals)
   * @param {number} minProceeds — minimum waUSDC proceeds required from swap (human-readable, 6 decimals)
   */
  const executeShort = useCallback(
    async (initialCollateral, targetDebtAmount, minProceeds = 0, onSuccess) => {
      if (
        !account ||
        !brokerAddress ||
        !infrastructure?.broker_router ||
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
      setStep("Checking operator status...");

      try {
        assertRuntimeReady(infrastructure);
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        setStep("Preparing short...");
        const signer = await getSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const collateralWei = ethers.parseUnits(String(initialCollateral), 6);
        const debtWei = ethers.parseUnits(String(targetDebtAmount), 6);
        const minProceedsWei = parseRequiredMinOut(
          minProceeds,
          "Minimum proceeds",
        );
        const shortArgs = [
          brokerAddress,
          collateralWei,
          debtWei,
          poolKey,
          minProceedsWei,
        ];

        // Preflight catches SlippageExceeded before wallet confirmation.
        setStep("Preflighting short...");
        await router.executeShort.staticCall(...shortArgs);

        setStep("Estimating gas...");
        const gasLimit = await estimateGasLimit(
          () => router.executeShort.estimateGas(...shortArgs),
          2_500_000,
        );

        setStep("Confirm short in wallet...");
        const tx = await router.executeShort(...shortArgs, { gasLimit });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Short opened ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("Open short failed:", e);
        let msg = "Short failed";
        if (e.code === "ACTION_REJECTED") {
          msg = "Transaction rejected";
        } else {
          msg = formatOpenShortError(e);
        }
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
  );

  /**
   * Close Short: spend waUSDC to buy wRLP and repay debt
   * @param {number} amountIn — waUSDC amount to spend (human-readable, 6 decimals)
   */
  const executeCloseShort = useCallback(
    async (amountIn, minDebtBought, onSuccessArg) => {
      const { minOut, onSuccess } = normalizeCallbackArgs(
        minDebtBought,
        onSuccessArg,
      );
      if (
        !account ||
        !brokerAddress ||
        !infrastructure?.broker_router ||
        !collateralAddr ||
        !positionAddr
      ) {
        setError("Missing required addresses");
        return;
      }
      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Checking operator status...");

      try {
        assertRuntimeReady(infrastructure);
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        setStep("Preparing close short...");
        const signer = await getSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const amountInWei = ethers.parseUnits(String(amountIn), 6);
        const minDebtBoughtWei = parseRequiredMinOut(
          minOut,
          "Minimum debt bought",
        );
        const closeShortArgs = [
          brokerAddress,
          amountInWei,
          poolKey,
          minDebtBoughtWei,
        ];

        setStep("Preflighting close short...");
        await router.closeShort.staticCall(...closeShortArgs);

        setStep("Estimating gas...");
        const gasLimit = await estimateGasLimit(
          () => router.closeShort.estimateGas(...closeShortArgs),
          1_500_000,
        );

        setStep("Confirm close short in wallet...");
        const tx = await router.closeShort(...closeShortArgs, { gasLimit });
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Short closed ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("Close short failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : e.shortMessage || e.message || "Close short failed";
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
  );

  /**
   * Direct Debt Repay: burn wRLP on broker to reduce debt (no swap)
   * @param {number} wrlpAmount — wRLP amount to repay (human-readable, 6 decimals)
   */
  const executeRepayDebt = useCallback(
    async (wrlpAmount, onSuccess) => {
      if (!account || !brokerAddress || !positionAddr) {
        setError("Missing required addresses");
        return;
      }
      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Preparing debt repayment...");

      try {
        const repayAmountNum = Number(wrlpAmount);
        if (!Number.isFinite(repayAmountNum) || repayAmountNum <= 0) {
          setError("Enter a valid wRLP repay amount");
          setStep("");
          return;
        }

        const signer = await getSigner();

        // Read marketId from broker
        const broker = new ethers.Contract(
          brokerAddress,
          [
            "function marketId() view returns (bytes32)",
            "function modifyPosition(bytes32 rawMarketId, int256 deltaCollateral, int256 deltaDebt) external",
          ],
          signer,
        );

        const rawMarketId = await broker.marketId();
        const repayWei = ethers.parseUnits(String(repayAmountNum), 6);

        // Direct repay burns wRLP from broker balance.
        setStep("Checking broker wRLP balance...");
        const positionToken = new ethers.Contract(
          positionAddr,
          ["function balanceOf(address) view returns (uint256)"],
          rpcProvider,
        );
        const availableWei = await positionToken.balanceOf(brokerAddress);
        if (repayWei > availableWei) {
          const maxAvailable = Number(
            ethers.formatUnits(availableWei, 6),
          ).toFixed(6);
          setError(
            `Insufficient broker wRLP for direct repay. Max available: ${maxAvailable}`,
          );
          setStep("");
          return;
        }

        // Preflight catches repay underflow / debt bound reverts before send.
        setStep("Preflighting debt repay...");
        await broker.modifyPosition.staticCall(rawMarketId, 0, -repayWei);

        setStep("Estimating gas...");
        const gasLimit = await estimateGasLimit(
          () => broker.modifyPosition.estimateGas(rawMarketId, 0, -repayWei),
          1_500_000,
        );

        setStep("Confirm debt repay in wallet...");
        const tx = await broker.modifyPosition(
          rawMarketId,
          0,            // no collateral change
          -repayWei,    // negative = repay debt
          { gasLimit },
        );
        setTxHash(tx.hash);

        setStep("Waiting for confirmation...");
        const receipt = await tx.wait();

        if (receipt.status === 1) {
          await _syncAndNotify("Debt repaid ✓", onSuccess, receipt);
        } else {
          setError("Transaction reverted");
          setStep("");
        }
      } catch (e) {
        console.error("Repay debt failed:", e);
        const msg =
          e.code === "ACTION_REJECTED"
            ? "Transaction rejected"
            : formatRepayDebtError(e);
        setError(msg);
        setStep("");
      } finally {
        setExecuting(false);
      }
    },
    [account, brokerAddress, positionAddr, _syncAndNotify],
  );

  return {
    approveRouter,
    executeLong,
    executeCloseLong,
    executeShort,
    executeCloseShort,
    executeRepayDebt,
    executing,
    error,
    step,
    txHash,
  };
}
