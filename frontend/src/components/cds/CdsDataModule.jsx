import React, { useState } from "react";
import CdsInterestRateChart from "../charts/CdsInterestRateChart";

export default function CdsDataModule({ collateral, durationDays, latestApy, projectionData }) {
  const [activeView, setActiveView] = useState("SIMULATION");

  // Empty state for future CDS logic

  return (
    <div className="w-full h-full border border-white/10 bg-[#080808] flex flex-col pt-4">
      {/* Module Header / Tabs */}
      <div className="px-6 flex items-center gap-6 border-b border-white/10 pb-4">
        <button
          onClick={() => setActiveView("SIMULATION")}
          className={`text-sm font-bold uppercase tracking-widest transition-colors pb-4 ${activeView === "SIMULATION" ? "text-cyan-400 border-cyan-400 -mb-[18px]" : "text-gray-500 hover:text-white=  -mb-[18px]"
            }`}
        >
          Payout Simulation
        </button>
        <button
          onClick={() => setActiveView("HISTORICAL")}
          className={`text-sm font-bold uppercase tracking-widest transition-colors pb-4 ${activeView === "HISTORICAL" ? "text-cyan-400 border-cyan-400 -mb-[18px]" : "text-gray-500 hover:text-white -mb-[18px]"
            }`}
        >
          Historical Prices
        </button>
      </div>

      {/* Module Content */}
      <div className="flex-1 relative">
        {activeView === "SIMULATION" ? (
          <div className="h-full w-full">
            <CdsInterestRateChart currentRate={latestApy} theme="cyan" />
          </div>
        ) : (
          <div className="h-full m-6 flex items-center justify-center border border-white/5 border-dashed relative">
            <p className="text-sm font-mono text-gray-600 uppercase tracking-widest text-center">
              Historical CDS data visualizing past premium costs.<br /><br />
              <span className="text-cyan-900 border border-cyan-900/40 px-2 py-1">Under Construction</span>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
