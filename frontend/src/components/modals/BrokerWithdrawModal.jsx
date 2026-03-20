import React, { useState } from "react";
import { X, ArrowUp, Loader2, AlertTriangle } from "lucide-react";
import { ethers } from "ethers";
import { getAnvilSigner, restoreAnvilChainId } from "../../utils/anvil";

/**
 * Broker Withdraw modal — calls broker.withdrawCollateral / withdrawPositionToken.
 *
 * Matches SwapConfirmModal / CreateBondModal design system.
 * Shows broker balance, amount input, and solvency warning.
 */
export default function BrokerWithdrawModal({
  isOpen,
  onClose,
  brokerAddress,
  tokenSymbol, // "waUSDC" or "wRLP"
  brokerTokenBalance, // current broker balance of this token (float)
  txPauseRef,
  onSuccess,
}) {
  const [amount, setAmount] = useState("");
  const [executing, setExecuting] = useState(false);
  const [executionStep, setExecutionStep] = useState("");
  const [executionError, setExecutionError] = useState("");

  if (!isOpen) return null;

  const parsedAmount = parseFloat(amount) || 0;
  const maxBalance = brokerTokenBalance ?? 0;
  const isValid = parsedAmount > 0 && parsedAmount <= maxBalance;

  // Pick the right withdraw function based on token
  const withdrawFn =
    tokenSymbol === "waUSDC"
      ? "function withdrawCollateral(address recipient, uint256 amount) external"
      : "function withdrawPositionToken(address recipient, uint256 amount) external";

  const handleWithdraw = async () => {
    if (!isValid) return;
    setExecuting(true);
    setExecutionError("");
    if (txPauseRef) txPauseRef.current = true;
    try {
      const signer = await getAnvilSigner();
      const userAddr = await signer.getAddress();
      const broker = new ethers.Contract(brokerAddress, [withdrawFn], signer);

      setExecutionStep("Sending withdraw...");
      const raw = ethers.parseUnits(amount, 6);
      const fnName =
        tokenSymbol === "waUSDC"
          ? "withdrawCollateral"
          : "withdrawPositionToken";
      const tx = await broker[fnName](userAddr, raw, { gasLimit: 300_000n });

      setExecutionStep("Waiting for confirmation...");
      await tx.wait();

      // Atomic: refresh state THEN close modal
      setAmount("");
      onSuccess?.();
      onClose();
    } catch (e) {
      console.error("[Withdraw] failed:", e);
      // Detect solvency revert
      const msg = e.reason || e.shortMessage || "Withdraw failed";
      if (msg.includes("Insolvent") || msg.includes("!solv")) {
        setExecutionError(
          "Withdrawal would make your broker insolvent. Reduce amount or repay debt first.",
        );
      } else {
        setExecutionError(msg);
      }
    } finally {
      setExecuting(false);
      setExecutionStep("");
      if (txPauseRef) txPauseRef.current = false;
      await restoreAnvilChainId();
    }
  };


  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={!executing ? onClose : undefined}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-orange-500 shadow-[0_0_8px_rgba(249,115,22,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Withdraw_{tokenSymbol}
            </h2>
          </div>
          {!executing && (
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-white transition-colors rounded-none"
            >
              <X size={18} />
            </button>
          )}
        </div>

        <div className="p-6 flex flex-col gap-0">
          {/* From: Broker */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
              From Broker
            </div>
            <div className="flex items-center gap-3">
              <input
                type="text"
                inputMode="decimal"
                value={amount}
                onChange={(e) => {
                  const v = e.target.value;
                  if (/^[0-9]*\.?[0-9]*$/.test(v)) setAmount(v);
                }}
                placeholder="0.00"
                className="flex-1 bg-transparent text-2xl font-light text-white tracking-tight outline-none placeholder:text-gray-700"
                disabled={executing}
              />
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {tokenSymbol}
              </span>
            </div>
            <div className="flex justify-between mt-2">
              <span className="text-[10px] text-gray-600 font-mono">
                Broker Balance:{" "}
                {maxBalance.toLocaleString(undefined, {
                  maximumFractionDigits: 2,
                })}{" "}
                {tokenSymbol}
              </span>
              {maxBalance > 0 && (
                <button
                  onClick={() => setAmount(String(maxBalance))}
                  className="text-[10px] text-orange-400 uppercase tracking-widest hover:text-orange-300 transition-colors"
                >
                  Max
                </button>
              )}
            </div>
          </div>

          {/* Arrow */}
          <div className="flex justify-center -my-2 relative z-10">
            <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-orange-400">
              <ArrowUp size={14} />
            </div>
          </div>

          {/* To: Wallet */}
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
              To Your Wallet
            </div>
            <div className="flex items-baseline justify-between">
              <span className="text-2xl font-light text-orange-400 tracking-tight">
                {parsedAmount > 0
                  ? parsedAmount.toLocaleString(undefined, {
                      maximumFractionDigits: 2,
                    })
                  : "0.00"}
              </span>
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {tokenSymbol}
              </span>
            </div>
          </div>

          {/* Detail rows */}
          <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            <div className="flex justify-between items-center px-4 py-2.5">
              <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                Action
              </span>
              <span className="text-[12px] font-mono text-gray-300">
                {tokenSymbol === "waUSDC"
                  ? "withdrawCollateral()"
                  : "withdrawPositionToken()"}
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-2.5">
              <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                Solvency
              </span>
              <span className="text-[12px] font-mono text-yellow-400">
                Checked after TX
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-2.5">
              <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                Broker
              </span>
              <span className="text-[12px] font-mono text-gray-300">
                {brokerAddress
                  ? `${brokerAddress.slice(0, 6)}…${brokerAddress.slice(-4)}`
                  : "—"}
              </span>
            </div>
          </div>
        </div>

        {/* Confirm Button */}
        <div className="px-6 pb-5">
          <button
            onClick={handleWithdraw}
            disabled={executing || !isValid}
            className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 ${
              isValid
                ? "bg-orange-500 text-black hover:bg-orange-400"
                : "bg-white/5 text-gray-600 cursor-not-allowed"
            } ${executing ? "opacity-70 cursor-wait" : ""}`}
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Processing..."}
              </>
            ) : (
              `Withdraw ${tokenSymbol}`
            )}
          </button>
        </div>

        {/* Footer Status */}
        <div className="px-6 pb-5">
          <div className="flex items-center gap-2 text-[10px] font-mono pt-2 border-t border-white/5">
            {executionError ? (
              <span className="text-red-400 flex items-center gap-1">
                <AlertTriangle size={10} />
                {executionError}
              </span>
            ) : executing ? (
              <span className="text-orange-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · Reverts if withdrawal would break solvency
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
