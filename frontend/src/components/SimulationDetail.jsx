import React, { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import {
  ArrowLeft,
  Terminal,
  Activity,
  Shield,
  Coins,
  Layers,
  Zap,
  CheckCircle,
  Clock,
  Box,
  TrendingUp,
  Percent,
  DollarSign,
  RefreshCw,
  User,
  Database,
  GitBranch,
} from "lucide-react";

/**
 * Display a single parameter row
 */
const ParamRow = ({ label, value, unit, icon: Icon, highlight }) => (
  <div
    className={`flex items-center justify-between py-3 border-b border-white/5 last:border-0 hover:bg-white/[0.02] px-2 transition-colors ${highlight ? "bg-cyan-500/5" : ""}`}
  >
    <div className="flex items-center gap-3 text-gray-500">
      {Icon && (
        <Icon
          size={12}
          className={highlight ? "text-cyan-400" : "text-cyan-500/50"}
        />
      )}
      <span className="text-xs uppercase tracking-widest">{label}</span>
    </div>
    <div className="flex items-center gap-1 font-mono text-sm text-white">
      <span className={highlight ? "text-cyan-400" : ""}>{value || "—"}</span>
      {unit && <span className="text-gray-600 text-[10px] ml-1">{unit}</span>}
    </div>
  </div>
);

/**
 * Address display with copy functionality
 */
const AddressRow = ({ label, value, icon: Icon }) => {
  const truncated = value
    ? `${value.substring(0, 10)}...${value.substring(38)}`
    : "—";

  return (
    <div className="flex items-center justify-between py-3 border-b border-white/5 last:border-0 hover:bg-white/[0.02] px-2 transition-colors">
      <div className="flex items-center gap-3 text-gray-500">
        {Icon && <Icon size={12} className="text-cyan-500/50" />}
        <span className="text-xs uppercase tracking-widest">{label}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-gray-400">{truncated}</span>
        {value && (
          <button
            onClick={() => navigator.clipboard.writeText(value)}
            className="text-gray-600 hover:text-cyan-400 transition-colors"
            title="Copy address"
          >
            <Database size={10} />
          </button>
        )}
      </div>
    </div>
  );
};

/**
 * Large stat card for prominent metrics
 */
const StatCard = ({ label, value, subvalue, icon: Icon, color = "cyan" }) => {
  const colorClasses = {
    cyan: "border-cyan-500/20 bg-cyan-500/5",
    green: "border-green-500/20 bg-green-500/5",
    yellow: "border-yellow-500/20 bg-yellow-500/5",
    purple: "border-purple-500/20 bg-purple-500/5",
  };

  return (
    <div className={`border ${colorClasses[color]} p-4`}>
      <div className="flex items-center gap-2 text-gray-500 text-[10px] uppercase tracking-widest mb-2">
        {Icon && <Icon size={12} className={`text-${color}-500`} />}
        {label}
      </div>
      <div className="text-2xl font-light text-white font-mono">{value}</div>
      {subvalue && (
        <div className={`text-xs text-${color}-500 mt-1`}>{subvalue}</div>
      )}
    </div>
  );
};

/**
 * Detailed Block for a Market
 */
const SimulationDetail = () => {
  const { id } = useParams(); // Market ID or Transaction Hash
  const [data, setData] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);

  useEffect(() => {
    fetchDetail();
    // Auto-refresh every 30 seconds
    const interval = setInterval(fetchDetail, 30000);
    return () => clearInterval(interval);
  }, [id]);

  const fetchDetail = async () => {
    try {
      // Try enriched endpoint first (uses market_state.db)
      const enrichedRes = await fetch(
        `http://localhost:8080/simulations/enriched/${id}`,
      );
      if (enrichedRes.ok) {
        const result = await enrichedRes.json();
        setData(result);
        setLastRefresh(new Date());
        setError(null);
        return;
      }

      // Fallback to original endpoint (decodes tx)
      const res = await fetch(`http://localhost:8080/simulation/${id}`);
      if (!res.ok) {
        throw new Error("Simulation data not found.");
      }
      const result = await res.json();
      setData(result);
      setLastRefresh(new Date());
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  if (isLoading) {
    return (
      <div className="h-screen w-full flex flex-col items-center justify-center bg-[#080808] text-cyan-500 space-y-4">
        <Activity size={32} className="animate-spin" />
        <div className="text-xs font-mono tracking-widest animate-pulse">
          FETCHING_MARKET_DATA...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-screen w-full flex flex-col items-center justify-center bg-[#080808]">
        <div className="max-w-md p-8 border border-red-900/50 bg-red-900/10 text-center space-y-4">
          <Shield size={48} className="mx-auto text-red-500" />
          <h2 className="text-red-400 font-mono tracking-widest uppercase">
            Error Loading Market
          </h2>
          <p className="text-gray-500 text-xs font-mono">{error}</p>
          <Link
            to="/simulation"
            className="inline-flex items-center gap-2 text-white text-xs border border-white/20 px-4 py-2 hover:bg-white/10 uppercase tracking-widest"
          >
            <ArrowLeft size={12} /> Return to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  // Extract state and risk params
  const state = data.state || {};
  const riskParams = data.risk_params || {};

  return (
    <div className="min-h-screen bg-[#080808] text-[#e0e0e0] font-mono pt-12 pb-20">
      <div className="w-full max-w-[1200px] mx-auto px-6">
        {/* Header / Nav */}
        <div className="mb-8">
          <Link
            to="/simulation"
            className="inline-flex items-center gap-2 text-gray-500 hover:text-white text-xs uppercase tracking-widest transition-colors mb-6 group"
          >
            <ArrowLeft
              size={12}
              className="group-hover:-translate-x-1 transition-transform"
            />
            Back_To_Simulations
          </Link>

          <div className="flex items-start justify-between border-b border-white/10 pb-6">
            <div>
              <div className="flex items-center gap-3 text-cyan-500 text-xs font-bold tracking-[0.3em] uppercase mb-2">
                <Terminal size={14} />
                Market_Overview
              </div>
              <h1 className="text-4xl font-light text-white tracking-tight">
                {data.positionTokenName ||
                  data.positionTokenSymbol ||
                  "Unknown Market"}
              </h1>
              <div className="flex items-center gap-4 mt-3 text-xs text-gray-500 flex-wrap">
                {data.block_number && (
                  <span className="flex items-center gap-1">
                    <Box size={10} />
                    BLOCK: {data.block_number}
                  </span>
                )}
                {data.market_id && (
                  <>
                    <span className="text-gray-700">|</span>
                    <span className="font-mono text-gray-600 truncate max-w-[400px]">
                      ID: {data.market_id}
                    </span>
                  </>
                )}
              </div>
            </div>

            <div className="flex flex-col items-end gap-2">
              <div className="bg-green-900/20 border border-green-500/20 text-green-400 px-3 py-1 text-xs font-bold tracking-widest uppercase flex items-center gap-2 rounded-sm">
                <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                Active_On_Chain
              </div>
              {lastRefresh && (
                <button
                  onClick={fetchDetail}
                  className="flex items-center gap-1 text-[10px] text-gray-600 hover:text-cyan-400 transition-colors"
                >
                  <RefreshCw size={10} />
                  Last: {lastRefresh.toLocaleTimeString()}
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Live State Stats - Top Row */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <StatCard
            label="Normalization Factor"
            value={state.normalization_factor_display || "1.000000"}
            subvalue={state.accrued_interest_pct || "0.0000% accrued"}
            icon={TrendingUp}
            color="cyan"
          />
          <StatCard
            label="Total Debt"
            value={state.total_debt_display || "0.00"}
            subvalue="synthetic units"
            icon={DollarSign}
            color="green"
          />
          <StatCard
            label="Min Collateral Ratio"
            value={`${riskParams.display_minColRatio || 0}%`}
            subvalue="required collateral"
            icon={Shield}
            color="yellow"
          />
          <StatCard
            label="Funding Period"
            value={
              riskParams.fundingPeriodDays
                ? `${riskParams.fundingPeriodDays}d`
                : "—"
            }
            subvalue={`${riskParams.fundingPeriod || 0} seconds`}
            icon={Clock}
            color="purple"
          />
        </div>

        {/* Price Feed Section */}
        {data.prices && (
          <div className="mb-8 p-6 border border-white/10 bg-gradient-to-r from-cyan-900/10 to-purple-900/10">
            <div className="flex items-center gap-2 text-xs text-gray-500 uppercase tracking-widest mb-4">
              <Activity size={12} className="text-cyan-400" />
              Live Oracle Prices
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Index Price */}
              <div className="flex items-center justify-between p-4 bg-black/30 border border-cyan-500/20">
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
                    Index Price
                  </div>
                  <div className="text-xs text-gray-400">
                    Rate Oracle (K=100 × Aave Rate)
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-3xl font-light text-cyan-400 font-mono">
                    {data.prices.index_price_display}
                  </div>
                  <div className="text-[10px] text-gray-600">per RLD paper</div>
                </div>
              </div>

              {/* Mark Price */}
              <div className="flex items-center justify-between p-4 bg-black/30 border border-purple-500/20">
                <div>
                  <div className="text-[10px] text-gray-500 uppercase tracking-widest mb-1">
                    Mark Price
                  </div>
                  <div className="text-xs text-gray-400">
                    From Spot Oracle (Pool TWAP)
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-3xl font-light text-purple-400 font-mono">
                    {data.prices.mark_price_display}
                  </div>
                  <div className="text-[10px] text-gray-600">pool TWAP</div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Grid Layout */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {/* Panel 1: Live Market State */}
          <div className="border border-white/10 p-6 bg-white/[0.02]">
            <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-4">
              <h3 className="text-sm font-bold text-white uppercase tracking-widest flex items-center gap-2">
                <Activity size={14} className="text-cyan-500" />
                Live_Market_State
              </h3>
              <span className="text-[10px] text-gray-600">
                {state.last_update || "N/A"}
              </span>
            </div>

            <div className="space-y-1">
              <ParamRow
                label="Normalization Factor"
                value={state.normalization_factor_display}
                icon={TrendingUp}
                highlight
              />
              <ParamRow
                label="Accrued Interest"
                value={state.accrued_interest_pct}
                icon={Percent}
              />
              <ParamRow
                label="Total Debt"
                value={state.total_debt_display}
                unit="units"
                icon={DollarSign}
              />
              <ParamRow
                label="Last Update Block"
                value={state.block_number}
                icon={Box}
              />
            </div>
          </div>

          {/* Panel 2: Risk Parameters */}
          <div className="border border-white/10 p-6 bg-white/[0.02]">
            <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-4">
              <h3 className="text-sm font-bold text-white uppercase tracking-widest flex items-center gap-2">
                <Shield size={14} className="text-pink-500" />
                Risk_Configuration
              </h3>
            </div>

            <div className="space-y-1">
              <ParamRow
                label="Min Col Ratio"
                value={riskParams.display_minColRatio}
                unit="%"
                icon={Shield}
              />
              <ParamRow
                label="Maintenance Margin"
                value={riskParams.display_maintenanceMargin}
                unit="%"
                icon={Activity}
              />
              <ParamRow
                label="Liquidation Close Factor"
                value={riskParams.display_liquidationCloseFactor}
                unit="%"
                icon={Zap}
              />
              <ParamRow
                label="Funding Period"
                value={
                  riskParams.fundingPeriodDays
                    ? `${riskParams.fundingPeriodDays} days`
                    : "—"
                }
                icon={Clock}
              />
              <ParamRow
                label="Debt Cap"
                value={riskParams.debtCap || "Unlimited"}
                icon={Database}
              />
            </div>
          </div>

          {/* Panel 3: Asset Configuration */}
          <div className="border border-white/10 p-6 bg-white/[0.02]">
            <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-4">
              <h3 className="text-sm font-bold text-white uppercase tracking-widest flex items-center gap-2">
                <Layers size={14} className="text-green-500" />
                Asset_Parameters
              </h3>
            </div>

            <div className="space-y-1">
              <ParamRow
                label="Position Token"
                value={data.positionTokenSymbol}
                icon={Coins}
              />
              <AddressRow
                label="Position Token Address"
                value={data.positionToken}
                icon={Database}
              />
              <AddressRow
                label="Collateral Token"
                value={data.collateralToken}
                icon={Coins}
              />
              <AddressRow
                label="Underlying Token"
                value={data.underlyingToken}
                icon={Layers}
              />
              <AddressRow
                label="Underlying Pool"
                value={data.underlyingPool}
                icon={GitBranch}
              />
            </div>
          </div>

          {/* Panel 4: System Modules */}
          <div className="border border-white/10 p-6 bg-white/[0.02]">
            <div className="flex items-center justify-between border-b border-white/10 pb-4 mb-4">
              <h3 className="text-sm font-bold text-white uppercase tracking-widest flex items-center gap-2">
                <Terminal size={14} className="text-purple-500" />
                System_Modules
              </h3>
            </div>

            <div className="space-y-1">
              <AddressRow label="Curator" value={data.curator} icon={User} />
              <AddressRow
                label="Spot Oracle"
                value={data.spotOracle}
                icon={TrendingUp}
              />
              <AddressRow
                label="Rate Oracle"
                value={data.rateOracle}
                icon={Percent}
              />
              <AddressRow
                label="Liquidation Module"
                value={data.liquidationModule}
                icon={Zap}
              />
              <AddressRow
                label="Broker Verifier"
                value={riskParams.brokerVerifier}
                icon={Shield}
              />
            </div>
          </div>
        </div>

        {/* Raw Market ID */}
        {data.market_id && (
          <div className="mt-8 p-4 border border-white/5 bg-white/[0.01]">
            <div className="text-[10px] text-gray-600 uppercase tracking-widest mb-2">
              Full Market ID
            </div>
            <div className="font-mono text-xs text-gray-400 break-all">
              {data.market_id}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default SimulationDetail;
