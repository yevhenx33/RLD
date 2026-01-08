import React from "react";
import { Shield, Percent } from "lucide-react";
import { useWallet } from "../context/WalletContext";
import Header from "./Header";

// Hooks
import { useMarketData } from "../hooks/useMarketData";
import { useTradeLogic } from "../hooks/useTradeLogic";
import { useWealthProjection } from "../hooks/useWealthProjection";

// Components
import MetricsGrid from "./MetricsGrid";
import TradingTerminal from "./TradingTerminal";
import WealthProjectionChart from "./WealthProjectionChart";
import ProductCard from "./ProductCard";

export default function BondsPage() {
  const { account, connectWallet, usdcBalance } = useWallet();
  const { rates, error, isLoading, stats, dailyChange, latest, isCappedRaw } =
    useMarketData();
  const tradeLogic = useTradeLogic(latest.apy);

  const projectionData = useWealthProjection(
    tradeLogic.state.notional,
    latest.apy,
    tradeLogic.state.maturityDays
  );

  if (error)
    return (
      <div className="h-screen flex items-center justify-center text-red-600 bg-black font-mono text-xs">
        ERR: API_DISCONNECTED
      </div>
    );
  if (isLoading || !rates)
    return (
      <div className="h-screen flex items-center justify-center text-gray-500 bg-black font-mono text-xs animate-pulse">
        SYSTEM_INITIALIZING...
      </div>
    );

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col">
      <Header
        latest={latest}
        isCapped={isCappedRaw}
        account={account}
        connectWallet={connectWallet}
        ratesLoaded={!!rates}
      />
      <div className="max-w-[1800px] mx-auto w-full px-6 flex-1 flex flex-col gap-6 pt-0 pb-12">
        <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
          <div className="xl:col-span-9 flex flex-col gap-6">
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
              <div className="lg:col-span-4 h-full ">
                <ProductCard
                  theme="cyan"
                  title="FIXED_YIELD"
                  badge="Synthetic Bond"
                  Icon={Shield}
                  desc="Transform volatile rates into a fixed-income product. Short RLP + TWAMM."
                  onClick={() =>
                    tradeLogic.actions.setActiveProduct("FIXED_YIELD")
                  }
                  isActive={tradeLogic.state.activeProduct === "FIXED_YIELD"}
                />
              </div>
              <div className="lg:col-span-8 h-full">
                <MetricsGrid
                  latest={latest}
                  dailyChange={dailyChange}
                  stats={stats}
                />
              </div>
              <div className="lg:col-span-4 h-[200px]">
                <ProductCard
                  theme="pink"
                  title="FIXED_BORROW"
                  badge="Fixed-Term Debt"
                  Icon={Percent}
                  desc="Lock in your borrowing costs. Long RLP + TWAMM."
                  onClick={() =>
                    tradeLogic.actions.setActiveProduct("FIXED_BORROW")
                  }
                  isActive={tradeLogic.state.activeProduct === "FIXED_BORROW"}
                />
              </div>
              <div className="lg:col-span-8 h-[500px]">
                <WealthProjectionChart
                  data={projectionData}
                  collateral={tradeLogic.state.notional}
                  theme={
                    tradeLogic.state.activeProduct === "FIXED_BORROW"
                      ? "pink"
                      : "cyan"
                  }
                />
              </div>
            </div>
          </div>
          <TradingTerminal
            account={account}
            connectWallet={connectWallet}
            usdcBalance={usdcBalance}
            currentRate={latest.apy}
            state={tradeLogic.state}
            actions={tradeLogic.actions}
          />
        </div>
      </div>
    </div>
  );
}
