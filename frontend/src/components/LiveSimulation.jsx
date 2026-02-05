import React, { useState, useEffect, useCallback, useMemo } from "react";
import {
  Terminal,
  Activity,
  TrendingUp,
  TrendingDown,
  Droplets,
  DollarSign,
  Clock,
  Database,
  RefreshCw,
  Zap,
  BarChart3,
  Box,
  Wifi,
  WifiOff,
  Percent,
  Target,
  Scale,
  ArrowRight,
  ChevronUp,
  ChevronDown,
  AlertCircle,
} from "lucide-react";
import RLDPerformanceChart from "./RLDChart";

// API base URL
const INDEXER_API = import.meta.env.VITE_INDEXER_API || "http://localhost:8080";

/**
 * Format large numbers for human readability
 */
const formatNumber = (num, decimals = 2) => {
  if (num === null || num === undefined || isNaN(num)) return "—";
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(decimals)}M`;
  if (num >= 1_000) return `${(num / 1_000).toFixed(decimals)}K`;
  return num.toFixed(decimals);
};

const formatCurrency = (num, decimals = 4) => {
  if (num === null || num === undefined || isNaN(num)) return "—";
  return `$${num.toFixed(decimals)}`;
};

const formatPercent = (num, decimals = 4) => {
  if (num === null || num === undefined || isNaN(num)) return "—";
  return `${num.toFixed(decimals)}%`;
};

/**
 * Large Hero Stat - Primary metrics
 */
const HeroStat = ({ label, value, subLabel, icon: Icon, color = "cyan" }) => {
  const colorClasses = {
    cyan: "text-cyan-400 border-cyan-500/30 bg-cyan-500/5",
    pink: "text-pink-400 border-pink-500/30 bg-pink-500/5",
    green: "text-green-400 border-green-500/30 bg-green-500/5",
    purple: "text-purple-400 border-purple-500/30 bg-purple-500/5",
  };

  return (
    <div className={`border ${colorClasses[color]} p-6`}>
      <div className="flex items-center gap-2 text-gray-500 text-[10px] uppercase tracking-[0.2em] mb-3">
        {Icon && (
          <Icon size={12} className={colorClasses[color].split(" ")[0]} />
        )}
        {label}
      </div>
      <div className="text-3xl font-light text-white font-mono tracking-tight mb-1">
        {value}
      </div>
      {subLabel && <div className="text-xs text-gray-500">{subLabel}</div>}
    </div>
  );
};

/**
 * Metric Row - Detail list item
 */
const MetricRow = ({
  label,
  value,
  description,
  icon: Icon,
  highlight = false,
}) => (
  <div
    className={`flex items-center justify-between py-3 px-4 border-b border-white/5 last:border-0 transition-colors ${highlight ? "bg-cyan-500/5" : "hover:bg-white/[0.02]"}`}
  >
    <div className="flex items-center gap-3">
      {Icon && (
        <Icon
          size={14}
          className={highlight ? "text-cyan-400" : "text-gray-600"}
        />
      )}
      <div>
        <div className="text-xs text-gray-300 uppercase tracking-wider">
          {label}
        </div>
        {description && (
          <div className="text-[10px] text-gray-600 mt-0.5">{description}</div>
        )}
      </div>
    </div>
    <div
      className={`font-mono text-sm ${highlight ? "text-cyan-400" : "text-white"}`}
    >
      {value}
    </div>
  </div>
);

/**
 * Price Comparison Card
 */
const PriceCard = ({
  title,
  price,
  subtitle,
  color = "cyan",
  isIndex = false,
}) => {
  const colors = {
    cyan: {
      border: "border-cyan-500/20",
      text: "text-cyan-400",
      bg: "bg-cyan-500/5",
    },
    purple: {
      border: "border-purple-500/20",
      text: "text-purple-400",
      bg: "bg-purple-500/5",
    },
  };
  const c = colors[color];

  return (
    <div className={`border ${c.border} ${c.bg} p-5`}>
      <div className="flex items-center justify-between mb-3">
        <div className="text-[10px] text-gray-500 uppercase tracking-[0.2em]">
          {title}
        </div>
        {isIndex && (
          <div className="text-[9px] bg-cyan-900/30 text-cyan-400 px-2 py-0.5 uppercase tracking-wider">
            Oracle
          </div>
        )}
      </div>
      <div
        className={`text-4xl font-light font-mono ${c.text} tracking-tight mb-2`}
      >
        {price}
      </div>
      <div className="text-xs text-gray-500">{subtitle}</div>
    </div>
  );
};

/**
 * Funding Rate Display
 */
const FundingDisplay = ({ indexPrice, markPrice }) => {
  if (!indexPrice || !markPrice) return null;

  const spread = ((markPrice - indexPrice) / indexPrice) * 100;
  const isPositive = spread >= 0;

  return (
    <div className="border border-white/10 bg-[#080808] p-5">
      <div className="text-[10px] text-gray-500 uppercase tracking-[0.2em] mb-3 flex items-center gap-2">
        <Scale size={12} className="text-yellow-500" />
        Price Spread (Funding Direction)
      </div>

      <div className="flex items-center gap-4">
        <div
          className={`text-2xl font-mono ${isPositive ? "text-green-400" : "text-red-400"}`}
        >
          {isPositive ? "+" : ""}
          {spread.toFixed(4)}%
        </div>
        <div className="flex-1 h-2 bg-gray-800 rounded-full overflow-hidden">
          <div
            className={`h-full transition-all duration-500 ${isPositive ? "bg-green-500" : "bg-red-500"}`}
            style={{ width: `${Math.min(Math.abs(spread) * 10, 100)}%` }}
          />
        </div>
        <div className="flex items-center gap-1 text-xs text-gray-500">
          {isPositive ? (
            <>
              <ChevronUp size={14} className="text-green-400" />
              <span>Longs Pay</span>
            </>
          ) : (
            <>
              <ChevronDown size={14} className="text-red-400" />
              <span>Shorts Pay</span>
            </>
          )}
        </div>
      </div>

      <div className="mt-4 text-[10px] text-gray-600">
        When mark {">"} index, long positions pay funding to short positions
        (and vice versa).
      </div>
    </div>
  );
};

/**
 * Price Chart Component - REMOVED: Now using RLDPerformanceChart
 */

/**
 * Pool Metrics Panel - Human Readable
 */
const PoolMetrics = ({ poolState }) => {
  if (!poolState || !poolState.liquidity) return null;

  // Convert tick to a human-readable format - with null check
  const tickDescription =
    poolState.tick !== undefined && poolState.tick !== null
      ? poolState.tick < 0
        ? `${Math.abs(poolState.tick).toLocaleString()} below parity`
        : `${poolState.tick.toLocaleString()} above parity`
      : "Loading...";

  return (
    <div className="border border-white/10 bg-[#080808]">
      <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center">
        <h3 className="text-xs font-bold tracking-[0.2em] text-white uppercase flex items-center gap-2">
          <Droplets size={14} className="text-purple-500" />
          Pool Liquidity
        </h3>
        <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_#22c55e]" />
      </div>

      <div className="divide-y divide-white/5">
        <MetricRow
          label="Active Liquidity"
          value={formatNumber(Number(poolState.liquidity), 2)}
          description="Total liquidity in active tick range"
          icon={Droplets}
          highlight
        />
        <MetricRow
          label="Current Tick"
          value={tickDescription}
          description="Price position within the pool"
          icon={Target}
        />
        <MetricRow
          label="Pool Status"
          value="Active"
          description="V4 pool with TWAMM hook"
          icon={Activity}
        />
      </div>
    </div>
  );
};

/**
 * Market Details Panel
 */
const MarketDetails = ({ marketState, lastRefresh }) => {
  if (!marketState) return null;

  const nfDecimal = marketState.normalization_factor
    ? parseFloat(marketState.normalization_factor) / 1e18
    : 1;
  const accruedInterest = (1 - nfDecimal) * 100;
  const totalDebt = marketState.total_debt
    ? parseFloat(marketState.total_debt) / 1e6
    : 0;

  const lastUpdate = marketState.last_update_timestamp
    ? new Date(marketState.last_update_timestamp * 1000).toLocaleString()
    : "—";

  return (
    <div className="border border-white/10 bg-[#080808]">
      <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center">
        <h3 className="text-xs font-bold tracking-[0.2em] text-white uppercase flex items-center gap-2">
          <Database size={14} className="text-cyan-500" />
          Market State
        </h3>
        <div className="text-[10px] text-gray-600">
          Block #{marketState.block_number?.toLocaleString()}
        </div>
      </div>

      <div className="divide-y divide-white/5">
        <MetricRow
          label="Accrued Interest"
          value={formatPercent(accruedInterest, 6)}
          description="Funding accumulated since market creation"
          icon={Percent}
          highlight={accruedInterest > 0.001}
        />
        <MetricRow
          label="Total Synthetic Debt"
          value={`${formatNumber(totalDebt)} wRLP`}
          description="Outstanding minted synthetic tokens"
          icon={DollarSign}
        />
        <MetricRow
          label="Last Funding Update"
          value={lastUpdate}
          description="When normalization factor was last applied"
          icon={Clock}
        />
        <MetricRow
          label="Market Status"
          value="Active"
          description="Accepting positions and trades"
          icon={Zap}
        />
      </div>
    </div>
  );
};

/**
 * Main Dashboard Component
 */
const LiveSimulation = () => {
  const [status, setStatus] = useState(null);
  const [latest, setLatest] = useState(null);
  const [chartData, setChartData] = useState([]);
  const [isConnected, setIsConnected] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [error, setError] = useState(null);

  const fetchData = useCallback(async () => {
    try {
      const [statusRes, latestRes, chartRes] = await Promise.all([
        fetch(`${INDEXER_API}/api/status`),
        fetch(`${INDEXER_API}/api/latest`),
        fetch(`${INDEXER_API}/api/chart/price?limit=100`),
      ]);

      if (statusRes.ok) {
        setStatus(await statusRes.json());
        setIsConnected(true);
      }

      if (latestRes.ok) {
        const data = await latestRes.json();
        setLatest(data);
        setLastUpdate(new Date());
      }

      if (chartRes.ok) {
        const data = await chartRes.json();
        setChartData(data.data?.reverse() || []);
      }

      setError(null);
    } catch (err) {
      setIsConnected(false);
      setError("Cannot connect to indexer");
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 12000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // Extract data
  const marketState = latest?.market_states?.[0] || {};
  const poolState = latest?.pool_states?.[0] || {};

  // Calculate human-readable values
  const indexPrice = marketState.index_price
    ? parseFloat(marketState.index_price) / 1e18
    : null;
  const markPrice = poolState.mark_price || null;
  const totalDebt = marketState.total_debt
    ? parseFloat(marketState.total_debt) / 1e6
    : 0;
  const nfDecimal = marketState.normalization_factor
    ? parseFloat(marketState.normalization_factor) / 1e18
    : 1;
  const accruedInterest = (1 - nfDecimal) * 100;

  // Transform chart data for RLDPerformanceChart
  // Note: /api/chart/price already returns formatted values (not raw wei)
  // Filter out corrupted data where mark_price is obviously wrong (> $1000)
  const transformedChartData = useMemo(() => {
    if (!chartData || chartData.length === 0) return [];
    return chartData
      .filter((d) => d.index_price && d.index_price > 0)
      .map((d) => {
        // Validate mark_price - if it's absurdly high (> 1000), use index_price as fallback
        const validMarkPrice =
          d.mark_price && d.mark_price < 1000 ? d.mark_price : d.index_price;
        return {
          timestamp: d.timestamp,
          indexPrice: d.index_price,
          markPrice: validMarkPrice || d.index_price || 0,
        };
      });
  }, [chartData]);

  // Chart areas configuration
  const chartAreas = useMemo(
    () => [
      { key: "indexPrice", color: "#22d3ee", name: "Index Price" },
      { key: "markPrice", color: "#a855f7", name: "Mark Price" },
    ],
    [],
  );

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-pink-500/30 selection:text-white">
      <div className="max-w-[1600px] mx-auto px-8 py-12">
        {/* Header */}
        <header className="border-b border-white/10 pb-8 mb-10">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3 text-gray-500 text-[10px] font-bold tracking-[0.3em] uppercase mb-4">
                <Terminal size={14} />
                RLD Protocol // STATUS
              </div>
              <h1 className="text-5xl font-light tracking-tight text-white mb-3">
                waUSDC Market Status
              </h1>
              <p className="text-gray-500 text-sm max-w-xl leading-relaxed">
                Real-time monitoring of the RLD synthetic position market. Track
                index vs mark price convergence, funding accumulation, and pool
                liquidity.
              </p>
            </div>

            {/* Connection Status */}
            <div className="text-right">
              <div
                className={`flex items-center gap-2 justify-end mb-3 ${isConnected ? "text-green-400" : "text-red-400"}`}
              >
                {isConnected ? <Wifi size={16} /> : <WifiOff size={16} />}
                <span className="text-xs uppercase tracking-widest font-bold">
                  {isConnected ? "Live" : "Offline"}
                </span>
                {isConnected && (
                  <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse shadow-[0_0_8px_#22c55e]" />
                )}
              </div>
              {lastUpdate && (
                <button
                  onClick={fetchData}
                  className="flex items-center gap-2 text-[11px] text-gray-500 hover:text-cyan-400 transition-colors ml-auto"
                >
                  <RefreshCw size={12} />
                  Updated {lastUpdate.toLocaleTimeString()}
                </button>
              )}
              {status && (
                <div className="text-[11px] text-gray-600 mt-2">
                  {status.total_block_states?.toLocaleString()} blocks indexed
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Error State */}
        {error && !isConnected && (
          <div className="mb-10 p-6 border border-red-500/30 bg-red-500/5 flex items-center gap-5">
            <AlertCircle size={28} className="text-red-500" />
            <div>
              <div className="text-red-400 font-bold uppercase tracking-widest mb-1">
                Connection Error
              </div>
              <div className="text-gray-500 text-sm">{error}</div>
              <div className="text-gray-600 text-xs mt-2">
                Start the indexer:{" "}
                <code className="text-gray-400 bg-gray-900 px-2 py-0.5">
                  uvicorn indexer_api:app --port 8080
                </code>
              </div>
            </div>
          </div>
        )}

        {/* Price Section */}
        <section className="mb-10">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
            <PriceCard
              title="Index Price"
              price={formatCurrency(indexPrice)}
              subtitle="Derived from Aave lending rate (K = 100 × rate)"
              color="cyan"
              isIndex
            />
            <PriceCard
              title="Mark Price"
              price={formatCurrency(markPrice)}
              subtitle="Current spot price from Uniswap V4 pool"
              color="purple"
            />
          </div>

          <FundingDisplay indexPrice={indexPrice} markPrice={markPrice} />
        </section>

        {/* Key Metrics */}
        <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-5 mb-10">
          <HeroStat
            label="Total Value Locked"
            value={`$${formatNumber(totalDebt * (indexPrice || 0))}`}
            subLabel="Synthetic debt × index price"
            icon={DollarSign}
            color="green"
          />
          <HeroStat
            label="Outstanding Debt"
            value={`${formatNumber(totalDebt)} wRLP`}
            subLabel="Total minted synthetic tokens"
            icon={Database}
            color="cyan"
          />
          <HeroStat
            label="Funding Accrued"
            value={formatPercent(accruedInterest, 6)}
            subLabel="Since market inception"
            icon={Percent}
            color="pink"
          />
          <HeroStat
            label="Pool Liquidity"
            value={formatNumber(Number(poolState.liquidity || 0))}
            subLabel="Active in current tick range"
            icon={Droplets}
            color="purple"
          />
        </section>

        {/* Chart */}
        <section className="border border-white/10 bg-[#080808] mb-10">
          <div className="p-4 border-b border-white/10 bg-[#0a0a0a] flex justify-between items-center">
            <h3 className="text-xs font-bold tracking-[0.2em] text-white uppercase flex items-center gap-2">
              <BarChart3 size={14} className="text-cyan-500" />
              Price History
            </h3>
            <div className="text-[10px] text-gray-500">
              Index vs Mark price convergence
            </div>
          </div>
          <div className="h-[300px] p-4">
            {transformedChartData.length > 1 ? (
              <RLDPerformanceChart
                data={transformedChartData}
                areas={chartAreas}
                resolution="RAW"
              />
            ) : (
              <div className="h-full flex items-center justify-center text-gray-600">
                <div className="text-center">
                  <BarChart3 size={32} className="mx-auto mb-3 opacity-30" />
                  <div className="text-[11px] uppercase tracking-widest">
                    Collecting price history...
                  </div>
                  <div className="text-[10px] text-gray-700 mt-1">
                    Data appears after multiple blocks
                  </div>
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Details Grid */}
        <section className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <MarketDetails marketState={marketState} lastRefresh={lastUpdate} />
          <PoolMetrics poolState={poolState} />
        </section>

        {/* Footer */}
        <footer className="mt-12 pt-6 border-t border-white/5 flex justify-between text-[10px] text-gray-600">
          <div className="flex items-center gap-2">
            <Box size={12} />
            Latest: Block #{marketState.block_number?.toLocaleString() || "—"}
          </div>
          <div>Data refreshes every 12 seconds</div>
        </footer>
      </div>
    </div>
  );
};

export default LiveSimulation;
