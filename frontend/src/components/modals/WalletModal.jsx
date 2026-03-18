import React, { useState } from 'react';
import { X, Copy, LogOut, Power, Check, Droplets, Loader2, ArrowRightLeft } from "lucide-react";
import { useWallet } from '../../context/WalletContext';
import { formatNum } from '../../utils/helpers';

export default function WalletModal({ 
  isOpen, 
  onClose, 
  onFaucet, 
  faucetLoading,
  faucetStep,
  usdcBalance,
  waUsdcBalance,
  ethBalance
}) {
  const { account, disconnect, chainId, switchNetwork, balance } = useWallet();
  const [copied, setCopied] = useState(false);

  if (!isOpen) return null;

  const copyAddress = () => {
    if (account) {
      navigator.clipboard.writeText(account);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div className="relative w-full max-w-sm bg-[#0a0a0a] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">

        {/* ─── Header ─────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5">
          <div className="flex items-center gap-2.5">
            <div className={`w-1.5 h-1.5 ${account ? 'bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.4)]' : 'bg-red-500'}`} />
            <span className="text-xs font-bold tracking-[0.2em] text-white uppercase">
              {account ? 'Connected' : 'Disconnected'}
            </span>
          </div>
          <button onClick={onClose} className="text-gray-600 hover:text-white transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* ─── Body ───────────────────────────────────────────────── */}
        <div className="p-5 flex flex-col gap-5">

          {/* Address */}
          <div>
            <div className="text-[10px] uppercase tracking-widest text-gray-600 mb-2">Address</div>
            <div className="flex items-center justify-between gap-2 px-3.5 py-2.5 bg-white/[0.03] border border-white/10">
              <span className="font-mono text-sm text-white/90 truncate">
                {account ? `0x${account.slice(2, 7)}…${account.slice(-5)}` : "—"}
              </span>
              <button
                onClick={copyAddress}
                className="text-gray-600 hover:text-white transition-colors shrink-0"
                title="Copy"
              >
                {copied ? <Check size={13} className="text-green-400" /> : <Copy size={13} />}
              </button>
            </div>
          </div>

          {/* Balances — compact row */}
          <div className="grid grid-cols-3 gap-2">
            <div className="px-2.5 py-3 border border-white/10 bg-white/[0.02]">
              <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-1">USDC</div>
              <div className="text-sm text-white font-mono font-light tracking-tight truncate">
                {formatNum(parseFloat(usdcBalance))}
              </div>
            </div>
            <div className="px-2.5 py-3 border border-white/10 bg-white/[0.02]">
              <div className="text-[9px] text-cyan-700 uppercase tracking-widest mb-1">waUSDC</div>
              <div className="text-sm text-cyan-400 font-mono font-light tracking-tight truncate">
                {formatNum(parseFloat(waUsdcBalance))}
              </div>
            </div>
            <div className="px-2.5 py-3 border border-white/10 bg-white/[0.02]">
              <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-1">ETH</div>
              <div className="text-sm text-white font-mono font-light tracking-tight truncate">
                {formatNum(parseFloat(ethBalance || balance), 4)}
              </div>
            </div>
          </div>

          {/* Network — minimal */}
          <div className="flex items-center justify-between px-3.5 py-2.5 border border-white/10 bg-white/[0.02]">
            <div className="flex items-center gap-2">
              <Power size={12} className="text-gray-600" />
              <span className="text-[10px] text-gray-600 uppercase tracking-widest">Chain</span>
            </div>
            <span className="text-xs text-white font-mono">{chainId || "—"}</span>
          </div>

          {/* ─── Actions ──────────────────────────────────────────── */}
          <div className="flex flex-col gap-2 pt-1">

            {/* Faucet */}
            {onFaucet && (
              <button
                onClick={() => onFaucet(account)}
                disabled={faucetLoading}
                className="flex items-center justify-center gap-2 w-full py-3 border border-pink-500/30 text-pink-400 hover:bg-pink-500/10 hover:border-pink-500/50 disabled:opacity-40 disabled:cursor-not-allowed text-xs font-bold uppercase tracking-[0.15em] transition-colors"
              >
                {faucetLoading
                  ? <Loader2 size={13} className="animate-spin" />
                  : <Droplets size={13} />
                }
                {faucetLoading ? (faucetStep || "Requesting…") : "Request Faucet"}
              </button>
            )}

            {/* Switch network */}
            <button
              onClick={switchNetwork}
              className="flex items-center justify-center gap-2 w-full py-3 border border-white/10 text-gray-500 hover:text-white hover:bg-white/[0.04] hover:border-white/20 text-xs font-bold uppercase tracking-[0.15em] transition-colors"
            >
              <ArrowRightLeft size={13} />
              {chainId === "31337" ? "Switch Network" : "Switch Network"}
            </button>

            {/* Disconnect */}
            <button
              onClick={() => { disconnect(); onClose(); }}
              className="flex items-center justify-center gap-2 w-full py-3 border border-red-500/20 text-red-500/70 hover:text-red-400 hover:bg-red-500/10 hover:border-red-500/40 text-xs font-bold uppercase tracking-[0.15em] transition-colors"
            >
              <LogOut size={13} />
              Disconnect
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
