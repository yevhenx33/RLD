import React, { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Plus, Wallet, X } from "lucide-react";

export default function CreateBrokerConfirmModal({
  isOpen,
  onClose,
  onConfirm,
  creating = false,
  depositing = false,
  step = "",
  error = "",
  collateralSymbol = "waUSDC",
}) {
  const [depositAmount, setDepositAmount] = useState("");

  useEffect(() => {
    if (isOpen) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDepositAmount("");
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const executing = creating || depositing;
  const parsedAmount = Number(depositAmount || 0);
  const hasDeposit = Number.isFinite(parsedAmount) && parsedAmount > 0;
  const statusText = error || (executing ? step : "");

  const handleAmountChange = (event) => {
    const next = event.target.value;
    if (/^[0-9]*\.?[0-9]*$/.test(next)) {
      setDepositAmount(next);
    }
  };

  const handleConfirm = () => {
    if (executing) return;
    onConfirm?.(depositAmount.trim());
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={!executing ? onClose : undefined}
      />

      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Create_Broker
            </h2>
          </div>
          {!executing && (
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-white transition-colors rounded-none"
              aria-label="Close create broker modal"
            >
              <X size={18} />
            </button>
          )}
        </div>

        <div className="p-6 flex flex-col gap-4">
          <div className="border border-white/10 bg-white/[0.02] p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-[10px] text-gray-500 uppercase tracking-widest">
                Initial Deposit
              </div>
              <Wallet size={15} className="text-gray-600" />
            </div>
            <div className="flex items-center gap-3">
              <input
                type="text"
                inputMode="decimal"
                value={depositAmount}
                onChange={handleAmountChange}
                placeholder="0.00"
                className="flex-1 bg-transparent text-2xl font-light text-white tracking-tight outline-none placeholder:text-gray-700"
                disabled={executing}
                autoFocus
              />
              <span className="text-xs text-gray-500 uppercase tracking-widest">
                {collateralSymbol}
              </span>
            </div>
            <div className="mt-2 text-[10px] text-gray-600 font-mono">
              Leave blank to create broker without deposit.
            </div>
          </div>

          <div className="border border-white/10 bg-white/[0.02] divide-y divide-white/5">
            <div className="flex justify-between items-center px-4 py-2.5">
              <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                Action
              </span>
              <span className="text-[12px] font-mono text-gray-300">
                Create broker
              </span>
            </div>
            <div className="flex justify-between items-center px-4 py-2.5">
              <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                Deposit
              </span>
              <span className="text-[12px] font-mono text-gray-300">
                {hasDeposit ? `${depositAmount} ${collateralSymbol}` : "None"}
              </span>
            </div>
          </div>

          {statusText && (
            <div className={`border px-4 py-3 text-xs font-mono ${error ? "border-red-500/30 bg-red-500/10 text-red-300" : "border-cyan-500/30 bg-cyan-500/10 text-cyan-300"}`}>
              <div className="flex items-center gap-2">
                {error ? <AlertTriangle size={14} /> : <Loader2 size={14} className="animate-spin" />}
                <span>{statusText}</span>
              </div>
            </div>
          )}
        </div>

        <div className="px-6 pb-5 grid grid-cols-2 gap-3">
          <button
            onClick={onClose}
            disabled={executing}
            className="py-3 text-xs font-bold tracking-[0.18em] uppercase border border-white/10 text-gray-400 hover:text-white hover:bg-white/5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={executing}
            className="py-3 text-xs font-bold tracking-[0.18em] uppercase bg-cyan-400 text-black hover:bg-cyan-300 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {depositing ? "Depositing" : "Creating"}
              </>
            ) : (
              <>
                <Plus size={14} />
                Confirm
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
