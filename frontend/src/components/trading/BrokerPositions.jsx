import React from "react";
import { Shield, AlertTriangle, Skull } from "lucide-react";

/**
 * Displays a table of active broker positions from the simulation.
 */
export default function BrokerPositions({ brokers = [] }) {
  if (brokers.length === 0) {
    return (
      <div className="text-sm text-gray-600 uppercase tracking-widest text-center py-6">
        No active positions
      </div>
    );
  }

  const getHealthColor = (hf) => {
    if (hf >= 3) return "text-green-500";
    if (hf >= 1.5) return "text-yellow-500";
    return "text-red-500";
  };

  const getHealthIcon = (hf) => {
    if (hf >= 3) return <Shield size={10} className="text-green-500" />;
    if (hf >= 1.5)
      return <AlertTriangle size={10} className="text-yellow-500" />;
    return <Skull size={10} className="text-red-500" />;
  };

  return (
    <div className="space-y-0 divide-y divide-white/5">
      {brokers.map((b) => (
        <div
          key={b.address}
          className="py-3 flex items-center justify-between gap-3"
        >
          {/* Left: label + address */}
          <div className="flex-1 min-w-0">
            <div className="text-sm text-white font-bold tracking-wider truncate">
              {b.label}
            </div>
            <div className="text-sm text-gray-600 font-mono truncate">
              {b.address}
            </div>
          </div>

          {/* Center: collateral / debt */}
          <div className="text-right flex-shrink-0">
            <div className="text-sm text-gray-400 font-mono">
              {formatCompact(b.collateral)}
              <span className="text-gray-600"> / </span>
              {formatCompact(b.debtValue)}
            </div>
          </div>

          {/* Right: health factor */}
          <div
            className={`flex items-center gap-1 flex-shrink-0 ${getHealthColor(b.healthFactor)}`}
          >
            {getHealthIcon(b.healthFactor)}
            <span className="text-sm font-mono font-bold">
              {b.healthFactor.toFixed(2)}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function formatCompact(val) {
  if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
  if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
  if (val >= 1e3) return `$${(val / 1e3).toFixed(1)}K`;
  return `$${val.toFixed(0)}`;
}
