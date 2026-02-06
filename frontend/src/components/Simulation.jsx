import React, { useState, useEffect } from "react";
import { Link } from "react-router-dom";
import {
  Terminal,
  Play,
  Server,
  Activity,
  X,
  Database,
  Shield,
  Zap,
  Clock,
  Coins,
  Lock,
  ChevronDown,
  Layers,
  ArrowRight,
  Link as LinkIcon,
} from "lucide-react";

/**
 * Custom Styled Dropdown
 */
const Dropdown = ({ value, options, onChange, disabled }) => {
  const [isOpen, setIsOpen] = useState(false);

  const selectedLabel = options.find((o) => o.value === value)?.label || value;

  return (
    <div
      className={`relative w-full ${disabled ? "opacity-50 pointer-events-none" : ""}`}
    >
      <button
        onClick={() => !disabled && setIsOpen(!isOpen)}
        className="w-full h-10 bg-black border border-white/10 text-white text-xs font-mono px-3 flex items-center justify-between uppercase focus:border-cyan-500 focus:outline-none hover:border-white/30 transition-all group"
      >
        <span className="truncate group-hover:text-white transition-colors">
          {selectedLabel}
        </span>
        <ChevronDown
          size={14}
          className={`text-gray-500 group-hover:text-white transition-all duration-200 ${isOpen ? "rotate-180 text-cyan-500" : ""}`}
        />
      </button>

      {isOpen && (
        <>
          <div
            className="fixed inset-0 z-30"
            onClick={() => setIsOpen(false)}
          />
          <div className="absolute top-full left-0 w-full mt-1 bg-[#0a0a0a] border border-white/10 z-40 max-h-[200px] overflow-y-auto shadow-2xl animate-in slide-in-from-top-1 duration-100 flex flex-col">
            {options.map((opt) => (
              <button
                key={opt.value}
                disabled={opt.disabled}
                onClick={() => {
                  if (!opt.disabled) {
                    onChange(opt.value);
                    setIsOpen(false);
                  }
                }}
                className={`w-full text-left px-3 py-2 text-xs font-mono uppercase tracking-wide transition-colors border-b border-white/5 last:border-b-0 flex justify-between items-center
                            ${opt.disabled ? "text-gray-700 cursor-not-allowed" : "hover:bg-white/5 text-gray-400 group"}
                            ${value === opt.value ? "text-cyan-400 font-bold bg-white/[0.02]" : ""}
                        `}
              >
                <span>{opt.label}</span>
                {opt.tag && (
                  <span className="text-[9px] bg-white/10 px-1 rounded text-gray-500">
                    {opt.tag}
                  </span>
                )}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
};

/**
 * Deployment Configuration Modal
 */
const DeploymentModal = ({ isOpen, onClose, onDeploy, isLoading }) => {
  const [formData, setFormData] = useState({
    lendingProtocol: "AAVE",
    targetMarket: "aUSDC",
    collateralToken: "USDC",
    minColRatio: "150",
    maintenanceMargin: "110",
    liquidationCloseFactor: "50",
    fundingPeriod: "86400",
    debtCap: "1000000",
    initialPrice: "4.50",
  });

  // Auto-update collateral based on target market
  useEffect(() => {
    // Collateral matches the Target Market (Yield Token)
    setFormData((prev) => ({ ...prev, collateralToken: prev.targetMarket }));
  }, [formData.targetMarket]);

  const protocolOptions = [
    { value: "AAVE", label: "Aave V3" },
    { value: "MORPHO", label: "Morpho Blue", disabled: true, tag: "SOON" },
    { value: "EULER", label: "Euler V2", disabled: true, tag: "SOON" },
  ];

  const targetMarketOptions = [
    { value: "aUSDC", label: "aUSDC (USD Coin)" },
    { value: "aUSDT", label: "aUSDT (Tether)" },
    { value: "aDAI", label: "aDAI (Dai Stable)" },
  ];

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/80 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />

      {/* Modal Container */}
      <div className="relative w-full max-w-2xl bg-[#080808] border border-white/10 shadow-2xl flex flex-col animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-white/10 bg-white/[0.02]">
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 bg-cyan-500 shadow-[0_0_8px_rgba(6,182,212,0.5)]" />
            <h2 className="text-sm font-bold tracking-[0.2em] text-white uppercase">
              Initialize_Market
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-gray-500 hover:text-white transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Form Content */}
        <div className="p-6 space-y-8 max-h-[70vh] overflow-y-auto custom-scrollbar">
          {/* SECTION 1: ASSET CONFIG (2x2 Grid) */}
          <div className="space-y-4">
            <div className="text-[10px] text-cyan-500 font-bold uppercase tracking-widest border-b border-white/10 pb-1 w-full flex items-center gap-2">
              <Layers size={10} /> Asset_Configuration
            </div>

            <div className="grid grid-cols-2 gap-6">
              {/* Row 1, Col 1: Lending Protocol */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Lending_Protocol
                </label>
                <Dropdown
                  value={formData.lendingProtocol}
                  options={protocolOptions}
                  onChange={(val) =>
                    setFormData({ ...formData, lendingProtocol: val })
                  }
                  disabled={isLoading}
                />
              </div>

              {/* Row 1, Col 2: Target Market */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Target_Market
                </label>
                <Dropdown
                  value={formData.targetMarket}
                  options={targetMarketOptions}
                  onChange={(val) =>
                    setFormData({ ...formData, targetMarket: val })
                  }
                  disabled={isLoading}
                />
              </div>

              {/* Row 2, Col 1: Initial Index Price */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold flex items-center gap-2">
                  <Coins size={12} /> Initial_Index_Price
                </label>
                <div className="h-10 border border-white/10 bg-black px-3 flex items-center justify-between text-white font-mono text-xs focus-within:border-cyan-500 transition-colors">
                  <input
                    type="text"
                    value={formData.initialPrice}
                    onChange={(e) =>
                      setFormData({ ...formData, initialPrice: e.target.value })
                    }
                    disabled={isLoading}
                    className="w-full bg-transparent text-white text-xs font-mono focus:outline-none"
                  />
                  <span className="text-gray-600 text-[10px]">APY%</span>
                </div>
              </div>

              {/* Row 2, Col 2: Required Collateral (Read Only) */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold flex items-center gap-2">
                  <LinkIcon size={12} /> Required_Collateral
                </label>
                <div className="h-10 border border-white/10 bg-white/[0.05] flex items-center justify-between px-3 group cursor-not-allowed">
                  <div className="flex items-center gap-2">
                    <div className="h-4 w-4 rounded-full bg-cyan-500/20 text-cyan-500 flex items-center justify-center text-[10px] font-bold">
                      C
                    </div>
                    <span className="text-white font-mono text-xs uppercase tracking-widest">
                      {formData.collateralToken}
                    </span>
                  </div>
                  <Lock size={12} className="text-gray-600" />
                </div>
              </div>
            </div>
          </div>

          {/* SECTION 2: RISK PARAMETERS */}
          <div className="space-y-4">
            <div className="text-[10px] text-pink-500 font-bold uppercase tracking-widest border-b border-white/10 pb-1 w-full flex items-center gap-2">
              <Shield size={10} /> Risk_Parameters
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-6">
              {/* Min Col Ratio */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Min_Col_Ratio
                </label>
                <div className="flex items-center h-10 border border-white/10 bg-black px-3 focus-within:border-pink-500 transition-colors">
                  <input
                    type="text"
                    value={formData.minColRatio}
                    onChange={(e) =>
                      setFormData({ ...formData, minColRatio: e.target.value })
                    }
                    disabled={isLoading}
                    className="w-full bg-transparent text-white text-xs font-mono focus:outline-none"
                  />
                  <span className="text-gray-600 text-[10px]">%</span>
                </div>
              </div>
              {/* Maintenance Margin */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Maint_Margin
                </label>
                <div className="flex items-center h-10 border border-white/10 bg-black px-3 focus-within:border-pink-500 transition-colors">
                  <input
                    type="text"
                    value={formData.maintenanceMargin}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        maintenanceMargin: e.target.value,
                      })
                    }
                    disabled={isLoading}
                    className="w-full bg-transparent text-white text-xs font-mono focus:outline-none"
                  />
                  <span className="text-gray-600 text-[10px]">%</span>
                </div>
              </div>
              {/* Liquidation Close Factor */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Liq_Close_Fact
                </label>
                <div className="flex items-center h-10 border border-white/10 bg-black px-3 focus-within:border-pink-500 transition-colors">
                  <input
                    type="text"
                    value={formData.liquidationCloseFactor}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        liquidationCloseFactor: e.target.value,
                      })
                    }
                    disabled={isLoading}
                    className="w-full bg-transparent text-white text-xs font-mono focus:outline-none"
                  />
                  <span className="text-gray-600 text-[10px]">%</span>
                </div>
              </div>
              {/* Debt Cap */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Debt_Cap
                </label>
                <div className="flex items-center h-10 border border-white/10 bg-black px-3 focus-within:border-pink-500 transition-colors">
                  <input
                    type="text"
                    value={formData.debtCap}
                    onChange={(e) =>
                      setFormData({ ...formData, debtCap: e.target.value })
                    }
                    disabled={isLoading}
                    className="w-full bg-transparent text-white text-xs font-mono focus:outline-none"
                  />
                  <span className="text-gray-600 text-[10px]">RLP</span>
                </div>
              </div>
              {/* Funding Period */}
              <div className="space-y-2">
                <label className="text-[10px] uppercase tracking-widest text-gray-500 font-bold">
                  Fund_Period
                </label>
                <div className="flex items-center h-10 border border-white/10 bg-black px-3 focus-within:border-pink-500 transition-colors">
                  <input
                    type="text"
                    value={formData.fundingPeriod}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        fundingPeriod: e.target.value,
                      })
                    }
                    disabled={isLoading}
                    className="w-full bg-transparent text-white text-xs font-mono focus:outline-none"
                  />
                  <span className="text-gray-600 text-[10px]">SEC</span>
                </div>
              </div>
            </div>
          </div>

          {/* Action Button */}
          <button
            onClick={() => onDeploy(formData)}
            disabled={isLoading}
            className="w-full h-12 mt-4 bg-white text-black font-bold text-xs tracking-[0.2em] uppercase hover:bg-cyan-400 transition-colors flex items-center justify-center gap-3 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isLoading ? (
              <>
                <Activity size={14} className="animate-spin" />
                Deploying...
              </>
            ) : (
              <>
                <Terminal size={14} />
                Deploy_Contract
              </>
            )}
          </button>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 bg-[#050505] border-t border-white/10 text-[10px] text-gray-600 font-mono flex justify-between items-center">
          <span>GAS_EST: 0.04 ETH</span>
          <span className="text-cyan-500/50">NETWORK: SEPOLIA</span>
        </div>
      </div>
    </div>
  );
};

/**
 * Simulation Page
 * Allows users to deploy testnet markets and run economic simulations.
 */
const Simulation = () => {
  const [isDeployOpen, setIsDeployOpen] = useState(false);
  const [simulations, setSimulations] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch simulations on load
  useEffect(() => {
    fetchSimulations();
    const interval = setInterval(fetchSimulations, 5000); // Poll every 5s
    return () => clearInterval(interval);
  }, []);

  const fetchSimulations = async () => {
    try {
      // Use the enriched endpoint for live market state data
      const res = await fetch("http://localhost:8080/simulations/enriched");
      const data = await res.json();
      if (Array.isArray(data)) setSimulations(data);
    } catch (err) {
      console.error("Failed to fetch simulations:", err);
    }
  };

  const handleDeploy = async (formData) => {
    setIsLoading(true);
    setError(null);
    try {
      const payload = {
        lending_protocol: formData.lendingProtocol,
        target_market: formData.targetMarket,
        collateral_token: formData.collateralToken,
        initial_price: formData.initialPrice,
        min_col_ratio: formData.minColRatio,
        maintenance_margin: formData.maintenanceMargin,
        liq_close_factor: formData.liquidationCloseFactor,
        debt_cap: formData.debtCap,
        funding_period: formData.fundingPeriod,
      };

      const res = await fetch("http://localhost:8080/deploy-market", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key":
            import.meta.env.VITE_API_KEY ||
            "***REDACTED_API_KEY***",
        },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Deployment Failed");
      }

      const result = await res.json();
      console.log("Deployed:", result);
      setIsDeployOpen(false);
      fetchSimulations(); // Refresh list
    } catch (err) {
      setError(err.message);
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <>
      <div className="min-h-screen bg-[#080808] text-[#e0e0e0] font-mono selection:bg-white selection:text-black flex flex-col items-center pt-12">
        <div className="w-full max-w-[1200px] px-6">
          {/* Header Section */}
          <div className="border-b border-white/10 pb-6 mb-12">
            <div className="flex items-center gap-3 text-gray-500 text-xs font-bold tracking-[0.3em] uppercase mb-4">
              <Terminal size={14} />
              Environment: Testnet_Sim
            </div>
            <h1 className="text-4xl font-light tracking-tight text-white mb-2">
              ECONOMIC_SIMULATION
            </h1>
            <p className="text-gray-500 text-sm max-w-2xl leading-relaxed">
              Deploy automated agents to stress-test market parameters against
              historical volatility events.
            </p>
          </div>

          {/* Control Panel Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            {/* Card 1: Market Deployment */}
            <div className="border border-white/10 p-8 bg-white/[0.02] hover:bg-white/[0.04] transition-colors group relative overflow-hidden">
              <div className="absolute top-4 right-4 text-gray-700">
                <Server size={20} />
              </div>

              <h3 className="text-xl text-white font-medium mb-4 tracking-wide">
                MARKET_DEPLOYMENT
              </h3>
              <p className="text-xs text-gray-500 mb-8 h-12">
                Initialize a new RLD market instance on Sepolia with custom risk
                parameters.
              </p>

              <button
                onClick={() => setIsDeployOpen(true)}
                className="w-full h-12 bg-white text-black font-bold text-xs tracking-[0.2em] uppercase hover:bg-cyan-400 transition-colors flex items-center justify-center gap-3"
              >
                <Play size={14} />
                Deploy Market
              </button>
              {error && (
                <div className="mt-4 p-3 bg-red-500/10 border border-red-500/50 text-red-400 text-[10px] break-all">
                  ACTION_FAILED: {error}
                </div>
              )}
            </div>

            {/* Card 2: Active Simulations */}
            <div className="border border-white/10 p-8 bg-white/[0.02] hover:bg-white/[0.04] transition-colors group relative overflow-hidden flex flex-col">
              <div className="absolute top-4 right-4 text-gray-700">
                <Activity size={20} />
              </div>

              <h3 className="text-xl text-white font-medium mb-4 tracking-wide">
                ACTIVE_SIMULATIONS
              </h3>
              <p className="text-xs text-gray-500 mb-8 h-12">
                Live monitoring of deployed market agents and their economic
                status on the local testnet.
              </p>

              <div className="flex-1 w-full min-h-[100px]">
                {simulations.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full text-gray-600 space-y-4">
                    <div className="w-12 h-12 rounded-full bg-white/5 flex items-center justify-center">
                      <Zap size={16} className="text-gray-600" />
                    </div>
                    <div className="text-[10px] tracking-widest uppercase">
                      NO_ACTIVE_SIMULATIONS
                    </div>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between text-[10px] uppercase tracking-widest text-gray-600 border-b border-white/10 pb-2 mb-4">
                      <span>Market</span>
                      <span>State</span>
                    </div>
                    {simulations.map((sim, idx) => (
                      <Link
                        to={`/simulation/${sim.id}`}
                        key={sim.id}
                        className="group/item flex flex-col p-4 bg-black border border-white/10 hover:border-cyan-500/50 transition-all cursor-pointer block"
                      >
                        {/* Header Row */}
                        <div className="flex items-center justify-between mb-3">
                          <div className="flex items-center gap-4">
                            <div className="h-8 w-8 bg-cyan-900/20 text-cyan-500 flex items-center justify-center font-bold text-xs border border-cyan-500/20 group-hover/item:bg-cyan-500 group-hover/item:text-black transition-colors">
                              {idx + 1}
                            </div>
                            <div className="flex flex-col gap-1">
                              <span className="text-xs font-bold text-white tracking-widest uppercase flex items-center gap-2 group-hover/item:text-cyan-400 transition-colors">
                                {sim.target_market}
                                <span className="text-[9px] text-gray-500 font-normal">
                                  {sim.timestamp
                                    ? new Date(
                                        sim.timestamp * 1000,
                                      ).toLocaleTimeString()
                                    : ""}
                                </span>
                              </span>
                              <span className="text-[10px] text-gray-500 font-mono flex items-center gap-2">
                                <span className="w-1 h-1 rounded-full bg-gray-500"></span>
                                {sim.market_id
                                  ? `${sim.market_id.substring(0, 16)}...`
                                  : sim.id?.substring(0, 16)}
                              </span>
                            </div>
                          </div>

                          <div className="flex items-center gap-3">
                            <span className="text-[10px] text-green-400 font-bold uppercase tracking-widest bg-green-900/20 px-2 py-0.5 border border-green-500/20 rounded-sm flex items-center gap-1">
                              <span className="w-1 h-1 rounded-full bg-green-400 animate-pulse"></span>
                              RUNNING
                            </span>
                            <ArrowRight
                              size={14}
                              className="text-gray-600 group-hover/item:text-cyan-500 -translate-x-2 opacity-0 group-hover/item:translate-x-0 group-hover/item:opacity-100 transition-all"
                            />
                          </div>
                        </div>

                        {/* Market State Data */}
                        {sim.state && (
                          <div className="grid grid-cols-3 gap-4 pt-3 border-t border-white/5">
                            {/* Normalization Factor */}
                            <div className="flex flex-col">
                              <span className="text-[9px] text-gray-600 uppercase tracking-wider mb-1">
                                Norm. Factor
                              </span>
                              <span className="text-xs text-white font-mono">
                                {sim.state.normalization_factor_display ||
                                  "1.000000"}
                              </span>
                              <span className="text-[9px] text-cyan-500">
                                {sim.state.accrued_interest_pct || "0.0000%"}{" "}
                                accrued
                              </span>
                            </div>

                            {/* Total Debt */}
                            <div className="flex flex-col">
                              <span className="text-[9px] text-gray-600 uppercase tracking-wider mb-1">
                                Total Debt
                              </span>
                              <span className="text-xs text-white font-mono">
                                {sim.state.total_debt_display || "0.00"}
                              </span>
                              <span className="text-[9px] text-gray-500">
                                units
                              </span>
                            </div>

                            {/* Last Update */}
                            <div className="flex flex-col">
                              <span className="text-[9px] text-gray-600 uppercase tracking-wider mb-1">
                                Last Update
                              </span>
                              <span className="text-[10px] text-white font-mono">
                                {sim.state.last_update || "N/A"}
                              </span>
                              {sim.state.block_number && (
                                <span className="text-[9px] text-gray-500">
                                  Block #{sim.state.block_number}
                                </span>
                              )}
                            </div>
                          </div>
                        )}

                        {/* Risk Parameters */}
                        {sim.risk_params && (
                          <div className="grid grid-cols-4 gap-3 pt-3 mt-2 border-t border-white/5">
                            <div className="flex flex-col">
                              <span className="text-[8px] text-gray-600 uppercase">
                                Min Col
                              </span>
                              <span className="text-[10px] text-yellow-500 font-bold">
                                {sim.risk_params.min_col_ratio_display || "N/A"}
                              </span>
                            </div>
                            <div className="flex flex-col">
                              <span className="text-[8px] text-gray-600 uppercase">
                                Maint.
                              </span>
                              <span className="text-[10px] text-orange-500 font-bold">
                                {sim.risk_params.maintenance_margin_display ||
                                  "N/A"}
                              </span>
                            </div>
                            <div className="flex flex-col">
                              <span className="text-[8px] text-gray-600 uppercase">
                                Liq. Factor
                              </span>
                              <span className="text-[10px] text-red-500 font-bold">
                                {sim.risk_params
                                  .liquidation_close_factor_display || "N/A"}
                              </span>
                            </div>
                            <div className="flex flex-col">
                              <span className="text-[8px] text-gray-600 uppercase">
                                Funding
                              </span>
                              <span className="text-[10px] text-purple-400 font-bold">
                                {sim.risk_params.funding_period_days
                                  ? `${sim.risk_params.funding_period_days}d`
                                  : "N/A"}
                              </span>
                            </div>
                          </div>
                        )}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <DeploymentModal
        isOpen={isDeployOpen}
        onClose={() => setIsDeployOpen(false)}
        onDeploy={handleDeploy}
        isLoading={isLoading}
      />
    </>
  );
};

export default Simulation;
