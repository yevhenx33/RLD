import React, { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useWallet } from "../context/WalletContext";
import { useFaucet } from "../hooks/useFaucet";
import { useSimulation } from "../hooks/useSimulation";
import WalletModal from "./WalletModal";
import { Menu, X, Droplets, Loader2 } from "lucide-react";

export default function Header({ isCapped, ratesLoaded }) {
  const { account, connectWallet, disconnect } = useWallet();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const location = useLocation();

  // Faucet integration
  const { marketInfo } = useSimulation();
  const waUsdcAddr = marketInfo?.collateral?.address;
  const { requestFaucet, loading: faucetLoading } = useFaucet(account, waUsdcAddr);

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
            <div className="hidden md:flex text-sm items-center gap-1 font-bold tracking-[0.15em] uppercase">
              <span className="text-white/10">//</span>

              <div className="relative group">
                <Link
                  to="/markets"
                  className={`transition-colors px-2 tracking-widest flex items-center gap-1 ${location.pathname.startsWith("/markets") ? "text-cyan-400 cursor-default" : "text-white hover:text-cyan-400 cursor-pointer"}`}
                >
                  Markets
                  <svg
                    width="8"
                    height="5"
                    viewBox="0 0 8 5"
                    fill="none"
                    className="opacity-50 mt-px"
                  >
                    <path
                      d="M1 1L4 4L7 1"
                      stroke="currentColor"
                      strokeWidth="1.5"
                      strokeLinecap="square"
                    />
                  </svg>
                </Link>
                {/* Hover dropdown */}
                <div className="absolute top-full left-0 pt-2 opacity-0 invisible group-hover:opacity-100 group-hover:visible transition-all duration-150 z-50">
                  <div className="border border-white/10 bg-[#0a0a0a] min-w-[160px] shadow-2xl">
                    <Link
                      to="/markets/perps"
                      className="flex items-center gap-2.5 px-4 py-3 text-sm font-bold uppercase tracking-widest text-gray-400 hover:text-cyan-400 hover:bg-white/[0.03] transition-colors border-b border-white/5"
                    >
                      Long/Short
                    </Link>
                    <Link
                      to="/markets/pools"
                      className="flex items-center gap-2.5 px-4 py-3 text-sm font-bold uppercase tracking-widest text-gray-400 hover:text-cyan-400 hover:bg-white/[0.03] transition-colors"
                    >
                      Pools
                    </Link>
                  </div>
                </div>
              </div>

              <span className="text-white/10">|</span>

              <Link
                to="/bonds"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/bonds" ? "text-white cursor-default" : "text-white hover:text-cyan-400 cursor-pointer"}`}
              >
                BONDS
              </Link>



              <span className="text-white/10">|</span>

              <Link
                to="/portfolio"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/portfolio" ? "text-white cursor-default" : "text-white hover:text-cyan-400 cursor-pointer"}`}
              >
                PORTFOLIO
              </Link>

              <span className="text-white/10">|</span>

              <Link
                to="/explore"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/explore" ? "text-white cursor-default" : "text-white hover:text-cyan-400 cursor-pointer"}`}
              >
                EXPLORE
              </Link>

              <span className="text-white/10">|</span>

              <Link
                to="/app"
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/app" ? "text-white cursor-default" : "text-white hover:text-cyan-400 cursor-pointer"}`}
              >
                OLD
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

            {/* REQUEST FUNDS (only when connected) */}
            {account && (
              <button
                onClick={() => requestFaucet(account)}
                disabled={faucetLoading}
                className="hidden md:flex items-center gap-2 border border-cyan-500/20 hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-all px-3 py-2 text-xs font-bold uppercase tracking-widest text-cyan-400 disabled:opacity-50 disabled:cursor-wait"
              >
                {faucetLoading ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Droplets size={12} />
                )}
                {faucetLoading ? "Funding..." : "Faucet"}
              </button>
            )}

            {/* WALLET BUTTON */}
            <button
              onClick={handleWalletClick}
              className="flex items-center gap-3 border border-white/10 hover:bg-white/5 hover:border-white/30 transition-all px-4 md:px-6 py-2 focus:outline-none rounded-none"
            >
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  account
                    ? "bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.5)]"
                    : "bg-gray-600"
                }`}
              ></div>
              <span className="text-sm font-bold tracking-widest uppercase text-white">
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
                to="/explore"
                className={`py-2 ${location.pathname === "/explore" ? "text-white" : "text-gray-500"}`}
              >
                EXPLORE
              </Link>

              <Link
                to="/portfolio"
                className={`py-2 ${location.pathname === "/portfolio" ? "text-white" : "text-gray-500"}`}
              >
                PORTFOLIO
              </Link>
              <div>
                <span
                  className={`py-2 block ${location.pathname.startsWith("/markets") ? "text-cyan-400" : "text-cyan-700"}`}
                >
                  SIM
                </span>
                <div className="pl-4 flex flex-col gap-2 mt-1">
                  <Link
                    to="/markets/perps"
                    className={`py-1 text-[11px] flex items-center gap-2 ${location.pathname.startsWith("/markets/perps") ? "text-cyan-400" : "text-cyan-700"}`}
                  >
                    <div className="w-1 h-1 bg-cyan-500/50" />
                    Long/Short
                  </Link>
                  <Link
                    to="/markets/pools"
                    className={`py-1 text-[11px] flex items-center gap-2 ${location.pathname.startsWith("/markets/pools") ? "text-cyan-400" : "text-cyan-700"}`}
                  >
                    <div className="w-1 h-1 bg-cyan-500/50" />
                    Pools
                  </Link>
                </div>
              </div>
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
