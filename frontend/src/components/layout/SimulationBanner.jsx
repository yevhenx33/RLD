import React from "react";
import { Radio, Wifi, WifiOff } from "lucide-react";

/**
 * Thin status banner showing simulation connection status.
 */
export default function SimulationBanner({
  connected = false,
  blockNumber = null,
  totalBlocks = 0,
  totalEvents = 0,
  blockChanged = false,
}) {
  return (
    <div className="border-b border-white/10 bg-[#0a0a0a] px-6 py-2 flex items-center justify-between text-[10px] uppercase tracking-widest font-mono">
      {/* Left: connection status */}
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-1.5">
          {connected ? (
            <>
              <div className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
              <Wifi size={10} className="text-green-500" />
            </>
          ) : (
            <>
              <div className="w-1.5 h-1.5 bg-red-500 rounded-full" />
              <WifiOff size={10} className="text-red-500" />
            </>
          )}
        </div>
        <span className="text-cyan-400 font-bold flex items-center gap-1.5">
          <Radio size={10} />
          Simulation Mode
        </span>
      </div>

      {/* Right: block & stats */}
      <div className="flex items-center gap-4 text-gray-500">
        {blockNumber && (
          <span
            className={`transition-colors duration-300 ${blockChanged ? "text-cyan-400" : ""}`}
          >
            Block:{" "}
            <span className="text-white">{blockNumber.toLocaleString()}</span>
          </span>
        )}
        {totalBlocks > 0 && (
          <span className="hidden md:inline">
            Indexed:{" "}
            <span className="text-white">{totalBlocks.toLocaleString()}</span>
          </span>
        )}
        {totalEvents > 0 && (
          <span className="hidden md:inline">
            Events:{" "}
            <span className="text-white">{totalEvents.toLocaleString()}</span>
          </span>
        )}
      </div>
    </div>
  );
}
