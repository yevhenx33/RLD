import React from "react";
import { ArrowUpRight, CheckCircle } from "lucide-react";

// --- HELPERS ---
const formatDate = (isoString) => {
  if (!isoString) return "N/A";
  return new Date(isoString).toLocaleDateString("en-GB", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};

const calculateRemainingDays = (maturityDate) => {
  const now = new Date();
  const maturity = new Date(maturityDate);
  const diffTime = maturity - now;
  const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
  return diffDays > 0 ? diffDays : 0;
};

const TOKEN_LOGOS = {
  USDC: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48/logo.png",
  DAI: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0x6B175474E89094C44Da98b954EedeAC495271d0F/logo.png",
  USDT: "https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/ethereum/assets/0xdAC17F958D2ee523a2206206994597C13D831ec7/logo.png",
};

const BondCard = ({ nft }) => {
  const isMatured = nft.status === "MATURED";
  const daysRemaining = calculateRemainingDays(nft.maturityDate);
  const TokenIcon = TOKEN_LOGOS[nft.currency] || TOKEN_LOGOS["USDC"]; // Fallback

  return (
    <div className="group relative h-full bg-[#0a0a0a] border border-white/10 hover:border-white/20 transition-colors flex flex-col w-full max-w-[400px] mx-auto md:max-w-none">
      {/* Header: Identity with distinct background */}
      <div className="bg-white/5 px-5 py-3 border-b border-white/5 flex justify-between items-center">
        <div className="flex items-center gap-2">
          <img
            src={TokenIcon}
            alt={nft.currency}
            className="w-5 h-5 rounded-full"
          />
          <span className="font-mono text-sm text-gray-200 font-medium">
            {nft.currency} Bond
          </span>
        </div>
        <span className="font-mono text-xs text-gray-500">#{nft.tokenId}</span>
      </div>

      {/* Body: Structured Data with Dividers */}
      <div className="flex-1 px-5 py-2 flex flex-col divide-y divide-white/5">
        {/* Row 1: Fixed APY - The Anchor */}
        <div className="py-4">
          <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
            Fixed APY
          </div>
          <div className="text-3xl text-cyan-400 font-mono font-light tracking-tight">
            {nft.rate.toFixed(2)}%
          </div>
        </div>

        {/* Row 2: Principal & Maturity */}
        <div className="py-4 grid grid-cols-2 gap-4">
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Principal
            </div>
            <div className="text-base text-white font-mono">
              {Number(nft.principal).toLocaleString()} {nft.currency}
            </div>
          </div>
          <div className="text-right">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Maturity
            </div>
            <div className="text-base text-gray-300 font-mono">
              {formatDate(nft.maturityDate)}
            </div>
          </div>
        </div>

        {/* Row 3: Protocol & Expiration */}
        <div className="py-4 mt-auto grid grid-cols-2 gap-4">
          <div>
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Protocol
            </div>
            <div className="text-sm text-gray-400 font-mono">AAVE V3</div>
          </div>
          <div className="text-right">
            <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
              Expiration
            </div>
            <div
              className={`text-sm font-mono ${
                daysRemaining <= 30 && !isMatured
                  ? "text-yellow-500"
                  : "text-gray-400"
              }`}
            >
              {isMatured ? (
                <span className="text-green-500">Ready</span>
              ) : (
                <span>{daysRemaining} Days</span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Footer: Action */}
      <div className="px-5 py-4 bg-[#050505] border-t border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div
            className={`w-1.5 h-1.5 rounded-full ${isMatured ? "bg-green-500" : "bg-cyan-500"}`}
          />
          <span
            className={`text-[10px] uppercase tracking-widest font-medium ${isMatured ? "text-green-500" : "text-cyan-500"}`}
          >
            {isMatured ? "Matured" : "Active"}
          </span>
        </div>

        <button
          disabled={!isMatured}
          className={`text-[10px] uppercase tracking-[0.15em] font-bold flex items-center gap-1 transition-colors
          ${
            isMatured
              ? "text-white hover:text-green-400 hover:underline decoration-green-500/50 underline-offset-4"
              : "text-gray-600 cursor-not-allowed"
          }`}
        >
          {isMatured ? "Claim Funds" : "Redeem Early"}
          {isMatured && <ArrowUpRight size={12} />}
        </button>
      </div>
    </div>
  );
};

export default BondCard;
