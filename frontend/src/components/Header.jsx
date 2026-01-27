import React, { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useWallet } from "../context/WalletContext";
import WalletModal from "./WalletModal";
import { Menu, X } from "lucide-react";

export default function Header({ latest, isCapped, ratesLoaded }) {
  const { account, connectWallet, disconnect } = useWallet();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const location = useLocation();

  const handleWalletClick = () => {
    if (account) {
      setIsModalOpen(true);
    } else {
      connectWallet();
    }
  };

  // Close mobile menu on route change
  React.useEffect(() => {
    setIsMobileMenuOpen(false);
  }, [location]);

  return (
    <>
      <div className="sticky top-0 bg-[#050505]/95 backdrop-blur-md z-50 w-full border-b border-transparent">
        <header className="max-w-[1800px] mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex items-center gap-5 pl-1">
            {/* LOGO */}
            {/* LOGO */}
            <Link
              to="/"
              className="flex items-center gap-2 hover:opacity-80 transition-opacity"
            >
              <div className="w-3 h-3 bg-white"></div>
              <h1 className="text-sm font-bold tracking-widest uppercase">
                RLD
              </h1>
            </Link>

            {/* DESKTOP NAV */}
            <div className="hidden md:flex text-[12px] items-center gap-1 font-bold tracking-[0.15em] uppercase">
              <span className="text-white/10">//</span>

              <Link
                to="/app"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/app" ? "text-white cursor-default" : "text-gray-400 hover:text-white cursor-pointer"}`}
              >
                TERMINAL
              </Link>

              <span className="text-white/10">|</span>

              <Link
                to="/bonds"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/bonds" ? "text-white cursor-default" : "text-gray-400 hover:text-white cursor-pointer"}`}
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
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/markets" ? "text-white cursor-default" : "text-gray-400 hover:text-white cursor-pointer"}`}
              >
                MARKETS
              </Link>

              <span className="text-white/10">|</span>

              <Link
                to="/portfolio"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/portfolio" ? "text-white cursor-default" : "text-gray-400 hover:text-white cursor-pointer"}`}
              >
                PORTFOLIO
              </Link>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* STATUS INDICATOR (Desktop Only) */}
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

            {/* WALLET BUTTON */}
            <button
              onClick={handleWalletClick}
              className="flex items-center gap-3 border border-white/10 bg-black hover:bg-white/5 hover:border-white/30 transition-all px-4 md:px-6 py-2 focus:outline-none rounded-none"
            >
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  account
                    ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"
                    : "bg-gray-600"
                }`}
              ></div>
              <span className="text-[10px] md:text-xs font-bold tracking-widest uppercase text-white">
                {account ? `${account.substring(0, 6)}...` : "CONNECT"}
              </span>
            </button>

            {/* MOBILE MENU TOGGLE */}
            <button
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
              className="md:hidden p-2 text-gray-400 hover:text-white"
            >
              {isMobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </header>

        {/* MOBILE MENU DROPDOWN */}
        {isMobileMenuOpen && (
          <div className="md:hidden absolute top-full left-0 w-full bg-[#050505] border-b border-white/10 flex flex-col p-6 animate-in slide-in-from-top-5 duration-200 shadow-2xl">
            {/* MOBILE STATUS */}
            <div className="flex items-center gap-2 mb-6 text-[10px] uppercase tracking-widest text-gray-500 border-b border-white/5 pb-4">
              <div
                className={`w-1.5 h-1.5 ${ratesLoaded ? "bg-green-500" : "bg-red-500"}`}
              ></div>
              {isCapped ? "WARN: LIMIT_ACTIVE" : "NET: STABLE"}
            </div>

            <nav className="flex flex-col gap-4 text-sm font-bold tracking-[0.15em] uppercase">
              <Link
                to="/"
                className={`py-2 ${location.pathname === "/" ? "text-white" : "text-gray-500"}`}
              >
                TERMINAL
              </Link>
              <Link
                to="/bonds"
                className={`py-2 ${location.pathname === "/bonds" ? "text-white" : "text-gray-500"}`}
              >
                BONDS
              </Link>
              <Link
                to="/markets"
                className={`py-2 ${location.pathname === "/markets" ? "text-white" : "text-gray-500"}`}
              >
                MARKETS
              </Link>
              <Link
                to="/portfolio"
                className={`py-2 ${location.pathname === "/portfolio" ? "text-white" : "text-gray-500"}`}
              >
                PORTFOLIO
              </Link>
              <span className="py-2 text-gray-700 cursor-not-allowed">
                CDS_[SOON]
              </span>
            </nav>
          </div>
        )}
      </div>
      <WalletModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        account={account}
        disconnect={() => {
          disconnect();
          setIsModalOpen(false);
        }}
      />
    </>
  );
}
