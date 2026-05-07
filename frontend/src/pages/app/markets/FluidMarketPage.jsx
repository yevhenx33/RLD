import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

export default function FluidMarketPage() {
  const { marketId } = useParams();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center gap-4 text-gray-400 font-mono">
      <span className="text-lg text-white font-bold tracking-tight uppercase">Fluid Market</span>
      <span className="text-sm text-cyan-400">{marketId}</span>
      <span className="text-xs text-gray-600 mt-2">Data sourcing and UI pending...</span>
      <button onClick={() => navigate(-1)} className="mt-4 text-cyan-500 hover:text-cyan-400 flex items-center gap-2 transition-colors border border-white/10 px-4 py-2 hover:bg-white/5 rounded-sm">
        <ArrowLeft size={16} /> Return
      </button>
    </div>
  );
}
