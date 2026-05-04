import React from "react";
import { Activity } from "lucide-react";
import { formatOpAmount } from "../../hooks/useOperations";

// eslint-disable-next-line no-unused-vars
export function SimMetricBox({ label, value, sub, Icon = Activity, dimmed }) {
  return (
    <div
      className={`p-4 md:p-6 flex flex-col justify-between h-full min-h-[120px] md:min-h-[180px] ${dimmed ? "opacity-30" : ""
        }`}
    >
      <div className="text-sm text-gray-500 uppercase tracking-widest mb-2 flex justify-between">
        {label} <Icon size={15} className="opacity-90" />
      </div>
      <div>
        <div className="text-2xl md:text-3xl font-light text-white mb-1 md:mb-2 tracking-tight">
          {value}
        </div>
        <div className="text-sm text-gray-500 uppercase tracking-widest">
          {sub}
        </div>
      </div>
    </div>
  );
}

export function OperationsFeed({
  operations = [],
  loading = false,
  connected = false,
  collateralSymbol = "waUSDC",
  positionSymbol = "wRLP",
}) {
  if (!connected) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-4">
        —
      </div>
    );
  }

  if (loading && operations.length === 0) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-4">
        Loading...
      </div>
    );
  }

  if (operations.length === 0) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-4">
        No operations yet
      </div>
    );
  }

  return (
    <div className="space-y-0 divide-y divide-white/5 max-h-[280px] overflow-y-auto custom-scrollbar">
      {operations.slice(0, 15).map((op) => {
        // Format amounts based on event type
        let detail = "";
        if (op.type === "SwapExecuted" && Number(op.args.action) === 1) {
          detail = `${formatOpAmount(op.args.amountIn)} ${collateralSymbol} → ${formatOpAmount(op.args.amountOut)} ${positionSymbol}`;
        } else if (op.type === "SwapExecuted" && Number(op.args.action) === 2) {
          detail = `${formatOpAmount(op.args.amountIn)} ${positionSymbol} → ${formatOpAmount(op.args.amountOut)} ${collateralSymbol}`;
        } else if (op.type === "ShortPositionUpdated") {
          detail = `${formatOpAmount(op.args[1])} debt · ${formatOpAmount(op.args[2])} proceeds`;
        } else if (op.type === "ShortPositionClosed") {
          detail = `${formatOpAmount(op.args[1])} repaid · ${formatOpAmount(op.args[2])} spent`;
        } else if (op.type === "Deposited") {
          detail = `${formatOpAmount(op.args[1])} → ${formatOpAmount(op.args[2])} ${collateralSymbol}`;
        }

        return (
          <div key={op.id} className="py-2.5 flex items-center gap-3">
            {/* Left: Action badge (centered) */}
            <span
              className={`text-xs font-bold font-mono px-2 py-1 tracking-wider text-center shrink-0 w-[90px] ${op.color}`}
            >
              {op.label}
            </span>
            {/* Right: Detail */}
            <div className="flex-1 min-w-0 text-right">
              {detail && (
                <div className="text-sm font-mono text-gray-300 truncate">
                  {detail}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
