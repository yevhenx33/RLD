import React, { useState, useEffect, useCallback } from "react";
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
 * Price Chart Component
 */
const PriceChart = ({ chartData }) => {
  if (!chartData || chartData.length < 2) {
    return (
      <div className="h-[200px] flex items-center justify-center text-gray-600">
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
    );
  }

  const indexPrices = chartData.map((d) => d.index_price || 0);
  const markPrices = chartData.map((d) => d.mark_price || d.index_price || 0);
  const allPrices = [...indexPrices, ...markPrices].filter((p) => p > 0);
  const minPrice = Math.min(...allPrices) * 0.999;
  const maxPrice = Math.max(...allPrices) * 1.001;
  const range = maxPrice - minPrice || 0.01;
  const height = 180;
  const width = 500;

  const indexPoints = chartData
    .map((d, i) => {
      const x = (i / (chartData.length - 1)) * width;
      const y = height - ((d.index_price - minPrice) / range) * height;
      return `${x},${y}`;
    })
    .join(" ");

  const markPoints = chartData
    .map((d, i) => {
      const x = (i / (chartData.length - 1)) * width;
      const price = d.mark_price || d.index_price;
      const y = height - ((price - minPrice) / range) * height;
      return `${x},${y}`;
    })
    .join(" ");

  // Get time range
  const firstTime = chartData[0]?.timestamp;
  const lastTime = chartData[chartData.length - 1]?.timestamp;
  const timeRange =
    firstTime && lastTime
      ? `${new Date(firstTime * 1000).toLocaleTimeString()} — ${new Date(lastTime * 1000).toLocaleTimeString()}`
      : "";

  return (
    <div className="p-5">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-6 text-[10px]">
          <div className="flex items-center gap-2">
            <div className="w-4 h-0.5 bg-cyan-400"></div>
            <span className="text-gray-400 uppercase tracking-wider">
              Index Price
            </span>
          </div>
          <div className="flex items-center gap-2">
            <div className="w-4 h-0.5 bg-purple-400"></div>
            <span className="text-gray-400 uppercase tracking-wider">
              Mark Price
            </span>
          </div>
        </div>
        <div className="text-[10px] text-gray-600 font-mono">{timeRange}</div>
      </div>

      <div className="relative">
        {/* Y-axis labels */}
        <div className="absolute left-0 top-0 h-full flex flex-col justify-between text-[9px] text-gray-600 font-mono -ml-12 w-10 text-right">
          <span>${maxPrice.toFixed(2)}</span>
          <span>${((maxPrice + minPrice) / 2).toFixed(2)}</span>
          <span>${minPrice.toFixed(2)}</span>
        </div>

        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-[180px] ml-2"
          preserveAspectRatio="none"
        >
          {/* Grid lines */}
          {[0, 0.25, 0.5, 0.75, 1].map((pct, i) => (
            <line
              key={i}
              x1="0"
              y1={height * pct}
              x2={width}
              y2={height * pct}
              stroke="rgba(255,255,255,0.03)"
              strokeWidth="1"
            />
          ))}

          {/* Mark price area fill */}
          <polygon
            points={`0,${height} ${markPoints} ${width},${height}`}
            fill="url(#markGradient)"
            opacity="0.3"
          />

          {/* Index price area fill */}
          <polygon
            points={`0,${height} ${indexPoints} ${width},${height}`}
            fill="url(#indexGradient)"
            opacity="0.2"
          />

          {/* Gradients */}
          <defs>
            <linearGradient id="indexGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#22d3ee" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#22d3ee" stopOpacity="0" />
            </linearGradient>
            <linearGradient id="markGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#a855f7" stopOpacity="0.3" />
              <stop offset="100%" stopColor="#a855f7" stopOpacity="0" />
            </linearGradient>
          </defs>

          {/* Mark price line */}
          <polyline
            points={markPoints}
            fill="none"
            stroke="#a855f7"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* Index price line */}
          <polyline
            points={indexPoints}
            fill="none"
            stroke="#22d3ee"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>

      <div className="flex justify-between text-[10px] text-gray-600 mt-3 ml-2">
        <span>{chartData.length} data points</span>
        <span>
          Block #{chartData[0]?.block_number?.toLocaleString()} → #
          {chartData[chartData.length - 1]?.block_number?.toLocaleString()}
        </span>
      </div>
    </div>
  );
};

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
    const interval = setInterval(fetchData, 5000);
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

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-mono selection:bg-pink-500/30 selection:text-white">
      <div className="max-w-[1600px] mx-auto px-8 py-12">
        {/* Header */}
        <header className="border-b border-white/10 pb-8 mb-10">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3 text-gray-500 text-[10px] font-bold tracking-[0.3em] uppercase mb-4">
                <Terminal size={14} />
                RLD Protocol // Live Simulation Dashboard
              </div>
              <h1 className="text-5xl font-light tracking-tight text-white mb-3">
                waUSDC Market
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
          <PriceChart chartData={chartData} />
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
          <div>Data refreshes every 5 seconds</div>
        </footer>
      </div>
    </div>
  );
};

export default LiveSimulation;
