import React from "react";
import { X, ArrowDown, TrendingUp, AlertTriangle, Loader2 } from "lucide-react";

/**
 * Swap confirmation modal — matches AccountModal design system.
 *
 * Shows trade summary and requires explicit confirmation before executing.
 */
export default function SwapConfirmModal({
  isOpen,
  onClose,
  onConfirm,
  tradeSide = "LONG",
  tradeAction = "OPEN",
  collateral,
  amountOut,
  entryRate,
  liqRate,
  notional,
  shortCR,
  fee,
  executing,
  executionStep,
  executionError,
}) {
  if (!isOpen) return null;

  const isClose = tradeAction === "CLOSE";
  const isShort = tradeSide === "SHORT";
  const isOpenShort = isShort && !isClose;
  const isCloseShort = isShort && isClose;
  const isBuy = !isClose;
  const _accent = isClose ? "pink" : tradeSide === "LONG" ? "cyan" : "pink";

  // Swap labels based on action
  const payLabel = isCloseShort ? "waUSDC" : isClose ? "wRLP" : "waUSDC";
  const receiveLabel = isCloseShort ? "wRLP" : isClose ? "waUSDC" : "wRLP";
  const rateLabel = "AVG_Rate";
  const headerLabel = isCloseShort
    ? "Close_Short"
    : isOpenShort
      ? "Confirm_Short"
      : isClose
        ? `Close_${tradeSide}`
        : `Confirm_${tradeSide}`;
  const confirmLabel = isCloseShort
    ? "Close Short"
    : isOpenShort
      ? "Open Short"
      : isClose
        ? `Close ${tradeSide}`
        : `Confirm ${tradeSide}`;

  const _rows = isOpenShort
    ? [
        {
          label: "Collateral",
          value: `${Number(collateral).toLocaleString()} waUSDC`,
        },
        {
          label: "Notional",
          value: notional
            ? `$${Number(notional).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
            : "—",
          highlight: true,
        },
        {
          label: "CR",
          value: shortCR ? `${Number(shortCR).toFixed(0)}%` : "—",
        },
        {
          label: rateLabel,
          value: entryRate ? `${Number(entryRate).toFixed(4)}` : "—",
        },
        {
          label: "Liq._Rate",
          value: liqRate ? `${Number(liqRate).toFixed(4)}` : "None",
          dimmed: !liqRate,
        },
        {
          label: "Est._Fee",
          value: fee ? `$${Number(fee).toFixed(2)}` : "$0.00",
        },
      ]
    : [
        {
          label: "You_Pay",
          value: `${Number(collateral).toLocaleString()} ${payLabel}`,
        },
        {
          label: "You_Receive",
          value: amountOut
            ? `${Number(amountOut).toLocaleString(undefined, { maximumFractionDigits: 4 })} ${receiveLabel}`
            : "—",
          highlight: true,
        },
        {
          label: rateLabel,
          value: entryRate ? `${Number(entryRate).toFixed(4)}` : "—",
        },
        {
          label: "Liq._Rate",
          value: liqRate ? `${Number(liqRate).toFixed(4)}` : "None",
          dimmed: !liqRate,
        },
        {
          label: "Est._Fee",
          value: fee ? `$${Number(fee).toFixed(2)}` : "$0.00",
        },
      ];

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
            <div
              className={`w-2 h-2 ${
                isBuy
                  ? "bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]"
                  : "bg-pink-500 shadow-[0_0_8px_rgba(236,72,153,0.5)]"
              }`}
            />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              {headerLabel}
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

        {/* Trade Summary */}
        <div className="p-6 flex flex-col gap-0">
          {isOpenShort ? (
            <>
              {/* SHORT: Collateral + Debt Amount */}
              <div className="border border-white/10 bg-white/[0.02] p-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
                  Collateral
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-2xl font-light text-white tracking-tight">
                    {Number(collateral).toLocaleString()}
                  </span>
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    waUSDC
                  </span>
                </div>
              </div>

              {/* Arrow */}
              <div className="flex justify-center -my-2 relative z-10">
                <div className="w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 text-pink-400">
                  <ArrowDown size={14} />
                </div>
              </div>

              {/* Debt / Notional */}
              <div className="border border-white/10 bg-white/[0.02] p-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
                  Borrow (Debt)
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-2xl font-light tracking-tight text-pink-400">
                    {amountOut
                      ? Number(amountOut).toLocaleString(undefined, {
                          maximumFractionDigits: 6,
                        })
                      : "—"}
                  </span>
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    wRLP
                  </span>
                </div>
              </div>

              {/* Detail rows */}
              <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
                {[
                  {
                    label: "Notional",
                    value: notional
                      ? `$${Number(notional).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
                      : "—",
                    color: "text-white",
                  },
                  {
                    label: "Collateral_Ratio",
                    value: shortCR ? `${Number(shortCR).toFixed(0)}%` : "—",
                  },
                  {
                    label: rateLabel,
                    value: entryRate ? Number(entryRate).toFixed(4) : "—",
                  },
                  {
                    label: "Liq._Rate",
                    value: liqRate ? Number(liqRate).toFixed(4) : "None",
                    color: liqRate ? "text-orange-400" : "text-gray-600",
                  },
                  {
                    label: "Side",
                    value: isClose ? "CLOSE SHORT" : "OPEN SHORT",
                    color: "text-pink-400",
                  },
                ].map((row) => (
                  <div
                    key={row.label}
                    className="flex justify-between items-center px-4 py-2.5"
                  >
                    <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                      {row.label}
                    </span>
                    <span
                      className={`text-[12px] font-mono ${
                        row.color || "text-gray-300"
                      }`}
                    >
                      {row.value}
                    </span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <>
              {/* LONG: Pay section */}
              <div className="border border-white/10 bg-white/[0.02] p-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
                  You Pay
                </div>
                <div className="flex items-baseline justify-between">
                  <span className="text-2xl font-light text-white tracking-tight">
                    {Number(collateral).toLocaleString()}
                  </span>
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    {payLabel}
                  </span>
                </div>
              </div>

              {/* Arrow */}
              <div className="flex justify-center -my-2 relative z-10">
                <div
                  className={`w-8 h-8 flex items-center justify-center bg-[#080808] border border-white/10 ${
                    isBuy ? "text-cyan-400" : "text-pink-400"
                  }`}
                >
                  <ArrowDown size={14} />
                </div>
              </div>

              {/* Receive section */}
              <div className="border border-white/10 bg-white/[0.02] p-4">
                <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-2">
                  You Receive
                </div>
                <div className="flex items-baseline justify-between">
                  <span
                    className={`text-2xl font-light tracking-tight ${
                      isBuy ? "text-cyan-400" : "text-pink-400"
                    }`}
                  >
                    {amountOut
                      ? Number(amountOut).toLocaleString(undefined, {
                          maximumFractionDigits: 4,
                        })
                      : "—"}
                  </span>
                  <span className="text-xs text-gray-500 uppercase tracking-widest">
                    {receiveLabel}
                  </span>
                </div>
              </div>

              {/* Detail rows */}
              <div className="mt-4 border border-white/10 bg-white/[0.02] divide-y divide-white/5">
                {[
                  {
                    label: rateLabel,
                    value: entryRate ? Number(entryRate).toFixed(4) : "—",
                  },
                  {
                    label: "Liq._Rate",
                    value: liqRate ? Number(liqRate).toFixed(4) : "None",
                    color: liqRate ? "text-orange-400" : "text-gray-600",
                  },
                  {
                    label: "Est._Fee",
                    value: fee ? `$${Number(fee).toFixed(2)}` : "$0.00",
                  },
                  {
                    label: "Side",
                    value: `${tradeAction} ${tradeSide}`,
                    color: isClose
                      ? "text-pink-400"
                      : tradeSide === "LONG"
                        ? "text-cyan-400"
                        : "text-pink-400",
                  },
                ].map((row) => (
                  <div
                    key={row.label}
                    className="flex justify-between items-center px-4 py-2.5"
                  >
                    <span className="text-[11px] text-gray-500 uppercase tracking-widest font-mono">
                      {row.label}
                    </span>
                    <span
                      className={`text-[12px] font-mono ${
                        row.color || "text-gray-300"
                      }`}
                    >
                      {row.value}
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Confirm Button */}
        <div className="px-6 pb-5">
          <button
            onClick={onConfirm}
            disabled={executing}
            className={`w-full py-4 text-xs font-bold tracking-[0.2em] uppercase transition-all focus:outline-none rounded-none flex items-center justify-center gap-2 ${
              isClose
                ? "bg-pink-500 text-black hover:bg-pink-400"
                : tradeSide === "LONG"
                  ? "bg-cyan-500 text-black hover:bg-cyan-400"
                  : "bg-pink-500 text-black hover:bg-pink-400"
            } ${executing ? "opacity-70 cursor-wait" : "hover:opacity-90"}`}
          >
            {executing ? (
              <>
                <Loader2 size={14} className="animate-spin" />
                {executionStep || "Processing..."}
              </>
            ) : (
              confirmLabel
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
              <span className="text-cyan-400/60">{executionStep}</span>
            ) : (
              <span className="text-gray-600">
                REVIEW · Confirm details before submitting
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
