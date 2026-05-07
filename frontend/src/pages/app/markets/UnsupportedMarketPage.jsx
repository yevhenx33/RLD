import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

export default function UnsupportedMarketPage() {
  const { protocol, marketId } = useParams();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center gap-4 text-gray-400 font-mono">
      <span className="text-lg text-white font-bold tracking-tight uppercase">Unsupported Protocol</span>
      <span className="text-sm text-red-400">Protocol: {protocol} | Market: {marketId}</span>
      <span className="text-xs text-gray-600 mt-2">This protocol is not currently supported by the RLD dashboard.</span>
      <button onClick={() => navigate("/data")} className="mt-4 text-cyan-500 hover:text-cyan-400 flex items-center gap-2 transition-colors border border-white/10 px-4 py-2 hover:bg-white/5 rounded-sm">
        <ArrowLeft size={16} /> Return to Hub
      </button>
    </div>
  );
}
