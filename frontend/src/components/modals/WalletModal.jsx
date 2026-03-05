import React from 'react';
import { X, Copy, LogOut, Power, CheckCircle2, AlertCircle, ArrowRightLeft } from "lucide-react";
import { useWallet } from '../../context/WalletContext';
import { formatNum } from '../../utils/helpers';

export default function WalletModal({ isOpen, onClose }) {
  const { account, disconnect, chainId, debugInfo, switchNetwork, usdcBalance, balance } = useWallet();

  if (!isOpen) return null;

  const copyAddress = () => {
    if (account) {
      navigator.clipboard.writeText(account);
      // Optional: Add toast notification here
    }
  };

  const isLocalhost = chainId === "31337";

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      {/* Modal Container */}
      <div className="relative w-full max-w-md bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
            <div className="flex items-center gap-3">
                <div className={`w-2 h-2 ${account ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]' : 'bg-red-500'}`} />
                <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
                    {account ? 'System_Connected' : 'System_Disconnect'}
                </h2>
            </div>
            <button 
                onClick={onClose}
                className="text-gray-500 hover:text-white transition-colors rounded-none"
            >
                <X size={18} />
            </button>
        </div>

        {/* Content */}
        <div className="p-6 flex flex-col gap-6">
            
            {/* Account Info Block */}
            <div className="space-y-4">
                <div className="flex justify-between items-end">
                    <span className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                        Active_Account
                    </span>
                </div>
                
                <div className="p-4 bg-white/[0.02] border border-white/10 font-mono text-sm text-white/90 flex items-center justify-between group">
                    <span className="break-all mr-2">
                        <span className="opacity-50">0x</span>
                        {account ? account.substring(2) : "Not Connected"}
                    </span>
                    {account && (
                        <button 
                            onClick={copyAddress}
                            className="text-gray-500 hover:text-white transition-colors p-1 shrink-0 rounded-none"
                            title="Copy Address"
                        >
                            <Copy size={16} />
                        </button>
                    )}
                </div>
            </div>

            {/* Balances Grid */}
            <div className="grid grid-cols-2 gap-4">
                <div className="p-4 border border-white/10 bg-white/[0.02] flex flex-col gap-1">
                    <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                        USDC_Balance
                    </span>
                    <span className="text-xl text-white font-mono font-light tracking-tighter">
                        {formatNum(parseFloat(usdcBalance))}
                    </span>
                </div>
                <div className="p-4 border border-white/10 bg-white/[0.02] flex flex-col gap-1">
                    <span className="text-[10px] text-gray-500 uppercase tracking-widest">
                        ETH_Gas
                    </span>
                    <span className="text-xl text-white font-mono font-light tracking-tighter">
                         {formatNum(parseFloat(balance), 4)}
                    </span>
                </div>
            </div>

            {/* Network Status */}
            <div className="p-4 border border-white/10 bg-white/[0.02] flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-500/10 border border-blue-500/20 text-blue-400">
                        <Power size={14} />
                    </div>
                    <div>
                        <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-0.5">Network_ID</div>
                        <div className="text-xs text-white font-mono uppercase">
                            {chainId || "Unknown"} <span className="text-gray-600">|</span> {isLocalhost ? "LOCALHOST" : "MAINNET/OTHER"}
                        </div>
                    </div>
                </div>
                <div className="h-2 w-2 bg-blue-500 animate-pulse" />
            </div>

            {/* Actions */}
            {/* Actions */}
            <div className="flex flex-col gap-3 mt-2">
                <button 
                    onClick={switchNetwork}
                    className="rounded-none flex items-center justify-center gap-3 w-full py-4 border border-blue-500/30 text-blue-400 hover:bg-blue-500/10 hover:border-blue-500/50 text-xs font-bold uppercase tracking-[0.2em]"
                >
                    <ArrowRightLeft size={14} />
                    {chainId === "31337" ? "Switch to Mainnet" : "Switch to Localhost"}
                </button>
                 <button 
                    onClick={disconnect}
                    className="rounded-none flex items-center justify-center gap-3 w-full py-4 border border-red-500/30 text-red-500 hover:bg-red-500/10 hover:border-red-500/50 text-xs font-bold uppercase tracking-[0.2em]"
                >
                    <LogOut size={14} />
                    Disconnect_Session
                </button>
            </div>

            {/* Footer Debug */}
            <div className="flex items-center gap-2 text-[10px] text-gray-600 font-mono pt-2 border-t border-white/5">
                <AlertCircle size={10} />
                <span>STATUS: {debugInfo || "OPERATIONAL"}</span>
            </div>
        </div>
      </div>
    </div>
  );
}
