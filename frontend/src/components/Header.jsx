import React, { useState } from 'react';
import { Link, useLocation } from "react-router-dom";
import { useWallet } from "../context/WalletContext";
import WalletModal from "./WalletModal";

export default function Header({ latest, isCapped, ratesLoaded }) {
    const { account, connectWallet, disconnect } = useWallet();
    const [isModalOpen, setIsModalOpen] = useState(false);
    const location = useLocation();

    const handleWalletClick = () => {
        if (account) {
            setIsModalOpen(true);
        } else {
            connectWallet();
        }
    };

    return (
        <>
            <div className="sticky top-0 bg-[#050505]/95 backdrop-blur-sm z-50 w-full border-b border-transparent">
                <header className="max-w-[1800px] mx-auto px-6 py-3 flex items-center justify-between">
                    <div className="flex items-center gap-5 pl-1">
                        <div className="flex items-center gap-2">
                            <div className="w-3 h-3 bg-white"></div>
                            <h1 className="text-sm font-bold tracking-widest uppercase">
                                RLD
                            </h1>
                        </div>
                        <div className="hidden md:flex text-[12px] items-center gap-1 font-bold tracking-[0.15em] uppercase">
                            <span className="text-white/10">//</span>

                            <Link
                                to="/"
                                className={`transition-colors px-2 tracking-widest ${location.pathname === '/' ? 'text-white cursor-default' : 'text-gray-400 hover:text-white cursor-pointer'}`}
                            >
                                TERMINAL
                            </Link>

                            <span className="text-white/10">|</span>

                            <Link
                                to="/bonds"
                                className={`transition-colors px-2 tracking-widest ${location.pathname === '/bonds' ? 'text-white cursor-default' : 'text-gray-400 hover:text-white cursor-pointer'}`}
                            >
                                BONDS
                            </Link>

                            <span className="text-white/10">|</span>

                            <a className="text-gray-400 hover:text-white transition-colors cursor-pointer px-2 tracking-widest ">
                                CDS_[SOON]
                            </a>
                            <span className="text-white/10">|</span>
                            <Link
                                to="/markets"
                                className={`transition-colors px-2 tracking-widest ${location.pathname === '/markets' ? 'text-white cursor-default' : 'text-gray-400 hover:text-white cursor-pointer'}`}
                            >
                                MARKETS
                            </Link>
                            <span className="text-white/10">|</span>
                            {/*                            <Link
                                to="/research"
                                className={`transition-colors px-2 tracking-widest ${location.pathname === '/research' ? 'text-white cursor-default' : 'text-gray-400 hover:text-white cursor-pointer'}`}
                            >
                                RESEARCH
                            </Link> */}
                        </div>
                    </div>

                    <div className="flex items-center gap-6">
                        <div className="hidden md:flex items-center gap-6 text-[11px] uppercase tracking-widest text-gray-500 border-r border-white/10 pr-6 h-6">
                            <span className="flex items-center gap-2">
                                <div
                                    className={`w-1.5 h-1.5 ${
                                        ratesLoaded ? "bg-green-500" : "bg-red-500"
                                    }`}
                                ></div>
                                {isCapped ? "WARN: LIMIT_ACTIVE" : "NET: STABLE"}
                            </span>

                        </div>
                        <button
                            onClick={handleWalletClick}
                            className="flex items-center gap-3 border border-white/10 bg-black hover:bg-white/5 hover:border-white/30 transition-all px-6 py-2 focus:outline-none rounded-none"
                        >
                            <div
                                className={`w-1.5 h-1.5 rounded-full ${
                                    account
                                        ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"
                                        : "bg-gray-600"
                                }`}
                            ></div>
                            <span className="text-xs font-bold tracking-widest uppercase text-white">
                                {account ? `${account.substring(0, 6)}...` : "CONNECT WALLET"}
                            </span>
                        </button>
                    </div>
                </header>
            </div>
            <WalletModal 
                isOpen={isModalOpen} 
                onClose={() => setIsModalOpen(false)} 
                account={account} 
                disconnect={() => { disconnect(); setIsModalOpen(false); }} 
            />
        </>
    );
}
