import React, { Suspense, lazy, useState } from "react";

import { Link, useLocation } from "react-router-dom";
import { useWallet } from "../../context/WalletContext";
import { useFaucet } from "../../hooks/useFaucet";
import { useToast } from "../../hooks/useToast";
import { useSim } from "../../context/SimulationContext";
import { ToastContainer } from "../common/Toast";
import { Menu, X, Droplets, Loader2 } from "lucide-react";
import { prefetchRoute } from "../../app/prefetchRoutes";

const WalletModal = lazy(() => import("../modals/WalletModal"));

export default function Header({ isCapped, ratesLoaded, transparent = false }) {
  const { account, connectWallet, disconnect } = useWallet();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const location = useLocation();
  const { toasts, addToast, removeToast } = useToast();

  // Faucet integration
  const { marketInfo } = useSim();
  const waUsdcAddr = marketInfo?.collateral?.address;
  const { 
    requestFaucet, 
    loading: faucetLoading,
    step: faucetStep,
    usdcBalance,
    waUsdcBalance,
    ethBalance: faucetEthBalance
  } = useFaucet(
    account,
    waUsdcAddr,
    marketInfo?.external_contracts,
  );

  const handleFaucetClick = async () => {
    try {
      const result = await requestFaucet(account);
      if (result?.success) {
        const waAmt = parseFloat(result.waUsdcBalance || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
        const usdcAmt = parseFloat(result.usdcBalance || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
        // Open wallet modal so user sees updated balances
        setIsModalOpen(true);
        addToast({
          type: "faucet",
          title: "Wallet Funded ✓",
          message: `${waAmt} waUSDC + ${usdcAmt} USDC`,
          duration: 5000,
        });
      }
    } catch (err) {
      addToast({
        type: "error",
        title: "Faucet Failed",
        message: err.message || "Could not fund wallet",
      });
    }
  };

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

  // Nav prefetch: bonds data is now in ACCOUNT_QUERY (SimulationContext)
  // No REST prefetch needed — SWR dedups automatically.

  return (
    <>
      <div className={`sticky top-0 z-50 w-full ${transparent ? 'bg-transparent' : 'bg-[#050505]/95 backdrop-blur-md'}`}>
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
            <div className="hidden lg:flex text-sm items-center gap-1 font-bold tracking-[0.15em] uppercase">
              <span className="text-white/10">//</span>

              <Link
                to="/bonds"
                onMouseEnter={() => prefetchRoute("/bonds")}
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/bonds" ? "text-cyan-500 cursor-default" : "text-white hover:text-cyan-500 cursor-pointer"}`}
              >
                BONDS
              </Link>

              <span className="text-white/10">|</span>

              <Link
                to="/markets/cds"
                onMouseEnter={() => prefetchRoute("/markets/cds")}
                className={`transition-colors px-2 tracking-widest ${location.pathname.startsWith("/markets/cds") ? "text-cyan-500 cursor-default" : "text-white hover:text-cyan-500 cursor-pointer"}`}
              >
                CDS
              </Link>

              <span className="text-white/10">|</span>

              <div className="relative group">
                <Link
                  to="/markets"
                  onMouseEnter={() => prefetchRoute("/markets/perps")}
                  className={`transition-colors px-2 tracking-widest flex items-center gap-1 ${(location.pathname.startsWith("/markets/perps") || location.pathname.startsWith("/markets/pools") || location.pathname === "/markets") ? "text-cyan-500 cursor-default" : "text-white hover:text-cyan-500 cursor-pointer"}`}
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
                      onMouseEnter={() => prefetchRoute("/markets/perps")}
                      className="flex items-center gap-2.5 px-4 py-3 text-sm font-bold uppercase tracking-widest text-gray-400 hover:text-cyan-400 hover:bg-white/[0.03] transition-colors border-b border-white/5"
                    >
                      Perps
                    </Link>
                    <Link
                      to="/markets/pools"
                      onMouseEnter={() => prefetchRoute("/markets/pools")}
                      className="flex items-center gap-2.5 px-4 py-3 text-sm font-bold uppercase tracking-widest text-gray-400 hover:text-cyan-400 hover:bg-white/[0.03] transition-colors"
                    >
                      LP Pools
                    </Link>
                  </div>
                </div>
              </div>

              <span className="text-white/10">|</span>

              <Link
                to="/portfolio"
                onMouseEnter={() => prefetchRoute("/portfolio")}
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/portfolio" ? "text-cyan-500 cursor-default" : "text-white hover:text-cyan-500 cursor-pointer"}`}
              >
                PORTFOLIO
              </Link>

              <span className="text-white/10">|</span>

              <Link
                to="/data"
                onMouseEnter={() => prefetchRoute("/data")}
                className={`transition-colors px-2 tracking-widest ${location.pathname === "/data" ? "text-cyan-500 cursor-default" : "text-white hover:text-cyan-500 cursor-pointer"}`}
              >
                DATA
              </Link>

              <span className="text-white/10">|</span>

              <a
                href="https://docs.rld.fi"
                target="_blank"
                rel="noopener noreferrer"
                className="transition-colors px-2 tracking-widest text-white hover:text-cyan-500 cursor-pointer"
              >
                DOCS
              </a>
            </div>
          </div>

          <div className="flex items-center gap-4">
            {/* STATUS INDICATOR (Desktop Only) */}
            <div className="hidden lg:flex items-center gap-6 text-[11px] uppercase tracking-widest text-gray-500 border-r border-white/10 pr-6 h-6">
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
                onClick={handleFaucetClick}
                disabled={faucetLoading}
                className="hidden lg:flex items-center gap-2 border border-cyan-500/20 hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-all px-3 py-2 text-xs font-bold uppercase tracking-widest text-cyan-400 disabled:opacity-50 disabled:cursor-wait"
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
              className="flex items-center gap-3 border border-white/10 hover:bg-white/5 hover:border-white/30 transition-all px-4 lg:px-6 py-2 focus:outline-none rounded-none"
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
              className="lg:hidden p-2 text-gray-400 hover:text-white"
            >
              {isMobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </header>

        {/* MOBILE MENU DROPDOWN */}
        {isMobileMenuOpen && (
          <div className="lg:hidden absolute top-full left-0 w-full bg-[#050505] border-b border-white/10 flex flex-col p-6 animate-in slide-in-from-top-5 duration-200 shadow-2xl">
            {/* MOBILE STATUS */}
            <div className="flex items-center gap-2 mb-6 text-[10px] uppercase tracking-widest text-gray-500 border-b border-white/5 pb-4">
              <div
                className={`w-1.5 h-1.5 ${ratesLoaded ? "bg-green-500" : "bg-red-500"}`}
              ></div>
              {isCapped ? "WARN: LIMIT_ACTIVE" : "NET: STABLE"}
            </div>

            <nav className="flex flex-col gap-4 text-sm font-bold tracking-[0.15em] uppercase pb-6">
              <Link
                to="/bonds"
                className={`py-2 transition-colors ${location.pathname === "/bonds" ? "text-cyan-500" : "text-gray-500 hover:text-cyan-500"}`}
              >
                BONDS
              </Link>
              <Link
                to="/markets/cds"
                onMouseEnter={() => prefetchRoute("/markets/cds")}
                className={`py-2 transition-colors ${location.pathname.startsWith("/markets/cds") ? "text-cyan-500" : "text-gray-500 hover:text-cyan-500"}`}
              >
                CDS
              </Link>
              <div>
                <span
                  className={`py-2 block transition-colors ${(location.pathname.startsWith("/markets/perps") || location.pathname.startsWith("/markets/pools") || location.pathname === "/markets") ? "text-cyan-500" : "text-gray-500 hover:text-cyan-500"}`}
                >
                  MARKETS
                </span>
                <div className="pl-4 flex flex-col gap-2 mt-1 border-l border-white/5 ml-1">
                  <Link
                    to="/markets/perps"
                    onMouseEnter={() => prefetchRoute("/markets/perps")}
                    className={`py-1 text-[11px] flex items-center gap-2 ${location.pathname.startsWith("/markets/perps") ? "text-cyan-400" : "text-gray-500"}`}
                  >
                    <div className="w-1 h-1 bg-cyan-500/50" />
                    Perps
                  </Link>
                  <Link
                    to="/markets/pools"
                    onMouseEnter={() => prefetchRoute("/markets/pools")}
                    className={`py-1 text-[11px] flex items-center gap-2 ${location.pathname.startsWith("/markets/pools") ? "text-cyan-400" : "text-gray-500"}`}
                  >
                    <div className="w-1 h-1 bg-cyan-500/50" />
                    LP Pools
                  </Link>
                </div>
              </div>
              <Link
                to="/portfolio"
                onMouseEnter={() => prefetchRoute("/portfolio")}
                className={`py-2 transition-colors ${location.pathname === "/portfolio" ? "text-cyan-500" : "text-gray-500 hover:text-cyan-500"}`}
              >
                PORTFOLIO
              </Link>
              <Link
                to="/data"
                onMouseEnter={() => prefetchRoute("/data")}
                className={`py-2 transition-colors ${location.pathname === "/data" ? "text-cyan-500" : "text-gray-500 hover:text-cyan-500"}`}
              >
                DATA
              </Link>
              <a
                href="https://docs.rld.fi"
                target="_blank"
                rel="noopener noreferrer"
                className="py-2 text-gray-500 hover:text-cyan-500 transition-colors"
              >
                DOCS
              </a>
            </nav>

            {/* MOBILE FAUCET */}
            {account && (
              <div className="pt-4 border-t border-white/5">
                <button
                  onClick={handleFaucetClick}
                  disabled={faucetLoading}
                  className="w-full flex items-center justify-center gap-2 border border-cyan-500/20 hover:border-cyan-500/50 hover:bg-cyan-500/5 transition-all px-3 py-3 text-sm font-bold uppercase tracking-widest text-cyan-400 disabled:opacity-50 disabled:cursor-wait"
                >
                  {faucetLoading ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Droplets size={14} />
                  )}
                  {faucetLoading ? "Funding Wallet..." : "Request Faucet Funds"}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      {isModalOpen && (
        <Suspense fallback={null}>
          <WalletModal
            isOpen={isModalOpen}
            onClose={() => setIsModalOpen(false)}
            account={account}
            usdcBalance={usdcBalance}
            waUsdcBalance={waUsdcBalance}
            onFaucet={handleFaucetClick}
            faucetLoading={faucetLoading}
            faucetStep={faucetStep}
            ethBalance={faucetEthBalance}
            disconnect={() => {
              disconnect();
              setIsModalOpen(false);
            }}
          />
        </Suspense>
      )}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </>
  );
}
