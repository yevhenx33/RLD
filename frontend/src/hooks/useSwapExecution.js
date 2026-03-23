import { useState, useCallback } from "react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../utils/anvil";
import { rpcProvider } from "../utils/provider";

// BrokerRouter ABI (executeLong + closeLong)
const POOL_KEY_TUPLE = {
  name: "poolKey",
  type: "tuple",
  components: [
    { name: "currency0", type: "address" },
    { name: "currency1", type: "address" },
    { name: "fee", type: "uint24" },
    { name: "tickSpacing", type: "int24" },
    { name: "hooks", type: "address" },
  ],
};

const BROKER_ROUTER_ABI = [
  {
    name: "executeLong",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "amountIn", type: "uint256" },
      POOL_KEY_TUPLE,
    ],
    outputs: [{ name: "amountOut", type: "uint256" }],
  },
  {
    name: "closeLong",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "amountIn", type: "uint256" },
      POOL_KEY_TUPLE,
    ],
    outputs: [{ name: "amountOut", type: "uint256" }],
  },
  {
    name: "executeShort",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "initialCollateral", type: "uint256" },
      { name: "targetDebtAmount", type: "uint256" },
      POOL_KEY_TUPLE,
    ],
    outputs: [{ name: "proceeds", type: "uint256" }],
  },
  {
    name: "closeShort",
    type: "function",
    stateMutability: "nonpayable",
    inputs: [
      { name: "broker", type: "address" },
      { name: "collateralToSpend", type: "uint256" },
      POOL_KEY_TUPLE,
    ],
    outputs: [{ name: "debtRepaid", type: "uint256" }],
  },
];

// PrimeBroker.setOperator + operators check
const BROKER_ABI = [
  "function operators(address) view returns (bool)",
  "function setOperator(address operator, bool active)",
];

// ── Shared helpers ────────────────────────────────────────────────

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

/**
 * Ensure BrokerRouter is approved as operator on the broker.
 * Uses getAnvilSigner for the approval tx if needed.
 */
async function ensureOperator(brokerAddress, routerAddress, setStep) {
  const broker = new ethers.Contract(brokerAddress, BROKER_ABI, rpcProvider);
  const isOperator = await broker.operators(routerAddress);

  if (!isOperator) {
    setStep("Approving BrokerRouter as operator...");
    const signer = await getAnvilSigner();
    const brokerSigned = new ethers.Contract(brokerAddress, BROKER_ABI, signer);

    setStep("Confirm operator approval in wallet...");
    const opTx = await brokerSigned.setOperator(routerAddress, true, {
      gasLimit: 200_000,
    });
    setStep("Waiting for approval...");
    await opTx.wait();

    // Restore chainId between operator approval and the main tx
    await restoreAnvilChainId();
  }
}

// ── Hook ──────────────────────────────────────────────────────────

/**
 * useSwapExecution — Execute trades via BrokerRouter with MetaMask signing.
 *
 * Provides:
 * - executeLong(amountIn, onSuccess)  — open long: waUSDC → wRLP
 * - executeCloseLong(amountIn, onSuccess) — close long: wRLP → waUSDC
 * - executeShort(collateral, debt, onSuccess) — open short: deposit + borrow + sell wRLP
 * - executeCloseShort(amountIn, onSuccess) — close short: buy wRLP + repay debt
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

  const executeLong = useCallback(
    async (amountIn, onSuccess) => {
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
        // 1. Ensure operator
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        // 2. Execute the swap via MetaMask
        setStep("Preparing swap...");
        const signer = await getAnvilSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const amountInWei = ethers.parseUnits(String(amountIn), 6);

        setStep("Confirm swap in wallet...");
        const tx = await router.executeLong(
          brokerAddress,
          amountInWei,
          poolKey,
          { gasLimit: 1_000_000 },
        );
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
        await restoreAnvilChainId();
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
    async (amountIn, onSuccess) => {
      if (!account || !brokerAddress || !infrastructure?.broker_router) return;
      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Checking operator status...");

      try {
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        setStep("Preparing close...");
        const signer = await getAnvilSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const amountInWei = ethers.parseUnits(String(amountIn), 6);

        setStep("Confirm close in wallet...");
        const tx = await router.closeLong(brokerAddress, amountInWei, poolKey, {
          gasLimit: 1_000_000,
        });
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
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, infrastructure, collateralAddr, positionAddr, _syncAndNotify],
  );

  /**
   * Open Short: deposit collateral + borrow wRLP + swap wRLP → waUSDC
   * @param {number} initialCollateral — collateral amount in USDC (human-readable, 6 decimals)
   * @param {number} targetDebtAmount — wRLP to borrow (human-readable, 6 decimals)
   */
  const executeShort = useCallback(
    async (initialCollateral, targetDebtAmount, onSuccess) => {
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
      setStep("Checking operator status...");

      try {
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        setStep("Preparing short...");
        const signer = await getAnvilSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const collateralWei = ethers.parseUnits(String(initialCollateral), 6);
        const debtWei = ethers.parseUnits(String(targetDebtAmount), 6);

        setStep("Confirm short in wallet...");
        const tx = await router.executeShort(
          brokerAddress,
          collateralWei,
          debtWei,
          poolKey,
          { gasLimit: 1_500_000 },
        );
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
          const reason = e.reason || e.revert?.name || e.shortMessage || e.message;
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
   * Close Short: spend waUSDC to buy wRLP and repay debt
   * @param {number} amountIn — waUSDC amount to spend (human-readable, 6 decimals)
   */
  const executeCloseShort = useCallback(
    async (amountIn, onSuccess) => {
      if (!account || !brokerAddress || !infrastructure?.broker_router) return;
      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Checking operator status...");

      try {
        await ensureOperator(brokerAddress, infrastructure.broker_router, setStep);

        setStep("Preparing close short...");
        const signer = await getAnvilSigner();

        const router = new ethers.Contract(
          infrastructure.broker_router,
          BROKER_ROUTER_ABI,
          signer,
        );

        const poolKey = buildPoolKey(infrastructure, collateralAddr, positionAddr);
        const amountInWei = ethers.parseUnits(String(amountIn), 6);

        setStep("Confirm close short in wallet...");
        const tx = await router.closeShort(
          brokerAddress,
          amountInWei,
          poolKey,
          {
            gasLimit: 1_500_000,
          },
        );
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
        await restoreAnvilChainId();
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
      if (!account || !brokerAddress) return;
      setExecuting(true);
      setError(null);
      setTxHash(null);
      setStep("Preparing debt repayment...");

      try {
        const signer = await getAnvilSigner();

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
        const repayWei = ethers.parseUnits(String(wrlpAmount), 6);

        setStep("Confirm debt repay in wallet...");
        const tx = await broker.modifyPosition(
          rawMarketId,
          0,            // no collateral change
          -repayWei,    // negative = repay debt
          { gasLimit: 1_500_000 },
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
            : e.shortMessage || e.message || "Debt repay failed";
        setError(msg);
        setStep("");
      } finally {
        await restoreAnvilChainId();
        setExecuting(false);
      }
    },
    [account, brokerAddress, _syncAndNotify],
  );

  return {
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
